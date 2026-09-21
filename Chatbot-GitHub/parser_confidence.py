"""Conservative confidence estimate for the existing rule-based parser."""

from __future__ import annotations

from typing import Any


FALLBACK_THRESHOLD = 0.60


def assess_confidence(question: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Return a score and human-readable signals; never change the parse."""
    score = 0.35
    reasons: list[str] = []

    if parsed.get("day") and parsed["day"] != "ไม่ระบุ":
        score += 0.15
        reasons.append("recognized day")
    if parsed.get("class"):
        score += 0.20
        reasons.append("recognized class")
    if parsed.get("time") or parsed.get("period"):
        score += 0.15
        reasons.append("recognized time")
    if parsed.get("subject_code") or parsed.get("room"):
        score += 0.10
        reasons.append("recognized source entity")
    if parsed.get("asks"):
        score += 0.15
        reasons.append("recognized requested field")
    if parsed.get("query_mode") not in (None, "schedule_detail"):
        score += 0.20
        reasons.append("recognized query mode")
    if parsed.get("_schedule_language"):
        score += 0.10
        reasons.append("schedule language")
    if parsed.get("_unknown_day"):
        score -= 0.20
        reasons.append("unresolved day")

    has_signal = any(parsed.get(field) for field in (
        "day", "class", "time", "period", "subject_code", "room", "asks", "time_of_day"
    )) or parsed.get("query_mode") not in (None, "schedule_detail")
    if not has_signal:
        score -= 0.30
        reasons.append("no recognized query signal")
    if len(question.strip()) <= 2:
        score -= 0.10
        reasons.append("very short question")

    confidence = round(max(0.0, min(1.0, score)), 2)
    return {"confidence": confidence, "reasons": reasons}
