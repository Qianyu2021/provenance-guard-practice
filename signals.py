import os
import re
from collections import Counter
from typing import Any, Dict

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def compute_perplexity_signal(text: str) -> float:
    """Return a normalized 0.0-1.0 score for text predictability.

    Higher values indicate a more AI-like signal. This fallback remains available
    when the Groq call is unavailable or fails.
    """
    if not isinstance(text, str) or not text.strip():
        return 0.0

    tokens = re.findall(r"\b\w+\b", text.lower())
    if not tokens:
        return 0.0

    frequency = Counter(tokens)
    unique_ratio = len(frequency) / len(tokens)
    repetition_component = max(0.0, 1.0 - unique_ratio)

    sentence_count = len(re.findall(r"[.!?]+", text))
    sentence_component = 0.0 if sentence_count >= 3 else 0.15

    score = min(1.0, max(0.0, 0.8 * repetition_component + 0.2 * sentence_component))
    return round(score, 3)


def analyze_with_groq(text: str) -> Dict[str, Any]:
    """Ask Groq for a structured assessment of whether the text looks AI-generated."""
    if not isinstance(text, str) or not text.strip():
        return {"score": 0.0, "result": "uncertain", "rationale": "Empty input."}

    if not os.getenv("GROQ_API_KEY"):
        fallback_score = compute_perplexity_signal(text)
        return {
            "score": fallback_score,
            "result": "likely_ai" if fallback_score >= 0.8 else "likely_human" if fallback_score < 0.25 else "uncertain",
            "rationale": "Groq API key not configured; used heuristic fallback.",
        }

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You assess whether a short text looks AI-generated. "
                        "Respond with valid JSON only using this schema: "
                        '{"score": 0.0, "result": "likely_ai|likely_human|uncertain", "rationale": "short explanation"}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Assess this travel blog text. "
                        "Return only valid JSON with the requested schema.\n\n"
                        f"Text: {text}"
                    ),
                },
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        payload = completion.choices[0].message.content
        parsed = payload if isinstance(payload, dict) else {}
        if isinstance(payload, str):
            import json

            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                parsed = {}

        score = float(parsed.get("score", 0.0))
        result = parsed.get("result", "uncertain")
        rationale = parsed.get("rationale", "No rationale returned.")

        return {
            "score": max(0.0, min(1.0, score)),
            "result": result if result in {"likely_ai", "likely_human", "uncertain"} else "uncertain",
            "rationale": rationale,
        }
    except Exception as exc:
        fallback_score = compute_perplexity_signal(text)
        return {
            "score": fallback_score,
            "result": "likely_ai" if fallback_score >= 0.8 else "likely_human" if fallback_score < 0.25 else "uncertain",
            "rationale": f"Groq request failed: {exc}; used heuristic fallback.",
        }
