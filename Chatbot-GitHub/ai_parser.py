"""Optional local Qwen parser. It extracts a query, never schedule answers."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from normalizer import DEFAULT_DATA_DIR
from parser import SUPPORTED_ASKS


QUERY_FIELDS = (
    "day", "time", "time_of_day", "class", "subject_code", "room", "asks", "query_mode",
)
QUERY_MODES = {
    "schedule_detail", "teacher_info", "weekly_hours", "days_summary",
    "full_schedule", "subject_summary", "class_summary", "room_summary", "schedule_count",
}
TIME_OF_DAY = {"morning", "noon", "afternoon", "evening", "night"}
MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"
LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """คุณเป็น parser สำหรับคำถามตารางสอน ตอบเป็น JSON object เพียงหนึ่งชิ้น ไม่มี Markdown หรือคำอธิบาย
หน้าที่ของคุณคือตีความข้อความผู้ใช้เท่านั้น ห้ามตอบข้อมูลตารางสอน ห้ามเดาวัน เวลา ห้อง วิชา รหัสวิชา หรือกลุ่มเรียนที่ไม่ได้ระบุชัดเจน
ช่องที่ผู้ใช้ไม่ได้ระบุให้เป็น null และช่อง asks เป็น list ของชื่อ field เท่านั้น ห้ามมี object ของ question/answer
ใช้ keys เหล่านี้ครบและเท่านั้น: day, time, time_of_day, class, subject_code, room, asks, query_mode
asks ที่อนุญาต: {asks}
query_mode ที่อนุญาต: {modes}
time_of_day ที่อนุญาต: {times}
ถ้าคำถามไม่เกี่ยวกับตารางสอน ให้คืนทุกช่องเป็น null และ asks เป็น []
ตัวอย่างรูปแบบ: {{"day":null,"time":null,"time_of_day":null,"class":null,"subject_code":null,"room":null,"asks":[],"query_mode":null}}
ตัวอย่างคำถาม: จันนี้ สท 2/4 เรียนอะไร
ตัวอย่างผลลัพธ์: {{"day":"จันทร์","time":null,"time_of_day":null,"class":"สท.2/4","subject_code":null,"room":null,"asks":["subject"],"query_mode":"schedule_detail"}}
""".format(
    asks=", ".join(SUPPORTED_ASKS),
    modes=", ".join(sorted(QUERY_MODES)),
    times=", ".join(sorted(TIME_OF_DAY)),
)


@lru_cache(maxsize=1)
def _load_model() -> tuple[Any, Any, Any] | None:
    """Load model and tokenizer once, on the first low-confidence question."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME, torch_dtype="auto", device_map="auto", local_files_only=True
        )
        model.eval()
        return torch, tokenizer, model
    except Exception:
        LOGGER.exception("Local Qwen parser unavailable; using rule parser")
        return None


def _parse_json_response(response: str) -> dict[str, Any] | None:
    """Reject extra prose, keys, types, and unsupported structured intents."""
    try:
        candidate = json.loads(response.strip())
    except (TypeError, ValueError):
        return None
    if not _valid_shape(candidate):
        return None
    return candidate


def _valid_shape(candidate: Any) -> bool:
    if not isinstance(candidate, dict) or set(candidate) != set(QUERY_FIELDS):
        return False
    asks = candidate["asks"]
    if not isinstance(asks, list) or any(
        not isinstance(ask, str) or ask not in SUPPORTED_ASKS for ask in asks
    ) or len(asks) != len(set(asks)):
        return False
    if any(value is not None and not isinstance(value, str) for key, value in candidate.items() if key != "asks"):
        return False
    if candidate["query_mode"] is not None and candidate["query_mode"] not in QUERY_MODES:
        return False
    if candidate["time_of_day"] is not None and candidate["time_of_day"] not in TIME_OF_DAY:
        return False
    return True


def parse_with_ai(question: str) -> dict[str, Any] | None:
    """Return a validated structured query or None when local inference fails."""
    if not isinstance(question, str) or not question.strip():
        return None
    if os.environ.get("SCHEDULE_AI_ENABLED", "1") == "0":
        return None
    loaded = _load_model()
    if loaded is None:
        return None
    torch, tokenizer, model = loaded
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    try:
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=180, do_sample=False)
        generated = output[0][inputs["input_ids"].shape[1]:]
        response = tokenizer.decode(generated, skip_special_tokens=True)
        return _parse_json_response(response)
    except Exception:
        LOGGER.exception("Local Qwen inference failed; using rule parser")
        return None


@lru_cache(maxsize=4)
def _known_values(data_dir: Path) -> dict[str, set[str]]:
    def load(name: str) -> dict[str, Any]:
        with (data_dir / name).open(encoding="utf-8") as handle:
            return json.load(handle)

    rows = load("schedule.json")["schedule"]
    subjects = load("teacher_subjects.json")["subjects"]
    classes = load("classes.json")["classes"]
    return {
        "day": {row["day"] for row in rows},
        "time": {value for row in rows for value in (row["start_time"], row["end_time"])},
        "class": {item["class"] for item in classes},
        "subject_code": {item["code"] for item in subjects},
        "room": {row["room"] for row in rows},
    }


def validate_ai_query(candidate: Any, data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, Any] | None:
    """Accept only structured entities known to the local source JSON."""
    if not _valid_shape(candidate):
        return None
    known = _known_values(Path(data_dir).resolve())
    if any(candidate[field] is not None and candidate[field] not in allowed for field, allowed in known.items()):
        return None
    if not any(candidate[field] for field in QUERY_FIELDS if field != "asks") and not candidate["asks"]:
        return None
    return {field: candidate[field] if field != "asks" else list(candidate[field]) for field in QUERY_FIELDS}
