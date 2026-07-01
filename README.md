# provenance-guard-practice

A small Flask service that scores submitted text for likely AI authorship using
**two independent signals** (token predictability + structural burstiness),
combines them into a **calibrated confidence label**, writes a **structured audit
entry** for every decision, and lets creators **appeal** a classification. The
target content type is **travel blog posts** — personal narrative, destination
write-ups, and trip reports (see [planning.md](planning.md)).

## Implemented features

| Feature | Status | Where |
| --- | --- | --- |
| Content submission endpoint | ✅ | `POST /submit` — [app.py](app.py) |
| Multi-signal detection pipeline (2 signals) | ✅ | [signals.py](signals.py) |
| Confidence scoring with uncertainty | ✅ | `combine_signals` — [signals.py](signals.py) |
| Transparency label (3 variants) | ✅ | `LABELS` / label generator — [signals.py](signals.py) |
| Appeals workflow | ✅ | `POST /appeal` — [app.py](app.py), [audit_log.py](audit_log.py) |
| Rate limiting | ✅ | Flask-Limiter — [app.py](app.py) |
| Structured audit log | ✅ | SQLite — [audit_log.py](audit_log.py), `GET /log` |

No stretch features (ensemble ≥3 signals, provenance certificate, analytics
dashboard, multi-modal) are implemented — the scope is the seven required
features above. The pipeline is intentionally structured so a third signal could
be added to `combine_signals` without touching the endpoint.

## Endpoints

- `POST /submit` — submit `{ "text": ..., "creator_id": ... }`, get back an
  attribution result, confidence score, transparency label, and a per-signal
  breakdown. **Rate limited** (see below).
- `POST /appeal` — submit `{ "content_id": ..., "submitter_id": ..., "reason": ... }`
  to contest a classification. Logs the appeal, flips status to `under review`.
- `GET /log` — most recent audit-log entries **and** appeals (unauthenticated, for
  grading visibility only).

## Running

```bash
.venv/bin/python app.py   # serves on http://localhost:5001
```

Set `GROQ_API_KEY` in `.env` to use the LLM-backed perplexity signal. Without it,
the service transparently falls back to a local heuristic (see Signal 1 below).

### Tests

```bash
.venv/bin/python -m unittest discover -s tests -q   # 23 tests
```

Tests cover each signal in isolation, the confidence scorer's thresholds and
disagreement handling, all three label variants, the audit-log contract, and the
appeals workflow.

---

## Multi-signal detection pipeline

Two **independent** signals classify each text; neither alone can produce a
confident "AI" verdict. They were chosen because they measure *orthogonal*
properties — one looks at **word choice**, the other at **structure** — so
pairing them adds real information rather than double-counting one cue.

### Signal 1 — Token-level predictability (perplexity)

- **What it captures:** how "surprised" a language model is by the text's word
  choices. LLMs generate by repeatedly picking high-probability tokens, so their
  output sits in a low-perplexity valley (the "most likely" phrasing of each
  thought). Humans make locally surprising choices — odd words, tangents, asides,
  jokes — that read as less predictable.
- **How it's computed:** a Groq-hosted small model (`llama-3.1-8b-instant`,
  `temperature=0`) returns a categorical verdict + rationale. Small models
  *classify* well but emit poorly-calibrated raw numbers (they cluster around
  0.7–0.8 regardless of content), so we trust the category and **reconcile** the
  number into the band its verdict implies (`_reconcile_perplexity_score`). If
  Groq is unavailable, `compute_perplexity_signal` provides a local
  repetition/structure heuristic fallback so the service never hard-fails.
- **Why chosen:** it is the single most direct measure of the thing we care about
  (machine-likely phrasing) and is largely independent of sentence structure.

### Signal 2 — Burstiness (structural variance)

- **What it captures:** the rhythm of the prose. Humans write in bursts — a long
  explanatory sentence, then a short punch, in uneven paragraph blocks. LLMs
  default to steady, uniform, medium-length sentences and even paragraphs.
- **How it's computed:** three stylometric metrics folded into one 0–1 score —
  sentence-length coefficient of variation (primary, 50%), paragraph-length
  uniformity (25%), and sentence-length spread `(max-min)/mean` (25%). Below ~8
  sentences the signal carries little information, so short texts are **damped
  toward the neutral midpoint (0.5)** rather than allowed to look confident.
- **Why chosen:** it is nearly independent of perplexity (structure vs. word
  choice), which is exactly why combining the two is more than the sum of parts.

Both signals return `0.0` (very human-like) → `1.0` (very AI-like).

---

## Confidence scoring with uncertainty

The system returns a **calibrated score in [0, 1]**, not a binary label. The
score, not just the category, drives the transparency label a reader sees.

### How the score is built (`combine_signals`)

1. **Conservative weighted average** — `0.6 × perplexity + 0.4 × burstiness`.
   Perplexity is the primary signal; burstiness corroborates.
2. **Disagreement penalty** — the more the two signals conflict, the harder the
   score is pulled toward the neutral midpoint (0.5). Two signals that strongly
   disagree should *not* produce a confident verdict in either direction.
3. **AI-corroboration floor** — a confident "AI" verdict (`score ≥ 0.80`)
   requires **both** signals to independently lean AI (each ≥ 0.60). If only one
   does, the score is parked just below the AI threshold. This deliberately
   biases the system against false "AI" accusations, since wrongly flagging a
   human is the costlier error.

**Thresholds:** `score ≥ 0.80` → likely AI · `0.25 ≤ score < 0.80` → uncertain ·
`score < 0.25` → likely human.

### How I tested that the scores are meaningful

I pinned a small calibration set of representative `(perplexity, burstiness)`
pairs — the reconciled signal values observed on real travel texts — as unit
tests (`CalibrationTests` in [tests/test_app.py](tests/test_app.py)). These
tests assert that (a) all three labels are reachable, (b) a clearly-AI text and a
clearly-human text land **more than 0.5 apart** rather than clustering, and (c) a
lone strong signal on a short text stays `uncertain`. Because the tests run on
fixed signal pairs, they verify the *scorer's* behavior deterministically without
a network call. The variation below is what those tests lock in.

### Example submissions (actual scores from testing)

| Case | perplexity | burstiness | **combined score** | result |
| --- | --- | --- | --- | --- |
| **High-confidence** — long, uniform AI destination copy | 0.95 | 0.98 | **0.955** | `likely_ai` |
| **Lower-confidence** — strong perplexity, but short so burstiness abstains | 0.95 | 0.54 | **0.727** | `uncertain` |
| Clearly human — long bursty first-person narrative | 0.00 | 0.00 | 0.045 | `likely_human` |

The first two cases share an identical, strongly-AI perplexity signal (0.95) yet
land **0.23 apart** (0.955 vs 0.727) and in **different bands**: in the second
case the burstiness signal abstains (short text → damped to ~0.54), the two
signals disagree, and the corroboration floor keeps the result out of the AI
band. That is the system producing genuine uncertainty rather than a constant —
a 0.955 and a 0.727 mean meaningfully different things to a reader.

---

## Transparency label

The reader sees calibrated plain language, **never the raw number**. There are
exactly three variants, selected by the score band. Each is defined once in
`LABELS` ([signals.py](signals.py)) and returned verbatim in the `/submit`
response as `label_text`.

| Variant | Trigger (score band) | Exact text displayed |
| --- | --- | --- |
| **High-confidence AI** | `score ≥ 0.80` | `Likely AI-generated` |
| **High-confidence human** | `score < 0.25` | `Likely human-written` |
| **Uncertain** | `0.25 ≤ score < 0.80` | `Uncertain origin — this text may be AI-generated or human-written` |

The uncertain label is deliberately worded to name *both* possibilities in plain
language, so a non-technical reader understands the system is declining to make a
confident call rather than hedging a real verdict. The label is generated from
the score, not hard-coded per response — `test_submit_label_is_derived_from_score_not_hardcoded`
proves this end-to-end.

---

## Appeals workflow

A creator who believes a classification is wrong can contest it via
`POST /appeal`:

```bash
curl -X POST http://localhost:5001/appeal \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "ec83002d-7df7-4534-b476-f501a55e9f70",
    "submitter_id": "agency-2",
    "reason": "This is a human-written destination guide; the uniform structure is house style, not AI."
  }'
```

What happens (`log_appeal` in [audit_log.py](audit_log.py)):

1. The original decision is looked up by `content_id`. Unknown IDs return `404`;
   missing fields return `400`.
2. The appeal — submitter, reason, timestamp — is inserted into a dedicated
   `appeals` table **keyed by `content_id`**, so it sits alongside (never
   replaces) the decision it contests.
3. The matching `audit_log` row's status is flipped to **`under review`**. The
   original attribution, score, and signals are preserved untouched.
4. The response returns a **reviewer-queue view**: the original decision + the
   appeal, ready for a human to act on.

There is **no automatic re-classification** — an appeal flags the record for a
human, as specified.

---

## Rate limiting

The `/submit` endpoint is protected with
[Flask-Limiter](https://flask-limiter.readthedocs.io/), keyed on the client IP
(`get_remote_address`):

```python
@limiter.limit("10 per minute;100 per day")
```

### Chosen limits and reasoning

| Limit | Purpose |
| --- | --- |
| **10 / minute** | Burst guard |
| **100 / day** | Sustained-volume guard |

**Why these numbers:**

- **A real writer submits their own work.** A human authoring and checking pieces
  works at human speed — draft, submit, read the label, revise, resubmit. Even an
  actively-iterating writer rarely needs more than one submission every few
  seconds, so **10/minute** leaves comfortable headroom for legitimate
  back-and-forth while still cutting off anything faster than a person could
  plausibly generate. The submit path also calls an external LLM (Groq) per
  request, so a per-minute cap doubles as protection for that dependency and its
  cost.

- **100/day reflects a heavy-but-human day of work.** A dedicated writer polishing
  many pieces might submit dozens of times across a day; 100 covers that with
  margin. A script trying to farm the classifier or flood the audit log, however,
  wants thousands of calls — the daily cap makes that impossible from a single
  source without ever inconveniencing a genuine user.

- **Two windows, not one.** A single `100/day` limit would still allow a
  100-request burst in one second; a single `10/minute` limit would allow 14,400
  requests/day. Combining a short burst window with a longer sustained window
  stops both fast floods and slow grinding attacks.

- **Per-IP keying** is the right granularity here since submissions are
  unauthenticated. If auth is added later, the key should move to the account ID
  so shared-NAT users aren't penalized and a single abusive account can't rotate
  IPs.

### Verifying the limit

With the server running, send 12 rapid requests (the app listens on port
**5001**):

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5001/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}'
done
```

Observed output — the first 10 succeed, then the limiter returns `429`:

```
200
200
200
200
200
200
200
200
200
200
429
429
```

### Production note

Limits are stored in-memory (`storage_uri="memory://"`), which is correct for
local development and single-process runs. A multi-worker deployment should point
`storage_uri` at a shared backend (e.g. Redis) so counters are consistent across
workers; otherwise each worker enforces the limit independently and the effective
limit is multiplied by the worker count.

---

## Audit log

Every attribution decision is persisted to SQLite **before** the response is
returned, so nothing shown to a user is ever un-logged
([audit_log.py](audit_log.py)). Each entry records the content ID, creator,
timestamp, attribution, confidence score, the LLM/perplexity signal score, and a
lifecycle status (`classified` → `under review` when appealed). Appeals are
logged in a companion table keyed by the same `content_id`. `400` (invalid)
requests are **not** logged (`test_400_requests_are_not_logged`).

Sample `GET /log` output (real run — two classifications, one appeal that moved a
record to `under review`):

```json
{
  "entries": [
    {
      "attribution": "uncertain",
      "confidence": 0.306,
      "content_id": "750e97af-9915-4b8a-856e-2e932d71a8df",
      "creator_id": "poet-9",
      "llm_score": 0.0,
      "status": "classified",
      "timestamp": "2026-07-01T04:59:37.158Z"
    },
    {
      "attribution": "uncertain",
      "confidence": 0.79,
      "content_id": "ec83002d-7df7-4534-b476-f501a55e9f70",
      "creator_id": "agency-2",
      "llm_score": 0.95,
      "status": "under review",
      "timestamp": "2026-07-01T04:59:36.952Z"
    },
    {
      "attribution": "likely_human",
      "confidence": 0.045,
      "content_id": "095fec43-e858-416e-bedf-1e182d39d5aa",
      "creator_id": "poet-9",
      "llm_score": 0.0,
      "status": "classified",
      "timestamp": "2026-07-01T04:59:36.647Z"
    }
  ],
  "appeals": [
    {
      "content_id": "ec83002d-7df7-4534-b476-f501a55e9f70",
      "reason": "This is a human-written destination guide; the uniform structure is house style, not AI.",
      "submitter_id": "agency-2",
      "timestamp": "2026-07-01T04:59:37.162Z"
    }
  ]
}
```

Note the middle entry's `status` is `under review` — the appeal in `appeals`
references the same `content_id`, and the original attribution/score are
preserved.

---

## Known limitations

**Formulaic human travel writing gets misclassified as AI-leaning — by design of
both signals.** SEO listicles ("10 Best Beaches in Bali"), logistics sections
("how to get there, where to stay, prices"), and packing checklists are
*inherently* low-perplexity (stock phrasing) **and** low-burstiness (uniform,
parallel structure) even when a human writes them. That is precisely the failure
shown in the live audit log above: the human-written `agency-2` destination guide
scored `perplexity=0.95` and landed at `0.79` (`uncertain`) — the system is
leaning toward accusing a genuine human. Because *both* signals misfire on the
*same* content, no amount of combining them fixes it; the disagreement penalty and
corroboration floor only keep it out of the confident-AI band, buying `uncertain`
instead of a false `likely_ai`. This is a property of measuring word-choice
predictability and structural variance — the two things this system measures —
not a data-volume problem. A third, orthogonal signal (e.g. edit-history or
metadata provenance) would be the real fix.

Secondary limitations: the signals are **easily defeated** by a human lightly
editing AI output, or by AI prompted to "write quirkily / vary sentence length";
and burstiness is **unreliable on short texts** (<8 sentences), which is why short
inputs are damped toward `uncertain`.

---

## Spec reflection

**One way the spec guided the implementation:** the requirement that a `0.51`
confidence produce a *meaningfully different* label than a `0.95` forced me to
treat the score as a first-class output, not a byproduct of the label. That drove
the whole aggregator design — the disagreement penalty and the AI-corroboration
floor exist specifically so the number reflects genuine uncertainty, and it drove
`CalibrationTests`, which assert clearly-AI and clearly-human land >0.5 apart
rather than clustering. Without that spec line I'd likely have shipped a
thresholded binary.

**One way the implementation diverged:** [planning.md](planning.md) specifies
FastAPI and a GPT-2-small perplexity computation. The implementation uses
**Flask** (simpler for a service this small, and the rate-limiter/testing story
was quicker to stand up) and computes the perplexity signal via a **hosted Groq
model with a local heuristic fallback** rather than running GPT-2 locally. The
reason: a hosted small model classifies more reliably than raw GPT-2 perplexity on
short travel prose, and it avoids shipping model weights — at the cost of an
external dependency, which the fallback and the per-minute rate limit both
mitigate. The *contract* the plan described (a normalized 0–1 predictability
signal) is unchanged; only the mechanism differs.

---

## AI usage

- **Confidence-scorer calibration.** I directed the AI to design an aggregator
  that would keep a single strong signal from producing a confident AI verdict.
  It produced the weighted-average + disagreement-penalty structure. I **overrode**
  its first version, which let a maxed perplexity signal alone reach the AI band;
  I added the explicit `AI_CORROBORATION_FLOOR` (both signals must lean AI) and
  the test `test_no_single_signal_yields_confident_ai` to lock that in.

- **Perplexity signal reconciliation.** I asked the AI to wire up the Groq call.
  Its output trusted the model's raw numeric score directly. On testing I found
  the small model clusters its numbers around 0.7–0.8 regardless of content, so I
  **revised** the approach to trust the *categorical* verdict and reconcile the
  number into the implied band (`_reconcile_perplexity_score`), which is what makes
  the signal actually discriminate. I also added the heuristic fallback so the
  service degrades gracefully when Groq is unavailable.
