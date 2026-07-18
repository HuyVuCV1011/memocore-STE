from __future__ import annotations

import json

from scripts.live_telegram.run_live_qa import (
    BotReply,
    QaRunResult,
    QaStepResult,
    load_case,
    validate_expectations,
)


def test_load_case_accepts_string_messages(tmp_path):
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "smoke",
                "bot_username": "@bot",
                "messages": ["/today"],
                "expect": {"min_bot_messages": 1},
            }
        ),
        encoding="utf-8",
    )

    case = load_case(case_path)

    assert case.name == "smoke"
    assert case.bot_username == "@bot"
    assert case.messages[0].text == "/today"
    assert case.expect["min_bot_messages"] == 1


def test_validate_expectations_reports_forbidden_text():
    result = QaRunResult(
        case_name="smoke",
        bot_username="@bot",
        started_at="2026-07-16T00:00:00+00:00",
        steps=[
            QaStepResult(
                user_text="/today",
                bot_replies=[BotReply(text="task_id: hidden metadata leaked", buttons=[])],
            )
        ],
        failures=[],
        expect={"must_not_include": ["task_id"]},
    )

    failures = validate_expectations(result)

    assert failures == ["forbidden text present: task_id"]
