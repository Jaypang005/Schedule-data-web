"""Flask web interface for the retrieval-first schedule chatbot."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from chatbot import ScheduleChatbot


app = Flask(__name__)

# The schedule data is immutable while the process is running, so one chatbot
# instance can safely serve every request without repeatedly loading JSON files.
chatbot = ScheduleChatbot()
TEACHERS_PATH = Path(__file__).resolve().parent / "data" / "teachers.json"


@app.get("/")
def index():
    return render_template("chat.html")


@app.get("/api/teachers")
def teachers():
    try:
        with TEACHERS_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or not isinstance(payload.get("teachers"), list):
            raise ValueError("teachers.json has an invalid schema")
    except (OSError, ValueError, json.JSONDecodeError):
        app.logger.exception("Unable to load teachers.json")
        return jsonify({"error": "teacher data unavailable"}), 500
    return jsonify(payload)


@app.post("/api/chat")
def chat():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
        return jsonify({"error": "invalid request"}), 400

    message = payload["message"].strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        # app.py contains no answer logic. Every reply comes from chatbot.py.
        response = chatbot.response(message)
    except Exception:
        app.logger.exception("Schedule chatbot failed")
        return jsonify({"error": "internal server error"}), 500
    return jsonify(response)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
