Provenance Guard — Planning

Backend system a travel blog platform can plug into to classify submitted posts,
score confidence, surface a transparency label, and handle creator appeals.
(Target content type: travel blog posts — personal narrative, destination write-ups,
trip reports.)

Stack: Python / FastAPI, SQLite for storage.


Step 1 — The path a single piece of text takes (submission → label)

Below is the full journey of one submitted poem, naming every component it touches
and what each does. This is the reference flow the rest of the build follows.

1. Submission API — POST /submit (FastAPI route)

The front door. The creator's platform sends the text as JSON. Validates the payload
(text present? within length limits?) and hands off a clean request. No analysis
happens until this gate passes.

2. Rate Limiter (middleware, in front of the route)

Runs before the text is analyzed. Checks the request against a per-creator / per-IP
counter. Too many submissions too fast → rejected with 429 here, goes no further.
Protects the detection engine from flooding and stops an actor from rapidly probing
the system to reverse-engineer it.

3. Detection Pipeline (analysis core)

Runs the text through multiple independent signals, each a separate analyzer looking
at a different property (e.g. statistical regularity/perplexity, burstiness &
sentence-length variation, vocabulary/punctuation patterns). Each returns its own
vote and strength. No single signal decides the outcome.

4. Confidence Scorer / Aggregator

Combines the signal outputs into one calibrated confidence score (0–1) plus a leaning
(AI / human / uncertain). Where signals conflict, the score is pulled toward the
middle rather than forced into a confident verdict. Deliberately biased against false
"AI" accusations, since wrongly flagging a human is the costlier error.

5. Label Generator

Translates the numeric score into the human-facing transparency label — one of three
plain-language variants (high-confidence AI, high-confidence human, uncertain). The
reader sees calibrated language, never the raw number.

6. Datastore (SQLite) + Audit Log

Before returning anything, the full decision is persisted: content (or hash), score,
which signals fired and how they voted, label shown, timestamp, unique content ID.
The Audit Log (GET /log) reads from this, and appeals later attach to it. Writing
happens BEFORE the response, so nothing shown to a user is ever un-logged.

7. Response back to the platform

Returns the structured result — attribution result, confidence score, label text —
to the calling platform, which renders the label to the reader.

8. Appeals API — POST /appeal (later, asynchronous)

If the creator disputes the label, the platform sends the content ID plus the
creator's written reasoning. The handler looks up the original decision, attaches the
appeal (reasoning + timestamp), flips content status to "under review," and writes the
appeal into the same audit log. No automatic re-scoring — the record is flagged for a
human.

Through-line

Rate Limiter → Submission API → Detection Pipeline (multi-signal) → Confidence Scorer → Label Generator → Datastore/Audit Log → Response, with the Appeals API as a
later branch that reopens a stored decision.


Step 2 — The two detection signals (decided before writing code)

Target content type: travel blog posts. Travel writing is narrative and personal
(first-person experience, sensory detail, anecdotes, opinions), which gives both
signals strong separation between human and AI. The known weak spot is travel's
formulaic underbelly — SEO listicles, logistics sections, packing checklists — which
is low-perplexity and low-burstiness even when human. That weak spot is a subset of
the domain, so the system biases toward "uncertain" there rather than falsely accusing.

Signal 1 — Token-level predictability (perplexity)


Measures: How "surprised" a small language model (GPT-2 small) is by each next
word, averaged over the text. Low perplexity = highly predictable prose; high
perplexity = frequent locally-surprising word choices. Code blocks are stripped
before scoring.
Why it differs human vs. AI: LLMs generate by repeatedly choosing
high-probability tokens, so their output sits in a low-perplexity valley — the "most
likely" phrasing of each thought. Humans make locally surprising choices (odd words,
tangents, asides, jokes) that raise average perplexity.
Blind spot: Travel's formulaic sections are inherently low-perplexity even when
human — SEO listicles ("10 Best Beaches in Bali"), logistics ("how to get there,
where to stay, prices"), packing checklists — so those human passages can look
AI-like. Defeated by a human lightly editing AI output, or AI prompted to write
"quirkily." (No code to strip in travel content, unlike a tech blog.)


Signal 2 — Burstiness (structural variance)


Measures: Variance in sentence length and rhythm — coefficient of variation of
sentence lengths plus uniformity of paragraph sizes. High burstiness = mix of long
and very short sentences; low = everything similar, medium length.
Why it differs human vs. AI: Humans write in bursts (long explanatory sentence,
then a short punch). AI defaults to steady, uniform, medium-length sentences and even
paragraph blocks, optimizing each sentence locally toward the average rather than for
contrast. Nearly independent of perplexity (word choice vs. structure), which is why
pairing them adds information.
Blind spot: Format-driven travel writing is very uniform (listicles, itinerary
day-by-day breakdowns, logistics rundowns), so human "10 best…" posts can score
AI-like. Unreliable on short texts (needs ~8+ sentences). Easily defeated by AI told
to "vary sentence length."


Design consequence

Both signals independently misfire on the same formulaic travel content (listicles,
logistics, checklists). Therefore: (a) no single signal alone may produce a confident
"AI" verdict, and (b) the scorer biases toward "uncertain" over a false human
accusation.

Decision policy and UX details

1. Detection signals

- Signal 1 returns a normalized perplexity-based score in the range 0.0–1.0, where
  0.0 means very human-like and 1.0 means very AI-like.
- Signal 2 returns a normalized burstiness-based score in the range 0.0–1.0, where
  0.0 means very human-like and 1.0 means very AI-like.
- Each signal also carries lightweight metadata (for example: tokens analyzed,
  sentence count, or structural variance) that can be logged for audit purposes but is
  not shown to end users.

2. Uncertainty representation

- The system reports a calibrated confidence score in the range 0.0–1.0, where 0.0
  means strong evidence for human authorship and 1.0 means strong evidence for AI
  authorship.
- A score of 0.6 means the evidence leans somewhat toward AI, but not strongly
  enough for a high-confidence label; the system should show the uncertain label.
- Raw signal outputs are first normalized to 0.0–1.0, then combined by a conservative
  weighted average with a penalty when the two signals disagree. The resulting score is
  calibrated against a small labeled validation set so the number is interpretable rather
  than purely heuristic.
- Thresholds: score >= 0.80 => likely AI; 0.25 <= score < 0.80 => uncertain; score <
  0.25 => likely human.

3. Transparency label design

- High-confidence AI result: "Likely AI-generated"
- High-confidence human result: "Likely human-written"
- Uncertain result: "Uncertain origin — this text may be AI-generated or human-written"

4. Appeals workflow

- Who can submit an appeal: the original creator or the platform account that submitted
  the content.
- Required appeal information: content ID, submitter account ID, a short explanation of
  why the label seems wrong, and optional supporting context.
- What happens when an appeal is received: the system sets the content status to
  "under review," appends the appeal record to the audit log with timestamp and reason,
  and preserves the original decision until a human reviewer acts.
- What a human reviewer sees in the queue: content ID, original label, confidence score,
  the signals used, the original decision timestamp, the appeal reason, and the current
  status.

5. Anticipated edge cases

- A poem with heavy repetition and simple vocabulary may be scored as AI-like because the
  heuristics see low surprisal and low structural variance.
- A short, highly formulaic itinerary or packing checklist written by a human may be
  scored as AI-like because the structure is so uniform.
- A human-edited AI draft may evade the system if the writer deliberately varies
  sentence length and wording to look less machine-like.

### architecture

SUBMISSION FLOW
===============
POST /submit
   | raw text (JSON body)
   v
Rate Limiter
   | cleaned raw text
   v
Signal 1: Perplexity
   | raw text + perplexity score
   v
Signal 2: Burstiness
   | perplexity score + burstiness score
   v
Confidence Scoring
   | combined score (0-1) + leaning
   v
Transparency Label
   | label text + score + signals used
   v
Audit Log (write) ----[decision record]----> Datastore / SQLite
   | result + confidence score + label text
   v
Response to platform


APPEAL FLOW
===========
POST /appeal
   | content ID + submitter account ID + appeal reason + optional context
   v
Status Update -> "under review"   <----[looks up original decision by content ID]---- Datastore / SQLite
   | status change + appeal reasoning + timestamp
   v
Audit Log (append appeal) ----[appeal record]----> Datastore / SQLite
   | appeal ID + updated status + reviewer queue metadata
   v
Human Reviewer Queue
   | content ID + original label + confidence score + signals used + appeal reason
   v
Response to platform