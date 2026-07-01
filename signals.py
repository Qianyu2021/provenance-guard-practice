import re
from collections import Counter


def compute_perplexity_signal(text: str) -> float:
    """Return a normalized 0.0-1.0 score for text predictability.

    Higher values indicate a more AI-like signal. The function is intentionally
    heuristic-based and meant as a stub for the first detection signal.
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
