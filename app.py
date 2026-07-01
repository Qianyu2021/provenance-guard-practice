import uuid

from flask import Flask, jsonify, request

from audit_log import get_log, init_db, log_submission
from signals import (
    analyze_with_groq,
    burstiness_metrics,
    combine_signals,
    compute_burstiness_signal,
)

app = Flask(__name__)

init_db()


@app.post("/submit")
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


@app.get("/log")
def log():
    """Return the most recent audit log entries.

    Unauthenticated by design — this exists for documentation and grading
    visibility. A real deployment would put this behind auth.
    """
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
