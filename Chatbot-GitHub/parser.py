"""Intent and entity parser for normalized schedule questions."""

from __future__ import annotations

import json
import re
import normalizer
from pathlib import Path
from typing import Any

from normalizer import CLASS_RE, CODE_RE, DEFAULT_DATA_DIR, QuestionNormalizer


TIME_RE = re.compile(r"(?<!\d)([0-2]\d:[0-5]\d)(?!\d)")
OUT_OF_SCOPE_TOPIC_RE = re.compile(r"โรงอาหาร|อาหาร|ฝน|ค่าเทอม|ทุนการศึกษา|สอบ|รถรับส่ง|จันทบุรี|พระจันทร์|ดวงจันทร์|แสงจันทร์|พุดดิ้ง")
TIME_OF_DAY_TERMS = (
    ("กลางคืน", "night"), ("ค่ำ", "night"), ("เช้า", "morning"),
    ("เที่ยง", "noon"), ("บ่าย", "afternoon"), ("เย็น", "evening"),
)
SUPPORTED_ASKS = (
    "subject", "subject_code", "time", "room", "class", "student_count",
    "credit", "theory_hr", "practice_hr", "total_hr", "teacher",
    "department", "combined_class", "lesson_type", "online",
)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class QuestionParser:
    def __init__(self, data_dir: str | Path = DEFAULT_DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.normalizer = QuestionNormalizer(self.data_dir)
        with (self.data_dir / "rules.json").open("r", encoding="utf-8") as handle:
            self.rules = json.load(handle)
        with (self.data_dir / "schedule.json").open("r", encoding="utf-8") as handle:
            schedule = json.load(handle)["schedule"]
        self.rooms = sorted({row["room"] for row in schedule}, key=len, reverse=True)

    def parse(self, question: str) -> dict[str, Any]:
        original_lower = question.casefold()
        text = self.normalizer.normalize(question)
        lower = text.casefold()
        days = _unique([canonical for canonical in self.rules["day_aliases"].values() if canonical in text])
        unknown_day = bool(
            not days and "วันไหน" not in text
            and not re.search(r"(?:ทั้ง|หนึ่ง|1\s*)อาทิตย์|อาทิตย์(?:หนึ่ง|นึง)", text)
            and re.search(r"(?:วัน)?(?:เสาร์|อาทิตย์)|วันนี้|พรุ่งนี้|มะรืน", text)
        )
        if unknown_day:
            if "อาทิตย์" in text:
                days = ["อาทิตย์"]
            elif "เสาร์" in text:
                days = ["เสาร์"]
            else:
                days = ["ไม่ระบุ"]
        class_match = CLASS_RE.search(text)
        class_name = self.normalizer.normalize_class(class_match.group()) if class_match else None
        codes = _unique(CODE_RE.findall(text))
        times = _unique(TIME_RE.findall(text))
        period, point_time = None, (times[0] if times else None)
        if len(times) >= 2 and ("ถึง" in text or re.search(r"\d\s*-\s*\d", text)):
            period, point_time = f"{times[0]}-{times[1]}", None
        room = next(
            (known for known in self.rooms if known.casefold() in lower and known != class_name),
            None,
        )
        time_of_day = next(
            (value for term, value in TIME_OF_DAY_TERMS if term in text),
            None,
        )

        relative_day_label = None
        for terms, label in (
            (("วันนี้", "today"), "วันนี้"),
            (("พรุ่งนี้", "tomorrow"), "พรุ่งนี้"),
            (("เมื่อวาน", "yesterday"), "เมื่อวาน"),
        ):
            if any(term in original_lower for term in terms):
                relative_day_label = label
                break

        asks: list[str] = []
        # Vocational course tables commonly abbreviate theory, practice,
        # credits and total hours as ท-ป-น-ช. Treat the letters as complete
        # tokens so class names such as สท. and ทค. are never misread.
        def short_field(letter: str) -> bool:
            standalone = re.search(
                rf"(?<![ก-๙A-Za-z]){letter}(?![ก-๙A-Za-z])", text, re.I
            )
            # Also accept natural Thai typing without a space, such as
            # "ทเท่าไหร่", "ปกี่ชั่วโมง" and "ชั่วโมงทเท่าไหร่".
            attached_question = re.search(
                rf"(?:^|[\s.\-/]|ชั่วโมง|หน่วยกิต){letter}"
                rf"(?=(?:เท่า(?:ไหร่|ไร)|กี่|คือ|มี|$))",
                text,
                re.I,
            )
            return bool(standalone or attached_question)
        asks_tpnc = bool(re.search(
            r"(?<![ก-๙A-Za-z])ท\s*[.\-/]?\s*ป\s*[.\-/]?\s*น\s*[.\-/]?\s*ช(?![ก-๙A-Za-z])",
            text,
            re.I,
        ))
        asks_tpn = asks_tpnc or bool(re.search(
            r"(?<![ก-๙A-Za-z])ท\s*[.\-/]?\s*ป\s*[.\-/]?\s*น(?![ก-๙A-Za-z])",
            text,
            re.I,
        ))
        if asks_tpn:
            asks.extend(("theory_hr", "practice_hr", "credit"))
        else:
            if short_field("ท"):
                asks.append("theory_hr")
            if short_field("ป"):
                asks.append("practice_hr")
            if short_field("น"):
                asks.append("credit")
        if asks_tpnc or (not asks_tpn and short_field("ช")):
            asks.append("total_hr")
        if any(term in text for term in ("รหัสวิชา", "รหัสอะไร")):
            asks.append("subject_code")
        wants_name = any(term in text for term in ("ชื่อวิชา", "ชื่อเต็ม", "วิชาชื่ออะไร"))
        wants_name |= bool(re.search(r"วิชาที่(?:เรียน|สอน)(?:ชื่ออะไร|คืออะไร)|บอกวิชาที่(?:เรียน|สอน)", text))
        wants_schedule_subject = bool(re.search(
            r"(?<!ภาค)(?:เรียน|เรยน|สอน|วิชา)(?:อะ|อา)?(?:ไร|รัย|ราย)|"
            r"(?:เรียน|สอน)(?:เรื่อง|วิชา)(?:อะไร|ไหน)", text
        ))
        if wants_name or (wants_schedule_subject and "subject_code" not in asks) or re.search(r"(?<!รหัส)วิชา(?:อะไร|ไหน)", text):
            asks.append("subject")
        if any(term in text for term in ("กี่โมง", "เวลาไหน", "เวลาอะไร", "ช่วงเวลา", "กี่ช่วง", "ขอเวลา", "เวลาเรียนตรงไหน")):
            asks.append("time")
        if any(term in text for term in ("ห้องไหน", "ห้องอะไร", "ที่ไหน", "สถานที่", "ใช้ห้อง", "เรียนห้อง", "สอนตรงไหน", "บอกห้อง")):
            asks.append("room")
        if "เวลากับห้อง" in text or "ห้องกับเวลา" in text:
            asks.extend(("time", "room"))
        if any(term in text for term in ("กลุ่มไหน", "กลุ่มอะไร", "ห้องเรียนไหน")) and "รวมกับกลุ่มไหน" not in text:
            asks.append("class")
        if any(term in text for term in ("กี่คน", "นักเรียนกี่")) or re.search(r"จ(?:ำ|ํา)นวน(?:นักเรียน|เด็ก)", text):
            asks.append("student_count")
        if "หน่วยกิต" in text:
            asks.append("credit")
        if "ทฤษฎี" in text and "ชั่วโมง" in text:
            asks.append("theory_hr")
        if "ปฏิบัติ" in text and "ชั่วโมง" in text:
            asks.append("practice_hr")
        if "ชั่วโมง" in text and not {"theory_hr", "practice_hr"}.intersection(asks):
            asks.append("total_hr")
        if any(term in text for term in ("ชื่ออาจารย์", "อาจารย์ชื่อ", "ใครเป็นอาจารย์", "ครูชื่อ")):
            asks.append("teacher")
        if "แผนก" in text:
            asks.append("department")
        if any(term in text for term in ("คาบรวม", "เรียนรวมกับ", "เรียนรวมไหม", "รวมกับ", "รวมกลุ่ม")):
            asks.append("combined_class")
        if any(term in text for term in ("ประเภทคาบ", "คาบประเภท", "เป็นคาบอะไร")) or re.search(r"คาบ.{0,8}ทฤษฎีหรือปฏิบัติ", text):
            asks.append("lesson_type")
        if "ออนไลน์" in text:
            asks.append("online")
        if any(term in text for term in ("คาบอะไรบ้าง", "มีเรียนอะไรบ้าง")):
            asks.extend(("time", "subject", "room", "class"))
        if "และ" in text or "แล้วก็" in text or re.search(r"ขอ(?!ง)", text):
            if "วิชา" in text and "subject" not in asks and "subject_code" not in asks:
                asks.append("subject")
            if "เวลา" in text and not re.search(r"เวลา\s*\d{1,2}:\d{2}", text):
                asks.append("time")
            if "ห้อง" in text and not ("ในห้อง" in text and "student_count" in asks):
                asks.append("room")
            if "นักเรียน" in text:
                asks.append("student_count")

        schedule_language = bool(re.search(r"(?<!โรง)เรียน", text)) or any(
            term in text for term in ("สอน", "คาบ", "เวลา", "ห้อง", "กี่โมง", "วันไหน", "ที่ไหน")
        )
        subject_list = bool(
            re.search(r"(?:เรียน)?วิชาอะไรบ้าง|มีวิชาอะไรบ้าง|รายชื่อวิชา", text)
        )
        schedule_overview = not subject_list and any(
            term in text for term in ("มีเรียนอะไร", "เรียนอะไรบ้าง", "มีสอนอะไร")
        )
        yes_no = "ไหม" in text and schedule_language
        query_mode = self._query_mode(text, days, class_name, codes, times)
        if query_mode == "schedule_count":
            asks = [ask for ask in asks if ask != "time"]
        if query_mode == "full_schedule":
            asks = []
        if query_mode in {"weekly_hours", "schedule_count"} and any(
            term in text for term in ("ทั้งอาทิตย์", "อาทิตย์หนึ่ง", "1 อาทิตย์", "ทั้งสัปดาห์")
        ):
            days = []
            unknown_day = False
        parsed = {
            "day": days[0] if days else None,
            "time": point_time,
            "period": period,
            "class": class_name,
            "subject_code": codes[0] if codes else None,
            "room": room,
            "time_of_day": time_of_day,
            "asks": [ask for ask in _unique(asks) if ask in SUPPORTED_ASKS],
            "_normalized": text,
            "_unknown_day": unknown_day,
            "_asks_day": "วันไหน" in text,
            "_aggregate": (
                "รวม" in text or "ทั้งหมด" in text
                or bool(re.search(r"รหัสวิชา.{0,12}(?:มี)?อะไรบ้าง", text))
            ),
            "_per_period": "แต่ละคาบ" in text,
            "_asks_end": "ถึงกี่โมง" in text,
            "_asks_tpn": asks_tpn,
            "_asks_tpnc": asks_tpnc,
            "_info_key": self._info_key(text),
            "_schedule_language": schedule_language,
            "query_mode": query_mode,
            "_query_mode": query_mode if query_mode != "schedule_detail" else ("subject_list" if subject_list else ("overview" if schedule_overview else None)),
            "_yes_no": yes_no,
            "_relative_day_label": relative_day_label,
        }
        # Topic words can contain schedule-looking words (e.g. "โรงเรียน") or
        # time/day expressions without asking about the teaching schedule.
        if OUT_OF_SCOPE_TOPIC_RE.search(original_lower) and not class_name and not codes:
            parsed.update({
                "day": None, "time": None, "period": None, "room": None,
                "time_of_day": None, "asks": [], "_unknown_day": False,
                "_asks_day": False, "_info_key": None, "_schedule_language": False,
                "query_mode": "schedule_detail", "_query_mode": None,
                "_yes_no": False, "_relative_day_label": None,
            })
        if re.search(r"ค[าอ]บ(?:ต่อไป|ถัดไป|หน้า)", original_lower):
            now = normalizer._server_now()
            parsed["_next_period"] = True
            parsed["_after_time"] = parsed.get("time") or now.strftime("%H:%M")
            if not parsed.get("day"):
                parsed["day"] = normalizer.WEEKDAY_NAMES[now.weekday()]
                parsed["_relative_day_label"] = "วันนี้"
            parsed.update({"time": None, "period": None, "query_mode": "schedule_detail",
                           "_query_mode": None, "_yes_no": False, "_asks_day": False,
                           "_schedule_language": True, "asks": ["time", "subject", "room", "class"]})
        if re.search(r"ค[าอ]บ(?:แรก|ที่\s*1|หนึ่ง)", original_lower):
            now = normalizer._server_now()
            parsed["_first_period"] = True
            if not parsed.get("day"):
                parsed["day"] = normalizer.WEEKDAY_NAMES[now.weekday()]
                parsed["_relative_day_label"] = "วันนี้"
            parsed.update({"time": None, "period": None, "query_mode": "schedule_detail",
                           "_query_mode": None, "_yes_no": False, "_asks_day": False,
                           "_schedule_language": True, "asks": ["time", "subject", "room", "class"]})
        return parsed

    @staticmethod
    def _query_mode(text: str, days: list[str], class_name: str | None,
                    codes: list[str], times: list[str]) -> str:
        scoped = bool(class_name or codes or times)
        if not scoped and any(term in text for term in (
            "ตารางสอนของใคร", "ตารางนี้ของใคร", "นี่ตารางใคร",
            "ตารางนี้ของอาจารย์คนไหน", "ใครเป็นเจ้าของตารางนี้",
            "อาจารย์ชื่ออะไร", "ชื่ออาจารย์", "ใครสอน",
        )):
            return "teacher_info"
        if not scoped and any(term in text for term in (
            "สอนกี่ชั่วโมงต่อสัปดาห์", "สอนกี่ชั่วโมง", "อาทิตย์หนึ่งสอนกี่",
            "1 อาทิตย์สอนกี่", "ชั่วโมงสอนรวม", "รวมทั้งสัปดาห์กี่ชั่วโมง",
            "รวมเวลาสอนทุกวัน", "อาทิตย์นึงอาจารย์มีชั่วโมงสอน",
        )):
            return "weekly_hours"
        if not scoped and not days and re.search(r"(?:ตารางนี้เป็นของครู|คนที่สอน.*ตารางนี้.*ชื่ออะไร)", text):
            return "teacher_info"
        if not scoped and not days and any(term in text for term in (
            "สอนวันไหนบ้าง", "มีสอนวันอะไร", "วันไหนมีเรียน",
            "สอนกี่วัน", "มีเรียนกี่วันต่อสัปดาห์",
        )):
            return "days_summary"
        if not scoped and not days and any(term in text for term in (
            "ขอตารางทั้งหมด", "ตารางสอนทั้งหมด", "สอนอะไรบ้างทั้งอาทิตย์",
            "ขอดูตารางทั้งสัปดาห์",
        )):
            return "full_schedule"
        if not scoped and not days and (
            "ทั้งสัปดาห์" in text or "ครบทุกวัน" in text
        ) and any(term in text for term in ("ตาราง", "คาบ", "รายการสอน")) and not any(
            term in text for term in ("กี่คาบ", "กี่ช่วง")
        ):
            return "full_schedule"
        if not scoped and not days and any(term in text for term in (
            "สอนวิชาอะไรบ้าง", "มีวิชาอะไร", "สอนกี่วิชา", "รายวิชาที่สอน",
        )):
            return "subject_summary"
        if not scoped and not days and any(term in text for term in (
            "สอนห้องไหนบ้าง", "สอนกลุ่มไหน", "มีเด็กกลุ่มอะไรบ้าง",
            "สอน สท", "สอน ทค",
        )):
            return "class_summary"
        if not scoped and not days and any(term in text for term in (
            "สอนที่ไหนบ้าง", "ใช้ห้องอะไร", "มีสอนที่สถานประกอบการไหม",
            "มีออนไลน์ไหม",
        )):
            return "room_summary"
        if not scoped and ("กี่ช่วง" in text or "กี่คาบ" in text) and any(
            term in text for term in ("ทั้งอาทิตย์", "ทั้งสัปดาห์", "จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์")
        ):
            return "schedule_count"
        return "schedule_detail"

    @staticmethod
    def _info_key(text: str) -> str | None:
        if any(term in text for term in ("ภาคเรียน", "เทอม")):
            return "semester"
        if any(term in text for term in ("วิทยาลัยไหน", "วิทยาลัยอะไร", "สังกัดวิทยาลัย")):
            return "college"
        if any(term in text for term in ("วุฒิ", "การศึกษา")):
            return "education"
        if "หน้าที่พิเศษ" in text:
            return "special_duty"
        if "กี่สัปดาห์" in text:
            return "total_weeks"
        if "ช่วงสัปดาห์" in text:
            return "week_range"
        return None


def parse_question(question: str, data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    """Return normalized entities and requested fields for *question*."""
    return QuestionParser(data_dir).parse(question)
