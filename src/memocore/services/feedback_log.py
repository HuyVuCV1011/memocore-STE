from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path


def write_feedback_signal(intent: str, raw_text: str, context: dict) -> None:
    path = Path(
        os.environ.get("MEMOCORE_FEEDBACK_PATH", "data/user_feedback.jsonl")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "intent": intent,
        "raw_text": raw_text,
        "context": context,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
