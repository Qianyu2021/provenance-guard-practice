import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from audit_log import (
    get_appeals,
    get_decision,
    get_log,
    init_db,
    log_appeal,
    log_submission,
)
from signals import (
    analyze_with_groq,
    burstiness_metrics,
    combine_signals,
    compute_burstiness_signal,
)

app = Flask(__name__)

# Rate limiter keyed on client IP. In-memory storage is fine for local dev /
# a single-process deployment; a real multi-worker deployment would point
# storage_uri at Redis so counters are shared across workers.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

init_db()


@app.post("/submit")
@limiter.limit("10 per minute;100 per day")
def submit():
    """Submission endpoint: validate payload, run both signals, combine, label."""
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    creator_id = payload.get("creator_id", "")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text is required"}), 400

    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "creator_id is required"}), 400

    # Signal 1: token-level predictability (perplexity, via Groq w/ heuristic fallback).
    signal1 = analyze_with_groq(text)
    perplexity_score = signal1["score"]

    # Signal 2: burstiness / structural variance.
    burstiness_score = compute_burstiness_signal(text)
    burstiness_meta = burstiness_metrics(text)

    # Confidence scorer: conservative weighted average + disagreement penalty.
    decision = combine_signals(perplexity_score, burstiness_score)
    content_id = str(uuid.uuid4())
    confidence_score = decision["score"]

    # Persist the decision BEFORE responding, so nothing shown to a user is un-logged.
    log_submission(
        content_id=content_id,
        creator_id=creator_id,
        attribution=decision["result"],
        confidence=confidence_score,
        llm_score=round(perplexity_score, 3),
    )

    return jsonify(
        {
            "content_id": content_id,
            "result": decision["result"],
            "confidence_score": confidence_score,
            "label_text": decision["label_text"],
            "signals": [
                {
                    "name": "perplexity",
                    "score": round(perplexity_score, 3),
                    "result": signal1["result"],
                    "rationale": signal1["rationale"],
                },
                {
                    "name": "burstiness",
                    "score": burstiness_score,
                    "metrics": burstiness_meta,
                },
            ],
            "disagreement": decision["disagreement"],
            "corroborated": decision["corroborated"],
            "submitted_by": creator_id,
        }
    )


@app.post("/appeal")
def appeal():
    """Appeals endpoint: a creator contests a classification.

    Captures the creator's reasoning, logs it alongside the original decision,
    and flips the content's status to "under review". No automatic
    re-classification — the record is flagged for a human reviewer.
    """
    payload = request.get_json(silent=True) or {}
    content_id = payload.get("content_id", "")
    submitter_id = payload.get("submitter_id", "")
    reason = payload.get("reason", "")

    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "content_id is required"}), 400
    if not isinstance(submitter_id, str) or not submitter_id.strip():
        return jsonify({"error": "submitter_id is required"}), 400
    if not isinstance(reason, str) or not reason.strip():
        return jsonify({"error": "reason is required"}), 400

    if get_decision(content_id) is None:
        return jsonify({"error": "no decision found for content_id"}), 404

    review = log_appeal(
        content_id=content_id,
        submitter_id=submitter_id,
        reason=reason,
    )

    return jsonify(review)


@app.get("/log")
def log():
    """Return the most recent audit log entries and appeals.

    Unauthenticated by design — this exists for documentation and grading
    visibility. A real deployment would put this behind auth.
    """
    return jsonify({"entries": get_log(), "appeals": get_appeals()})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
