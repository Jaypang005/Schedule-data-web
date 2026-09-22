"""Retrieval-first schedule chatbot orchestration.

Pipeline: question -> rule parser -> optional structured parser -> retriever -> formatter.
The optional AI parser runs locally and may return structured entities only.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ai_parser import parse_with_ai, validate_ai_query
from formatter import AnswerFormatter
from learned_language import correct_spelling, enhance
from normalizer import DEFAULT_DATA_DIR, QuestionNormalizer
from parser import TIME_OF_DAY_TERMS, QuestionParser
from parser_confidence import FALLBACK_THRESHOLD, assess_confidence
from retriever import ScheduleRetriever

PARSER_LOG = logging.getLogger("schedule_chatbot.parser_route")
INVALID_DAY_RESPONSE = "กรุณาระบุวันให้ถูกต้องตามตัวอักษร"
MULTI_QUESTION_RESPONSE = "ระบบรองรับครั้งละ 1 คำถาม กรุณาถามทีละคำถาม"
SPECIAL_HOLIDAY_TERMS = (
    "วันหยุดพิเศษ", "วันหยุดราชการ", "วันหยุดนักขัตฤกษ์",
    "วันปีใหม่", "สงกรานต์", "วันจักรี", "วันฉัตรมงคล",
    "วันแรงงาน", "วันวิสาขบูชา", "วันอาสาฬหบูชา",
    "วันเข้าพรรษา", "วันแม่", "วันพ่อ", "วันปิยมหาราช",
    "วันรัฐธรรมนูญ",
)
COMMON_DAY_MISSPELLINGS = {
    "อังคา", "อังคาน", "จันทร", "พุด", "พรึหัส", "พฤหัด",
    "สุก", "เสา", "อาทิด",
}
DAY_FOLLOWING_TERMS = (
    "นี้", "หน้า", "เรียน", "สอน", "มี", "ใช้", "เวลา", "ห้อง", "คาบ",
    "ขอ", "ตอน", "ช่วง", "ทั้ง", "รวม", "กี่", "ถึง", "ที่", "เป็น",
    "เริ่ม", "เลิก", "ต้อง", "เข้า", "เช้า", "บ่าย", "เย็น", "เที่ยง",
)
if os.environ.get("SCHEDULE_PARSER_LOG", "1") != "0" and not PARSER_LOG.handlers:
    PARSER_LOG.addHandler(logging.StreamHandler())
    PARSER_LOG.setLevel(logging.INFO)
    PARSER_LOG.propagate = False


def _grounded_ai_query(candidate: dict[str, Any], parsed: dict[str, Any], normalized: str) -> bool:
    """Require explicit entities to occur in the question or agree with rules."""
    if not parsed.get("_schedule_language") and not any(
        parsed.get(field) for field in ("day", "time", "class", "subject_code", "room", "asks")
    ) and not any(candidate[field] for field in ("day", "time", "class", "subject_code", "room")):
        return False
    lower = normalized.casefold()
    for field in ("day", "time", "class", "subject_code", "room"):
        value = candidate[field]
        if value is None:
            continue
        primary = parsed.get(field)
        if primary not in (None, value):
            return False
        if primary is None and value.casefold() not in lower:
            return False
    period = candidate["time_of_day"]
    if period is not None:
        primary_period = parsed.get("time_of_day")
        if primary_period not in (None, period):
            return False
        if primary_period is None and not any(term in normalized for term, value in TIME_OF_DAY_TERMS if value == period):
            return False
    primary_mode = parsed.get("query_mode")
    if primary_mode not in (None, "schedule_detail", candidate["query_mode"]):
        return False
    return True


class ScheduleChatbot:
    def __init__(self, data_dir: str | Path = DEFAULT_DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.normalizer = QuestionNormalizer(self.data_dir)
        self.parser = QuestionParser(self.data_dir)
        self.retriever = ScheduleRetriever(self.data_dir)
        self.formatter = AnswerFormatter(self.retriever.rules)

    def _has_misspelled_day(self, question: str) -> bool:
        """Reject a day-looking edge token instead of guessing the weekday."""
        text = unicodedata.normalize("NFKC", question).strip()
        if not any(term in text for term in ("เรียน", "สอน", "คาบ", "ตาราง", "ห้อง", "เวลา")):
            return False
        tokens = re.findall(r"[ก-๙ฯ]{2,14}", text)
        if not tokens:
            return False
        allowed = set(self.normalizer.day_aliases)
        exact_day_following = DAY_FOLLOWING_TERMS + (
            "ครับ", "ค่ะ", "คะ", "หน่อย", "บ้าง", "ไหม", "มั้ย",
        )
        # Thai is commonly typed without spaces (for example
        # "เรียนอะไรวันอังคาร"). If an exact day alias occurs anywhere and is
        # followed by a normal schedule/polite suffix, it is not a misspelling.
        for alias in sorted(allowed, key=len, reverse=True):
            for match in re.finditer(re.escape(alias), text):
                remainder = text[match.end():]
                if not remainder or remainder.startswith(exact_day_following):
                    return False
        candidates = list(dict.fromkeys((tokens[0], tokens[-1])))
        for token in candidates:
            candidate = token[3:] if token.startswith("วัน") else token
            if not candidate or candidate in allowed:
                continue
            valid_prefix = False
            for alias in sorted(allowed, key=len, reverse=True):
                if len(alias) < 3 or not candidate.startswith(alias):
                    continue
                remainder = candidate[len(alias):]
                if not remainder or remainder.startswith(DAY_FOLLOWING_TERMS):
                    valid_prefix = True
                    break
            if valid_prefix:
                continue
            if candidate in COMMON_DAY_MISSPELLINGS:
                return True
            ranked = sorted(
                (
                    SequenceMatcher(None, candidate, alias).ratio()
                    for alias in allowed
                    if len(alias) >= 3
                ),
                reverse=True,
            )
            best = ranked[0] if ranked else 0.0
            second = ranked[1] if len(ranked) > 1 else 0.0
            threshold = 0.66 if len(candidate) <= 3 else 0.80
            if best >= threshold and best - second >= 0.08:
                return True
        return False

    @staticmethod
    def _non_teaching_day_response(question: str) -> str | None:
        """Return a clear no-class answer for weekends and named holidays."""
        text = unicodedata.normalize("NFKC", question).strip()
        has_saturday = "เสาร์" in text
        has_sunday = (
            "วันอาทิตย์" in text
            or bool(re.search(r"เสาร์\s*[-–]?\s*อาทิตย์", text))
            or (
                text.startswith("อาทิตย์")
                and not re.search(r"อาทิตย์(?:หนึ่ง|นึง)|อาทิตย์ละ|ทั้งอาทิตย์", text)
            )
        )
        if has_saturday and has_sunday:
            return "วันเสาร์และวันอาทิตย์เป็นวันหยุด ไม่มีการเรียนการสอนครับ"
        if has_saturday:
            return "วันเสาร์เป็นวันหยุด ไม่มีการเรียนการสอนครับ"
        if has_sunday:
            return "วันอาทิตย์เป็นวันหยุด ไม่มีการเรียนการสอนครับ"
        if any(term in text for term in SPECIAL_HOLIDAY_TERMS):
            return "วันดังกล่าวเป็นวันหยุดพิเศษ ไม่มีการเรียนการสอนครับ"
        return None

    @staticmethod
    def _needs_day_clarification(parsed: dict[str, Any]) -> bool:
        """Never broaden an unscoped detail question to the whole schedule."""
        if parsed.get("query_mode") != "schedule_detail":
            return False
        if parsed.get("_yes_no"):
            return False
        if any(
            parsed.get(field)
            for field in (
                "day", "time", "period", "class", "subject_code", "room",
                "time_of_day", "_next_period", "_first_period",
            )
        ):
            return False
        return bool(
            set(parsed.get("asks", ())).intersection(
                {"subject", "time", "room", "class", "student_count", "lesson_type"}
            )
        )

    @staticmethod
    def _has_multiple_questions(parsed: dict[str, Any]) -> bool:
        """Reject mixed schedule-detail and subject-metadata requests."""
        asks = set(parsed.get("asks", ()))
        schedule_fields = {
            "subject", "time", "room", "class", "student_count",
            "combined_class", "lesson_type", "online",
        }
        metadata_fields = {"credit", "theory_hr", "practice_hr", "total_hr"}
        return bool(asks.intersection(schedule_fields) and asks.intersection(metadata_fields))

    def _clarification_response(self, reply: str = INVALID_DAY_RESPONSE) -> dict[str, Any]:
        info = self.retriever.teacher_data["info"]
        return {
            "reply": reply,
            "teacher": {
                "name": info["teacher_name"],
                "department": info["department"],
                "semester": info["semester"],
                "college": info["college"],
            },
            "schedule_cards": [],
            "needs_clarification": True,
        }

    def parse_question(self, question: str) -> dict[str, Any]:
        """Expose the parsed NLU structure for API users and debugging."""
        question = correct_spelling(question)
        parsed = self.parser.parse(question)
        expanded = enhance(question, parsed)
        if expanded is not None:
            candidate = self.parser.parse(expanded)
            # Training never supplies schedule entities; preserve explicit source filters.
            fields = ("class", "subject_code", "room", "time", "period")
            if (parsed.get("day") in (None, "ไม่ระบุ") or candidate.get("day") == parsed["day"]) and all(parsed.get(field) is None or candidate.get(field) == parsed[field] for field in fields):
                parsed = candidate
                parsed["_learned_language"] = True
        if assess_confidence(question, parsed)["confidence"] >= FALLBACK_THRESHOLD:
            PARSER_LOG.info("RULE PARSER: %s", question)
            return parsed
        try:
            candidate = validate_ai_query(parse_with_ai(question), self.data_dir)
        except Exception:
            # An unavailable future model must not break deterministic parsing.
            PARSER_LOG.info("RULE PARSER (AI unavailable): %s", question)
            return parsed
        if candidate is None:
            PARSER_LOG.info("RULE PARSER (AI invalid): %s", question)
            return parsed
        if not _grounded_ai_query(candidate, parsed, self.normalizer.normalize(question)):
            PARSER_LOG.info("RULE PARSER (AI ungrounded): %s", question)
            return parsed
        result = {**parsed}
        for field in ("day", "time", "time_of_day", "class", "subject_code", "room"):
            if candidate[field] is not None:
                result[field] = candidate[field]
        if candidate["asks"] and not result.get("asks"):
            result["asks"] = candidate["asks"]
        result["query_mode"] = (
            primary_mode if (primary_mode := parsed.get("query_mode")) not in (None, "schedule_detail")
            else candidate["query_mode"] or "schedule_detail"
        )
        result["period"] = None  # The structured AI interface has only point time.
        result["_unknown_day"] = False
        result["_asks_day"] = False
        result["_info_key"] = None
        result["_schedule_language"] = True
        result["_query_mode"] = result["query_mode"] if result["query_mode"] != "schedule_detail" else None
        PARSER_LOG.info("AI FALLBACK: %s", question)
        return result

    def retrieve(self, question_or_parsed: str | dict[str, Any]) -> dict[str, Any]:
        parsed = (
            self.parse_question(question_or_parsed)
            if isinstance(question_or_parsed, str)
            else question_or_parsed
        )
        return self.retriever.retrieve(parsed)

    def answer(self, question: str) -> str:
        if holiday_reply := self._non_teaching_day_response(question):
            return holiday_reply
        if self._has_misspelled_day(question):
            return INVALID_DAY_RESPONSE
        parsed = self.parse_question(question)
        if self._has_multiple_questions(parsed):
            return MULTI_QUESTION_RESPONSE
        if self._needs_day_clarification(parsed):
            return INVALID_DAY_RESPONSE
        result = self.retriever.retrieve(parsed)
        reply = self.formatter.format(result)
        if result.get("kind") == "not_found" or reply == self.formatter.not_found:
            return reply
        rows = result.get("rows", [])
        if rows:
            teacher = self.retriever.teacher_data["info"]["teacher_name"]
            reply = f"ตรวจสอบจากตารางสอนของ {teacher} แล้วครับ\n\n{reply}"
        return reply

    def response(self, question: str) -> dict[str, Any]:
        """Return a web-friendly answer with grounded teacher and schedule cards."""
        if holiday_reply := self._non_teaching_day_response(question):
            return self._clarification_response(holiday_reply)
        if self._has_misspelled_day(question):
            return self._clarification_response()
        parsed = self.parse_question(question)
        if self._has_multiple_questions(parsed):
            return self._clarification_response(MULTI_QUESTION_RESPONSE)
        if self._needs_day_clarification(parsed):
            return self._clarification_response()
        result = self.retriever.retrieve(parsed)
        reply = self.formatter.format(result)
        if result.get("kind") == "not_found" or reply == self.formatter.not_found:
            return self._clarification_response(reply)
        info = self.retriever.teacher_data["info"]
        rows = result.get("rows", [])
        if rows:
            reply = f"ตรวจสอบจากตารางสอนของ {info['teacher_name']} แล้วครับ\n\n{reply}"

        cards = []
        for row in rows:
            subject = row.get("subject", {})
            cards.append({
                "day": row.get("day"),
                "start_time": row.get("start_time"),
                "end_time": row.get("end_time"),
                "subject": subject.get("name"),
                "subject_code": row.get("code"),
                "class": row.get("class"),
                "room": row.get("room"),
                "lesson_type": row.get("lesson_type"),
                "delivery_type": row.get("type"),
                "student_count": row.get("student_count"),
            })

        return {
            "reply": reply,
            "teacher": {
                "name": info["teacher_name"],
                "department": info["department"],
                "semester": info["semester"],
                "college": info["college"],
            },
            "schedule_cards": cards,
            "needs_clarification": False,
        }


def answer(question: str, data_dir: str | Path = DEFAULT_DATA_DIR) -> str:
    return ScheduleChatbot(data_dir).answer(question)


def main() -> None:
    cli = argparse.ArgumentParser(description="Schedule retrieval chatbot")
    cli.add_argument("question", nargs="*", help="คำถาม (ถ้าเว้นว่างจะเข้าโหมดโต้ตอบ)")
    cli.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = cli.parse_args()
    bot = ScheduleChatbot(args.data_dir)
    if args.question:
        print(bot.answer(" ".join(args.question)))
        return
    print("พิมพ์คำถามเกี่ยวกับตารางสอน (พิมพ์ exit เพื่อออก)")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question.casefold() in {"exit", "quit", "ออก"}:
            break
        print(bot.answer(question))


if __name__ == "__main__":
    main()
