from flask import Flask, jsonify, request

app = Flask(__name__)


@app.post("/submit")
def submit():
    """Temporary stub endpoint that validates the incoming payload shape."""
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    creator_id = payload.get("creator_id", "")

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text is required"}), 400

    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "creator_id is required"}), 400

    return jsonify(
        {
            "content_id": "stub-content-id",
            "result": "uncertain",
            "confidence_score": 0.5,
            "label_text": "Uncertain origin — this text may be AI-generated or human-written",
            "signals": [],
            "submitted_by": creator_id,
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
