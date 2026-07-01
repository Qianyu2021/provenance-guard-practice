import uuid

from flask import Flask, jsonify, request

from audit_log import get_log, init_db, log_submission
from signals import analyze_with_groq

app = Flask(__name__)

init_db()


@app.post("/submit")
def submit():
    """Submission endpoint that validates the incoming payload and runs signal 1."""
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    creator_id = payload.get("creator_id", "")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text is required"}), 400

    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "creator_id is required"}), 400

    signal_result = analyze_with_groq(text)
    content_id = str(uuid.uuid4())
    confidence_score = round(signal_result["score"], 3)

    # Persist the decision BEFORE responding, so nothing shown to a user is un-logged.
    log_submission(
        content_id=content_id,
        creator_id=creator_id,
        attribution=signal_result["result"],
        confidence=confidence_score,
        llm_score=confidence_score,
    )

    return jsonify(
        {
            "content_id": content_id,
            "result": signal_result["result"],
            "confidence_score": confidence_score,
            "label_text": "Uncertain origin — this text may be AI-generated or human-written",
            "signals": [
                {
                    "name": "groq_signal_1",
                    "score": confidence_score,
                    "result": signal_result["result"],
                    "rationale": signal_result["rationale"],
                }
            ],
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
