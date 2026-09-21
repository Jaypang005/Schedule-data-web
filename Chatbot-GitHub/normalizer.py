"""Conservative Thai text normalization for schedule questions."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from learned_language import correct_spelling


DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
CLASS_RE = re.compile(r"(สท|ทค)\s*\.?\s*(\d+)\s*/\s*(\d+)(?:\s*-\s*(\d+))?", re.I)
CODE_RE = re.compile(r"(?<!\d)\d{5}-\d{4}(?!\d)")
WEEKDAY_NAMES = (
    "จันทร์", "อังคาร", "พุธ", "พฤหัสฯ", "ศุกร์", "เสาร์", "อาทิตย์",
)
RELATIVE_DAY_OFFSETS = {
    "วันนี้": 0,
    "พรุ่งนี้": 1,
    "เมื่อวาน": -1,
    "today": 0,
    "tomorrow": 1,
    "yesterday": -1,
}
RELATIVE_DAY_RE = re.compile(
    r"วันนี้|พรุ่งนี้|เมื่อวาน|(?<![A-Za-z])(?:today|tomorrow|yesterday)(?![A-Za-z])",
    re.I,
)
SCHEDULE_TERMS = (
    "เรียน", "สอน", "คาบ", "เวลา", "ห้อง", "ออนไลน์", "ตาราง",
    "class", "schedule", "study",
)


def _server_now() -> datetime:
    """Return the server's current local date and time."""
    return datetime.now()


def resolve_relative_day(text: str, now: datetime | date | None = None) -> str:
    """Replace relative-day words with a Thai weekday calculated at call time."""
    reference = now or _server_now()
    base_date = reference.date() if isinstance(reference, datetime) else reference

    def replace(match: re.Match[str]) -> str:
        offset = RELATIVE_DAY_OFFSETS[match.group(0).casefold()]
        target_date = base_date + timedelta(days=offset)
        return WEEKDAY_NAMES[target_date.weekday()]

    return RELATIVE_DAY_RE.sub(replace, text)


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _compact(value: str) -> str:
    return re.sub(r"[^0-9A-Za-zก-๙]", "", value.casefold())


def _best_substring_ratio(text: str, candidate: str) -> float:
    source, target = _compact(text), _compact(candidate)
    if not source or not target:
        return 0.0
    best = 0.0
    for size in range(max(2, len(target) - 2), len(target) + 3):
        if size > len(source):
            continue
        for start in range(len(source) - size + 1):
            best = max(best, SequenceMatcher(None, source[start:start + size], target).ratio())
    return best


class QuestionNormalizer:
    """Normalize known entities without modifying the JSON source files."""

    def __init__(self, data_dir: str | Path = DEFAULT_DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        rules = _load(self.data_dir / "rules.json")
        teacher = _load(self.data_dir / "teacher_subjects.json")
        self.day_aliases: dict[str, str] = rules["day_aliases"]
        self.subject_aliases: dict[str, str] = rules["subject_aliases"]
        self.subjects: list[dict[str, Any]] = teacher["subjects"]

    @staticmethod
    def normalize_class(value: str) -> str:
        match = CLASS_RE.search(value)
        if not match:
            return value
        suffix = f"-{match.group(4)}" if match.group(4) else ""
        return f"{match.group(1)}.{match.group(2)}/{match.group(3)}{suffix}"

    @staticmethod
    def _normalize_classes(text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            suffix = f"-{match.group(4)}" if match.group(4) else ""
            return f"{match.group(1)}.{match.group(2)}/{match.group(3)}{suffix}"
        return CLASS_RE.sub(replace, text)

    @staticmethod
    def _normalize_numeric_times(text: str) -> str:
        def replace_hour(match: re.Match[str]) -> str:
            hour = int(match.group(1))
            return f"{hour:02d}:00" if 0 <= hour < 24 else match.group(0)
        text = re.sub(r"(?<!\d)([0-2]?\d)\s*(?:โมง(?:เช้า|เย็น)?|นาฬิกา)", replace_hour, text)
        return re.sub(
            r"(?<!\d)([0-2]?\d)\s*[.:]\s*([0-5]\d)(?!\d)",
            lambda m: f"{int(m.group(1)):02d}:{m.group(2)}", text,
        )

    @staticmethod
    def _normalize_spoken_times(text: str) -> str:
        spoken = {
            "เที่ยง": "12:00", "บ่ายโมง": "13:00", "บ่ายหนึ่ง": "13:00",
            "บ่ายสอง": "14:00", "บ่ายสาม": "15:00", "บ่ายสี่": "16:00",
            "บ่ายห้า": "17:00",
        }
        for phrase, value in sorted(spoken.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(phrase, value)
        return text

    def _normalize_days(self, text: str) -> str:
        aliases = sorted(self.day_aliases, key=len, reverse=True)
        pattern = re.compile(r"(?:วัน)?(" + "|".join(map(re.escape, aliases)) + r")(?=$|\s|นี้|หน้า|เรียน|สอน|มี|ใช้|เวลา|ห้อง|คาบ|ขอ|ตอน|ช่วง|ทั้ง|รวม|กี่|ถึง|ที่|เป็น|เริ่ม|เลิก|ต้อง|เข้า|เช้า|บ่าย|เย็น|เที่ยง|กลางคืน|ค่ำ|\d|[?.!,])")
        normalized, count = pattern.subn(lambda m: self.day_aliases[m.group(1)], text)
        if count:
            return normalized
        # Fuzzy matching is restricted to a day-looking fragment. Comparing
        # against the whole sentence made words such as "วันนี้" unsafe.
        after_day = re.search(r"วัน([ก-๙ฯ]{2,10})", text)
        if after_day:
            fuzzy_source = after_day.group(1)
        else:
            fuzzy_source = ""
        best_day, best_score = None, 0.0
        for alias, canonical in self.day_aliases.items():
            if len(alias) < 3:
                continue
            score = _best_substring_ratio(fuzzy_source, alias)
            if score > best_score:
                best_day, best_score = canonical, score
        if best_day and best_score >= 0.74:
            return f"{normalized} {best_day}"
        return normalized

    def _append_subject_code(self, text: str) -> str:
        if CODE_RE.search(text):
            return text
        lower = text.casefold()
        candidates = list(self.subject_aliases.items())
        candidates.extend((subject["name"], subject["code"]) for subject in self.subjects)
        for phrase, code in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
            exact = (
                re.search(rf"(?<![A-Za-z]){re.escape(phrase)}(?![A-Za-z])", text, re.I)
                if phrase.isascii() else phrase.casefold() in lower
            )
            if exact:
                return f"{text} {code}"
        best_code, best_score = None, 0.0
        for phrase, code in candidates:
            if len(_compact(phrase)) < 5:
                continue
            score = _best_substring_ratio(text, phrase)
            if score > best_score:
                best_code, best_score = code, score
        if best_code and best_score >= 0.74:
            return f"{text} {best_code}"
        return text

    def normalize(self, question: str) -> str:
        text = correct_spelling(unicodedata.normalize("NFKC", question)).strip()
        # Stray quote characters are common on mobile keyboards. Treat them as
        # separators so "อังคาร'เรียนไร" still keeps the explicit weekday.
        text = re.sub(r"['\"‘’“”]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        # Resolve relative dates only for schedule-like questions. The public
        # utility itself remains unconditional, while this guard prevents an
        # unrelated query such as "ราคาทองวันนี้" from becoming a day query.
        if any(term in text.casefold() for term in SCHEDULE_TERMS):
            text = resolve_relative_day(text)
        text = self._normalize_classes(text)
        text = self._normalize_spoken_times(text)
        text = self._normalize_numeric_times(text)
        text = self._normalize_days(text)
        text = self._append_subject_code(text)
        return re.sub(r"\s+", " ", text).strip()


def normalize_question(question: str, data_dir: str | Path = DEFAULT_DATA_DIR) -> str:
    return QuestionNormalizer(data_dir).normalize(question)
