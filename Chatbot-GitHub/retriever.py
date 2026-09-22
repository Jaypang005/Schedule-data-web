"""Data loading, schema validation, and deterministic schedule retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from normalizer import DEFAULT_DATA_DIR


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


TIME_OF_DAY_RANGES: dict[str, tuple[int, int | None]] = {
    "morning": (5 * 60, 12 * 60),
    "noon": (12 * 60, 13 * 60),
    "afternoon": (12 * 60, 17 * 60),
    "evening": (17 * 60, 19 * 60),
    "night": (19 * 60, None),
}


def _matches_time_of_day(start_time: str, time_of_day: str | None) -> bool:
    if not time_of_day:
        return True
    start, end = TIME_OF_DAY_RANGES[time_of_day]
    value = _minutes(start_time)
    return value >= start and (end is None or value < end)


class ScheduleRetriever:
    REQUIRED_FILES = ("teacher_subjects.json", "schedule.json", "classes.json", "rules.json")

    def __init__(self, data_dir: str | Path = DEFAULT_DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        docs = {name: _load(self.data_dir / name) for name in self.REQUIRED_FILES}
        self.teacher_data = docs["teacher_subjects.json"]
        self.schedule_data = docs["schedule.json"]
        self.classes_data = docs["classes.json"]
        self.rules = docs["rules.json"]
        self._validate()
        self.subjects = self.teacher_data["subjects"]
        self.subject_by_code = {item["code"]: item for item in self.subjects}
        self.schedule = self.schedule_data["schedule"]
        self.membership = self.classes_data["combined_membership"]

    def _validate(self) -> None:
        if not isinstance(self.teacher_data.get("info"), dict):
            raise ValueError("teacher_subjects.json: info must be an object")
        if not isinstance(self.teacher_data.get("subjects"), list):
            raise ValueError("teacher_subjects.json: subjects must be an array")
        if not isinstance(self.teacher_data.get("totals"), dict):
            raise ValueError("teacher_subjects.json: totals must be an object")
        subject_fields = {"code", "name", "theory_hr", "practice_hr", "credit", "total_hr_per_week"}
        if any(not isinstance(item, dict) or not subject_fields.issubset(item) for item in self.teacher_data["subjects"]):
            raise ValueError("teacher_subjects.json: invalid subject entry")
        period_fields = {"day", "start_time", "end_time", "code", "student_count", "room", "class"}
        if not isinstance(self.schedule_data.get("schedule"), list) or any(
            not isinstance(item, dict) or not period_fields.issubset(item)
            for item in self.schedule_data.get("schedule", [])
        ):
            raise ValueError("schedule.json: invalid schedule schema")
        if not isinstance(self.classes_data.get("classes"), list) or not isinstance(
            self.classes_data.get("combined_membership"), dict
        ):
            raise ValueError("classes.json: invalid classes schema")
        for key in ("source_policy", "student_count_policy", "day_aliases", "subject_aliases"):
            if not isinstance(self.rules.get(key), dict):
                raise ValueError(f"rules.json: {key} must be an object")
        if self.rules["student_count_policy"].get("allow_cross_period_sum") is not False:
            raise ValueError("rules.json must forbid cross-period student sums")

    def _combined_keys(self, class_name: str | None) -> set[tuple[str, ...]]:
        if not class_name:
            return set()
        return {
            (ref["day"], ref["start_time"], ref["end_time"], ref["code"], ref["room"], ref["combined_class"])
            for ref in self.membership.get(class_name, [])
        }

    @staticmethod
    def _row_key(row: dict[str, Any]) -> tuple[str, ...]:
        """Identity of one physical schedule period, used for union/dedup."""
        return (
            row["day"], row["start_time"], row["end_time"],
            row["code"], row["room"], row["class"],
        )

    def _class_row_keys(self, class_name: str | None) -> set[tuple[str, ...]] | None:
        """Union direct class rows with verified combined-membership rows."""
        if not class_name:
            return None
        direct = {
            self._row_key(row) for row in self.schedule if row["class"] == class_name
        }
        return direct | self._combined_keys(class_name)

    def _metadata_result(self, parsed: dict[str, Any]) -> dict[str, Any] | None:
        asks = set(parsed["asks"])
        info_asks = asks.intersection({"teacher", "department"})
        subject_asks = asks.intersection({"subject", "subject_code", "credit", "theory_hr", "practice_hr", "total_hr"})
        has_schedule_request = bool(
            parsed["day"] or parsed["time"] or parsed["period"] or parsed["class"] or parsed["room"]
            or parsed["_asks_day"]
            or asks.intersection({"time", "room", "class", "student_count", "combined_class", "lesson_type", "online"})
            or (parsed["_schedule_language"] and asks.intersection({"subject", "subject_code"}))
        )
        if info_asks or parsed.get("_info_key"):
            return {"kind": "info", "parsed": parsed, "info": self.teacher_data["info"]}
        if subject_asks and not has_schedule_request:
            code = parsed["subject_code"]
            if code in self.subject_by_code:
                subjects = [self.subject_by_code[code]]
            elif parsed.get("_asks_tpn") or (
                parsed.get("_aggregate") and subject_asks == {"subject_code"}
            ):
                subjects = self.subjects
            else:
                subjects = []
            return {
                "kind": "subject_metadata" if subjects else "totals",
                "parsed": parsed,
                "subjects": subjects,
                "totals": self.teacher_data["totals"],
            }
        return None

    def retrieve(self, parsed: dict[str, Any]) -> dict[str, Any]:
        mode = parsed.get("query_mode", "schedule_detail")
        if mode == "teacher_info":
            return {"kind": "info", "parsed": parsed, "info": self.teacher_data["info"]}
        summary_modes = {
            "weekly_hours", "days_summary", "full_schedule", "subject_summary",
            "class_summary", "room_summary", "schedule_count",
        }
        if mode in summary_modes:
            source_rows = _load(self.data_dir / "schedule.json")["schedule"] if mode == "weekly_hours" else self.schedule
            rows = [dict(row) for row in source_rows if not parsed["day"] or row["day"] == parsed["day"]]
            for row in rows:
                row["subject"] = self.subject_by_code[row["code"]]
            if mode == "weekly_hours":
                minutes = sum(_minutes(row["end_time"]) - _minutes(row["start_time"]) for row in rows)
                return {"kind": "summary", "parsed": parsed, "rows": rows, "minutes": minutes}
            return {"kind": "summary", "parsed": parsed, "rows": rows}
        metadata = self._metadata_result(parsed)
        if metadata is not None:
            return metadata
        asks = set(parsed["asks"])
        # "เรียนอะไร แล้วมีคาบรวมไหม" contains two intents about the same
        # class/day. Keep the full direct+combined union. A pure combined-class
        # question (optionally asking its time/room) still selects only that row.
        combined_only = "combined_class" in asks and "subject" not in asks
        membership_keys = self._combined_keys(parsed["class"])
        class_row_keys = self._class_row_keys(parsed["class"])
        rows: list[dict[str, Any]] = []
        seen_rows: set[tuple[str, ...]] = set()
        for source_row in self.schedule:
            row = dict(source_row)
            row_key = self._row_key(row)
            if parsed["day"] and row["day"] != parsed["day"]:
                continue
            if parsed["subject_code"] and row["code"] != parsed["subject_code"]:
                continue
            if parsed["room"] and row["room"] != parsed["room"]:
                continue
            via_membership = row_key in membership_keys
            if class_row_keys is not None and row_key not in class_row_keys:
                continue
            if not _matches_time_of_day(row["start_time"], parsed.get("time_of_day")):
                continue
            if parsed["period"]:
                start, end = parsed["period"][:5], parsed["period"][-5:]
                if row["start_time"] != start or row["end_time"] != end:
                    continue
            elif parsed["time"]:
                point = _minutes(parsed["time"])
                if not (_minutes(row["start_time"]) <= point < _minutes(row["end_time"])):
                    continue
            if "online" in parsed["asks"] and row.get("type") != "ออนไลน์":
                continue
            if "lesson_type" in parsed["asks"] and not row.get("lesson_type"):
                continue
            is_combined = bool(row.get("combined_class")) or via_membership
            if combined_only and not is_combined:
                continue
            row["subject"] = self.subject_by_code[row["code"]]
            row["matched_via_membership"] = via_membership
            row["is_combined"] = is_combined
            row["query_class"] = parsed["class"]
            if is_combined:
                row["combined_with"] = [
                    group for group in row.get("combined_groups", []) if group != parsed["class"]
                ]
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)
            rows.append(row)
        if parsed.get("_next_period"):
            rows = sorted(
                (row for row in rows if row["start_time"] > parsed["_after_time"]),
                key=lambda row: (row["start_time"], row["end_time"]),
            )[:1]
        if parsed.get("_first_period"):
            rows = sorted(rows, key=lambda row: (row["start_time"], row["end_time"]))[:1]
        # A schedule-looking word alone (for example "เรียน" or "คาบ") is not
        # enough to return every row. Require a real filter, requested field, or
        # a day that appears in genuine schedule language.
        recognized = bool(
            (parsed["day"] and parsed["_schedule_language"])
            or parsed["time"] or parsed["period"] or parsed["class"]
            or parsed["subject_code"] or parsed["room"] or parsed["asks"]
            or parsed.get("time_of_day")
            or parsed.get("_next_period") or parsed.get("_first_period")
        )
        return {"kind": "schedule" if recognized else "not_found", "parsed": parsed, "rows": rows}
