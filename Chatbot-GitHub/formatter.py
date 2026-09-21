"""Format retrieval results using only fields requested by the user."""
from __future__ import annotations

from typing import Any

DAY_ORDER = ("จันทร์", "อังคาร", "พุธ", "พฤหัสฯ", "ศุกร์")


class AnswerFormatter:
    def __init__(self, rules: dict[str, Any]) -> None:
        self.not_found = rules["source_policy"]["out_of_scope_response"]
        self.no_sum = rules["student_count_policy"]["cross_period_sum_response"]

    @staticmethod
    def _dedupe(lines: list[str]) -> list[str]:
        return list(dict.fromkeys(line for line in lines if line))

    def _format_info(self, result: dict[str, Any]) -> str:
        asks, info = result["parsed"]["asks"], result["info"]
        if result["parsed"].get("query_mode") == "teacher_info":
            return (f"ตารางสอนนี้เป็นของ {info['teacher_name']}\n"
                    f"แผนก{info['department']}\nภาคเรียน {info['semester']} ครับ")
        lines = []
        if "teacher" in asks:
            lines.append(f"อาจารย์ชื่อ {info['teacher_name']}")
        if "department" in asks:
            lines.append(f"แผนก {info['department']}")
        info_key = result["parsed"].get("_info_key")
        labels = {
            "semester": "ภาคเรียน", "college": "วิทยาลัย", "education": "วุฒิการศึกษา",
            "special_duty": "หน้าที่พิเศษ", "total_weeks": "จำนวนสัปดาห์",
            "week_range": "ช่วงสัปดาห์",
        }
        if info_key:
            value = info[info_key]
            suffix = " สัปดาห์" if info_key == "total_weeks" else ""
            lines.append(f"{labels[info_key]} {value}{suffix}")
        return "\n".join(lines) if lines else self.not_found

    @staticmethod
    def _hours(minutes: int) -> str:
        hours, remainder = divmod(minutes, 60)
        return str(hours) if not remainder else f"{hours} ชั่วโมง {remainder} นาที"

    def _format_summary(self, result: dict[str, Any]) -> str:
        parsed, rows = result["parsed"], result["rows"]
        mode = parsed["query_mode"]
        if mode == "weekly_hours":
            minutes = result["minutes"]
            duration = (f"{minutes // 60} ชั่วโมง" if minutes % 60 == 0
                        else self._hours(minutes))
            return f"จากตารางสอน อาจารย์มีเวลาสอนรวม {duration} ต่อสัปดาห์ครับ"
        if mode == "days_summary":
            days = [day for day in DAY_ORDER if any(row["day"] == day for row in rows)]
            if not days:
                return self.not_found
            if len(days) == 1:
                return f"อาจารย์มีสอน 1 วัน คือวัน{days[0]}ครับ"
            day_text = ", ".join(f"วัน{day}" for day in days[:-1])
            return f"อาจารย์มีสอนทั้งหมด {len(days)} วัน ได้แก่ {day_text} และวัน{days[-1]}ครับ"
        if mode == "schedule_count":
            day = parsed.get("day")
            scope = f"วัน{day}" if day else "ทั้งสัปดาห์"
            return f"{scope} มีทั้งหมด {len(rows)} ช่วงสอนครับ"
        if mode == "room_summary" and "ไหม" in parsed["_normalized"]:
            target = "ออนไลน์" if "ออนไลน์" in parsed["_normalized"] else "สถานประกอบการ"
            found = any(row["room"] == target for row in rows)
            return f"{'มี' if found else 'ไม่มี'}สอนที่{target}ในตารางครับ"
        if not rows:
            return self.not_found
        if mode == "subject_summary":
            subjects = list(dict.fromkeys((row["code"], row["subject"]["name"]) for row in rows))
            return f"อาจารย์สอนทั้งหมด {len(subjects)} วิชา ได้แก่\n" + "\n".join(
                f"• {name} ({code})" for code, name in subjects
            )
        if mode == "class_summary":
            classes = list(dict.fromkeys(row["class"] for row in rows))
            return f"อาจารย์สอนทั้งหมด {len(classes)} กลุ่ม ได้แก่ " + ", ".join(classes) + " ครับ"
        if mode == "room_summary":
            rooms = list(dict.fromkeys(row["room"] for row in rows))
            return "สถานที่ที่ใช้สอน ได้แก่ " + ", ".join(rooms) + " ครับ"
        if mode == "full_schedule":
            sections = []
            for day in DAY_ORDER:
                day_rows = sorted((row for row in rows if row["day"] == day), key=lambda row: (row["start_time"], row["end_time"]))
                if day_rows:
                    lines = [f"วัน{day} — {len(day_rows)} ช่วงสอน"]
                    for number, row in enumerate(day_rows, start=1):
                        lines.append(
                            f"{number}. {row['start_time']} - {row['end_time']} น.\n"
                            f"   {row['subject']['name']} ({row['code']})\n"
                            f"   กลุ่ม {row['class']} · ห้อง {row['room']}"
                        )
                    sections.append("\n".join(lines))
            return "\n\n".join(sections)
        return self.not_found

    def _format_subject_metadata(self, result: dict[str, Any]) -> str:
        parsed, subjects = result["parsed"], result.get("subjects", [])
        asks = parsed["asks"]
        if not subjects:
            # A metadata question without a subject scope asks for the total,
            # e.g. "มีชั่วโมง ท เท่าไหร่". An explicit unknown subject code
            # must still return not-found instead of silently showing totals.
            if not parsed["_aggregate"] and parsed.get("subject_code"):
                return self.not_found
            totals = result["totals"]
            lines = []
            if "credit" in asks:
                lines.append(f"หน่วยกิตรวมทั้งหมด {totals['credit']} หน่วยกิต")
            if "theory_hr" in asks:
                lines.append(f"ชั่วโมงทฤษฎีรวมทั้งหมด {totals['theory_hr']} ชั่วโมง")
            if "practice_hr" in asks:
                lines.append(f"ชั่วโมงปฏิบัติรวมทั้งหมด {totals['practice_hr']} ชั่วโมง")
            if "total_hr" in asks:
                lines.append(f"ชั่วโมงรวมทั้งหมด {totals['total_hr_per_week']} ชั่วโมง")
            return "\n".join(lines) if lines else self.not_found
        if parsed.get("_asks_tpn"):
            blocks = []
            for subject in subjects:
                short_values = (
                    f"{subject['theory_hr']}–{subject['practice_hr']}–{subject['credit']}"
                )
                detail = (
                    f"ทฤษฎี {subject['theory_hr']} ชั่วโมง · "
                    f"ปฏิบัติ {subject['practice_hr']} ชั่วโมง · "
                    f"{subject['credit']} หน่วยกิต"
                )
                if parsed.get("_asks_tpnc"):
                    short_values += f"–{subject['total_hr_per_week']}"
                    detail += f" · ชั่วโมงรวม {subject['total_hr_per_week']} ชั่วโมง"
                short_label = "ท–ป–น–ช" if parsed.get("_asks_tpnc") else "ท–ป–น"
                blocks.append("\n".join((
                    f"{subject['name']} ({subject['code']})",
                    f"{short_label}: {short_values}",
                    detail,
                )))
            return "\n\n".join(blocks)
        if len(subjects) > 1 and set(asks) == {"subject_code"}:
            return "รหัสวิชาทั้งหมด ได้แก่\n" + "\n".join(
                f"• {subject['code']} — {subject['name']}" for subject in subjects
            )
        lines = []
        for subject in subjects:
            fields = []
            if "subject" in asks:
                fields.append(f"วิชา {subject['name']}")
            if "subject_code" in asks:
                fields.append(f"รหัสวิชา {subject['code']}")
            if "credit" in asks:
                fields.append(f"{subject['credit']} หน่วยกิต")
            if "theory_hr" in asks:
                fields.append(f"ทฤษฎี {subject['theory_hr']} ชั่วโมง")
            if "practice_hr" in asks:
                fields.append(f"ปฏิบัติ {subject['practice_hr']} ชั่วโมง")
            if "total_hr" in asks:
                fields.append(f"ชั่วโมงรวมต่อสัปดาห์ {subject['total_hr_per_week']} ชั่วโมง")
            lines.append("\n".join(fields))
        return "\n".join(lines) if lines else self.not_found

    def _format_combined(self, row: dict[str, Any], asks: list[str]) -> str:
        query_class, others = row.get("query_class"), row.get("combined_with", [])
        if query_class and others:
            lines = [f"{query_class} มีคาบเรียนรวมกับ {', '.join(others)} ครับ"]
        else:
            lines = [f"คาบเรียนรวม: {row['class']}"]
        if "subject" in asks:
            lines.append(f"วิชา: {row['subject']['name']}")
        if "subject_code" in asks:
            lines.append(f"รหัสวิชา: {row['code']}")
        if "time" in asks:
            lines.append(f"วัน{row['day']} เวลา {row['start_time']} - {row['end_time']} น.")
        if "room" in asks:
            lines.append(f"ห้อง: {row['room']}")
        if "student_count" in asks:
            lines.append(f"จำนวนนักเรียน: {row['student_count']} คน")
        if "class" in asks:
            lines.append(f"กลุ่มรวม: {row['class']}")
        if row.get("lesson_type") and ("lesson_type" in asks or "combined_class" in asks):
            lines.append(f"รูปแบบ: {row['lesson_type']}")
        if "online" in asks and row.get("type"):
            lines.append(f"รูปแบบ: {row['type']}")
        return "\n".join(lines)

    @staticmethod
    def _format_merged_period(row: dict[str, Any], number: int) -> str:
        """Render one row in a merged subject + combined-class response."""
        lines = [
            f"คาบที่ {number}",
            f"เวลา: {row['start_time']} - {row['end_time']} น.",
        ]
        if row.get("is_combined"):
            groups = row.get("combined_groups", [])
            if groups:
                lines.append(f"เป็นคาบเรียนรวม {' และ '.join(groups)}")
            else:
                lines.append(f"เป็นคาบเรียนรวม {row['class']}")
        lines.append(f"วิชา: {row['subject']['name']}")
        if row.get("lesson_type"):
            lines.append(f"รูปแบบ: {row['lesson_type']}")
        if row.get("type"):
            lines.append(f"รูปแบบ: {row['type']}")
        lines.append(f"ห้อง: {row['room']}")
        return "\n".join(lines)

    @staticmethod
    def _schedule_header(parsed: dict[str, Any], row_count: int) -> str | None:
        """Create a short conversational summary for scoped schedule results."""
        class_name, day = parsed.get("class"), parsed.get("day")
        if class_name:
            day_text = f" วัน{day}" if day else ""
            return f"{class_name}{day_text} มีเรียน {row_count} ช่วงครับ"
        if not parsed["asks"] and day:
            return f"วัน{day} มีเรียน {row_count} ช่วงครับ"
        return None

    @staticmethod
    def _period_block(
        row: dict[str, Any], asks: list[str], default_fields: bool, show_day: bool = False
    ) -> str:
        """Format one period as a readable multi-line block, never a pipe row."""
        lines: list[str] = []
        if show_day:
            lines.append(f"วัน{row['day']}")
        if "time" in asks:
            lines.append(f"{row['start_time']} - {row['end_time']} น.")
        if "subject" in asks:
            lines.append(f"วิชา: {row['subject']['name']}")
        if "subject_code" in asks:
            lines.append(f"รหัสวิชา: {row['code']}")
        if "room" in asks:
            lines.append(f"ห้อง: {row['room']}")
        if "class" in asks and not row.get("is_combined"):
            lines.append(f"กลุ่ม: {row['class']}")
        if "student_count" in asks:
            if not lines or ("time" not in asks and default_fields is False):
                lines.append(f"คาบ {row['start_time']} - {row['end_time']} น.")
            lines.append(f"จำนวนนักเรียน: {row['student_count']} คน")
        if row.get("is_combined") and (default_fields or "class" in asks):
            others = row.get("combined_with", [])
            relation = f" (รวมกับ {', '.join(others)})" if others else ""
            lines.append(f"คาบเรียนรวม: {row['class']}{relation}")
        if "lesson_type" in asks or (default_fields and row.get("lesson_type")):
            lines.append(f"รูปแบบ: {row['lesson_type']}")
        if "online" in asks or (default_fields and row.get("type")):
            lines.append(f"รูปแบบ: {row['type']}")
        return "\n".join(lines)

    @staticmethod
    def _day_phrase(parsed: dict[str, Any]) -> str:
        relative = parsed.get("_relative_day_label")
        if relative:
            return relative
        day = parsed.get("day")
        return f"วัน{day}" if day else ""

    def _format_overview(self, parsed: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        day_phrase = self._day_phrase(parsed)
        subject = f"{day_phrase}มีเรียน" if day_phrase else "มีเรียน"
        header = f"{subject} {len(rows)} ช่วงครับ"
        blocks = []
        for number, row in enumerate(rows, start=1):
            blocks.append("\n".join((
                f"คาบที่ {number}",
                f"เวลา: {row['start_time']} - {row['end_time']} น.",
                f"วิชา: {row['subject']['name']}",
                f"กลุ่ม: {row['class']}",
                f"ห้อง: {row['room']}",
            )))
        return f"{header}\n\n" + "\n\n".join(blocks)

    def _format_subject_list(self, rows: list[dict[str, Any]]) -> str:
        names = self._dedupe([row["subject"]["name"] for row in rows])
        if not names:
            return self.not_found
        if len(names) == 1:
            return f"วิชา: {names[0]}"
        return "วิชาที่เรียน:\n" + "\n".join(f"- {name}" for name in names)

    def _format_no_schedule(self, parsed: dict[str, Any]) -> str:
        day_phrase = self._day_phrase(parsed)
        prefix = day_phrase or "ช่วงเวลานี้"
        asks = set(parsed["asks"])
        if "online" in asks:
            return f"{prefix}ไม่มีคาบเรียนออนไลน์ในตารางสอนครับ"
        if "combined_class" in asks:
            return f"{prefix}ไม่มีคาบเรียนรวมในตารางสอนครับ"
        return f"{prefix}ไม่มีคาบเรียนในตารางสอนครับ"

    def _format_yes_no(self, parsed: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        day_phrase = self._day_phrase(parsed)
        asks = set(parsed["asks"])
        activity = "มีเรียนออนไลน์" if "online" in asks else "มีเรียน"
        subject = f"{day_phrase}{activity}" if day_phrase else activity
        header = f"มีครับ {subject}"
        blocks = []
        for row in rows:
            blocks.append("\n".join((
                f"เวลา: {row['start_time']} - {row['end_time']} น.",
                f"วิชา: {row['subject']['name']}",
                f"กลุ่ม: {row['class']}",
            )))
        return f"{header}\n" + "\n\n".join(blocks)

    def _format_day_answer(self, parsed: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        """Answer a day-of-week question with unique days and compact periods."""
        days = self._dedupe([row["day"] for row in rows])
        subjects = self._dedupe([row["subject"]["name"] for row in rows])
        subject_text = ", ".join(subjects)
        if len(days) == 1:
            day_text = f"วัน{days[0]}"
            header = f"{subject_text} สอน{day_text}ครับ" if subject_text else f"มีเรียน{day_text}ครับ"
            day_rows = rows
            if len(day_rows) == 1:
                row = day_rows[0]
                details = [f"เวลา: {row['start_time']} - {row['end_time']} น."]
                if row.get("lesson_type"):
                    details.append(f"รูปแบบ: {row['lesson_type']}")
                if "room" in parsed["asks"]:
                    details.append(f"ห้อง: {row['room']}")
                if row.get("type"):
                    details.append(f"รูปแบบ: {row['type']}")
                return header + "\n" + "\n".join(details)
            lines = [header, f"มี {len(day_rows)} ช่วง:"]
            for row in day_rows:
                detail = f"• {row['start_time']} - {row['end_time']} น."
                if row.get("lesson_type"):
                    detail += f" {row['lesson_type']}"
                if row.get("type"):
                    detail += f" ({row['type']})"
                if "room" in parsed["asks"]:
                    detail += f" ห้อง: {row['room']}"
                lines.append(detail)
            return "\n".join(lines)

        day_text = ", ".join(f"วัน{day}" for day in days)
        return f"{subject_text} สอน{day_text}ครับ" if subject_text else f"มีเรียน{day_text}ครับ"

    def _format_class_day_periods(self, parsed: dict[str, Any], rows: list[dict[str, Any]]) -> str:
        """Keep each physical period visible for a class/day query."""
        header = f"{parsed['class']} วัน{parsed['day']} มีเรียน {len(rows)} ช่วงครับ"
        blocks = []
        for number, row in enumerate(rows, start=1):
            lines = [
                f"คาบที่ {number}",
                f"เวลา: {row['start_time']} - {row['end_time']} น.",
                f"วิชา: {row['subject']['name']}",
                f"รูปแบบ: {row['lesson_type']}",
                f"ห้อง: {row['room']}",
            ]
            if row.get("is_combined"):
                groups = row.get("combined_groups", [])
                if groups:
                    lines.append(
                        f"เป็นคาบเรียนรวม {' และ '.join(groups)} (คาบเรียนรวม: {row['class']})"
                    )
                else:
                    lines.append(f"เป็นคาบเรียนรวม: {row['class']}")
            blocks.append("\n".join(lines))
        return header + "\n\n" + "\n\n".join(blocks)

    def _format_schedule(self, result: dict[str, Any]) -> str:
        parsed, rows = result["parsed"], result.get("rows", [])
        if not rows:
            if parsed.get("_next_period"):
                label = parsed.get("_relative_day_label") or f"วัน{parsed['day']}"
                return f"{label}ไม่มีคาบเรียนต่อไปแล้วครับ"
            if parsed.get("_yes_no"):
                return self._format_no_schedule(parsed)
            return self.not_found
        asks = list(parsed["asks"])
        if parsed.get("_yes_no") and "combined_class" not in asks:
            return self._format_yes_no(parsed, rows)
        if parsed.get("_asks_day"):
            return self._format_day_answer(parsed, rows)
        if parsed.get("_query_mode") == "overview":
            return self._format_overview(parsed, rows)
        if parsed.get("_query_mode") == "subject_list":
            return self._format_subject_list(rows)
        if parsed.get("class") and parsed.get("day") and len(rows) > 1:
            return self._format_class_day_periods(parsed, rows)
        if "student_count" in asks and parsed["_aggregate"] and len(rows) > 1:
            return self.no_sum
        merge_combined_schedule = "combined_class" in asks and "subject" in asks
        if merge_combined_schedule:
            header = self._schedule_header(parsed, len(rows))
            blocks = [
                self._format_merged_period(row, number)
                for number, row in enumerate(rows, start=1)
            ]
            body = "\n\n".join(blocks)
            return f"{header}\n\n{body}" if header else body
        if "combined_class" in asks:
            return "\n\n".join(self._format_combined(row, asks) for row in rows)
        default_fields = not asks
        if default_fields:
            asks = ["time", "subject", "room", "class"]
        output: list[str] = []
        for row in rows:
            if parsed.get("_asks_end"):
                output.append(f"ถึง {row['end_time']} น.")
                continue
            if parsed["_asks_day"] and "time" not in asks:
                asks = ["time", *asks]
            output.append(
                self._period_block(row, asks, default_fields, show_day=parsed["_asks_day"])
            )
        blocks = self._dedupe(output)
        if not blocks:
            return self.not_found
        header = self._schedule_header(parsed, len(rows))
        body = "\n\n".join(blocks)
        return f"{header}\n\n{body}" if header else body

    def format(self, result: dict[str, Any]) -> str:
        parsed = result.get("parsed", {})
        if (
            parsed.get("_relative_day_label") == "วันนี้"
            and parsed.get("day") in {"เสาร์", "อาทิตย์"}
            and result.get("kind") not in {"info", "subject_metadata", "totals"}
        ):
            return "วันนี้ไม่มีคาบเรียนในตารางสอนครับ"
        kind = result.get("kind")
        if kind == "summary":
            return self._format_summary(result)
        if kind == "info":
            return self._format_info(result)
        if kind in {"subject_metadata", "totals"}:
            return self._format_subject_metadata(result)
        if kind == "schedule":
            return self._format_schedule(result)
        return self.not_found


def format_answer(result: dict[str, Any], rules: dict[str, Any]) -> str:
    return AnswerFormatter(rules).format(result)
