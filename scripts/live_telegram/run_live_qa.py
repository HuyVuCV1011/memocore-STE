from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QaMessage:
    text: str
    wait_after_seconds: float = 0.0


@dataclass(frozen=True)
class QaCase:
    name: str
    bot_username: str
    messages: list[QaMessage]
    timeout_seconds: float
    quiet_seconds: float
    expect: dict[str, Any]


@dataclass(frozen=True)
class BotReply:
    text: str
    buttons: list[str]


@dataclass(frozen=True)
class QaStepResult:
    user_text: str
    bot_replies: list[BotReply]


@dataclass(frozen=True)
class QaRunResult:
    case_name: str
    bot_username: str
    started_at: str
    steps: list[QaStepResult]
    failures: list[str]
    expect: dict[str, Any] | None = None


def load_case(path: Path) -> QaCase:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("case must define a non-empty messages list")
    messages = [_parse_message(item) for item in raw_messages]
    bot_username = str(payload.get("bot_username") or os.getenv("TELEGRAM_LIVE_QA_BOT_USERNAME") or "")
    if not bot_username:
        raise ValueError("case bot_username or TELEGRAM_LIVE_QA_BOT_USERNAME is required")
    return QaCase(
        name=_required_str(payload, "name"),
        bot_username=bot_username,
        messages=messages,
        timeout_seconds=float(payload.get("timeout_seconds", 30)),
        quiet_seconds=float(payload.get("quiet_seconds", 2)),
        expect=dict(payload.get("expect", {})),
    )


def validate_expectations(result: QaRunResult) -> list[str]:
    expect = result.expect or {}
    failures: list[str] = []
    all_text = "\n".join(reply.text for step in result.steps for reply in step.bot_replies)
    bot_message_count = sum(len(step.bot_replies) for step in result.steps)
    min_bot_messages = expect.get("min_bot_messages")
    if min_bot_messages is not None and bot_message_count < int(min_bot_messages):
        failures.append(
            f"expected at least {int(min_bot_messages)} bot message(s), got {bot_message_count}"
        )
    max_bot_messages = expect.get("max_bot_messages")
    if max_bot_messages is not None and bot_message_count > int(max_bot_messages):
        failures.append(
            f"expected at most {int(max_bot_messages)} bot message(s), got {bot_message_count}"
        )
    for value in expect.get("must_include", []):
        if str(value) not in all_text:
            failures.append(f"missing required text: {value}")
    for value in expect.get("must_not_include", []):
        if str(value) in all_text:
            failures.append(f"forbidden text present: {value}")
    return failures


async def run_case(
    case: QaCase,
    *,
    session_path: str,
    api_id: int,
    api_hash: str,
    phone: str | None,
) -> QaRunResult:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Install live QA dependencies with: python -m pip install -e .[live-qa]") from exc

    started_at = datetime.now(UTC).isoformat()
    steps: list[QaStepResult] = []
    client = TelegramClient(session_path, api_id, api_hash)
    await client.start(phone=phone)
    try:
        bot = await client.get_entity(case.bot_username)
        me = await client.get_me()
        for message in case.messages:
            sent = await client.send_message(bot, message.text)
            replies = await _collect_replies(
                client=client,
                entity=bot,
                bot_id=bot.id,
                own_user_id=me.id,
                min_id=sent.id,
                timeout_seconds=case.timeout_seconds,
                quiet_seconds=case.quiet_seconds,
            )
            steps.append(QaStepResult(user_text=message.text, bot_replies=replies))
            if message.wait_after_seconds > 0:
                await asyncio.sleep(message.wait_after_seconds)
    finally:
        await client.disconnect()
    partial = QaRunResult(
        case_name=case.name,
        bot_username=case.bot_username,
        started_at=started_at,
        steps=steps,
        failures=[],
        expect=case.expect,
    )
    return QaRunResult(
        case_name=partial.case_name,
        bot_username=partial.bot_username,
        started_at=partial.started_at,
        steps=partial.steps,
        failures=validate_expectations(partial),
        expect=case.expect,
    )


def write_reports(result: QaRunResult, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in result.case_name)
    json_path = report_dir / f"{stamp}-{safe_name}.json"
    md_path = report_dir / f"{stamp}-{safe_name}.md"
    json_path.write_text(
        json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    return json_path, md_path


async def _collect_replies(
    *,
    client: Any,
    entity: Any,
    bot_id: int,
    own_user_id: int,
    min_id: int,
    timeout_seconds: float,
    quiet_seconds: float,
) -> list[BotReply]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    quiet_deadline: float | None = None
    seen_ids: set[int] = set()
    replies: list[BotReply] = []
    while asyncio.get_running_loop().time() < deadline:
        batch = [
            message
            async for message in client.iter_messages(entity, min_id=min_id, reverse=True)
            if message.id not in seen_ids and message.sender_id not in {own_user_id, None}
        ]
        new_replies = [message for message in batch if message.sender_id == bot_id]
        if new_replies:
            for message in new_replies:
                seen_ids.add(message.id)
                replies.append(
                    BotReply(
                        text=message.raw_text or "",
                        buttons=_button_labels(message),
                    )
                )
            quiet_deadline = asyncio.get_running_loop().time() + quiet_seconds
        elif replies and quiet_deadline is not None and asyncio.get_running_loop().time() >= quiet_deadline:
            break
        await asyncio.sleep(0.5)
    return replies


def _button_labels(message: Any) -> list[str]:
    labels: list[str] = []
    for row in message.buttons or []:
        for button in row:
            labels.append(str(getattr(button, "text", "")))
    return labels


def _parse_message(item: Any) -> QaMessage:
    if isinstance(item, str):
        return QaMessage(text=item)
    if not isinstance(item, dict):
        raise ValueError("messages must contain strings or objects")
    return QaMessage(
        text=_required_str(item, "text"),
        wait_after_seconds=float(item.get("wait_after_seconds", 0)),
    )


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value


def _result_to_dict(result: QaRunResult) -> dict[str, Any]:
    return {
        "case_name": result.case_name,
        "bot_username": result.bot_username,
        "started_at": result.started_at,
        "failures": result.failures,
        "steps": [
            {
                "user_text": step.user_text,
                "bot_replies": [
                    {"text": reply.text, "buttons": reply.buttons} for reply in step.bot_replies
                ],
            }
            for step in result.steps
        ],
    }


def _render_markdown(result: QaRunResult) -> str:
    lines = [
        f"# Live Telegram QA: {result.case_name}",
        "",
        f"- Bot: `{result.bot_username}`",
        f"- Started: `{result.started_at}`",
        f"- Result: `{'pass' if not result.failures else 'fail'}`",
        "",
    ]
    if result.failures:
        lines.extend(["## Failures", ""])
        lines.extend(f"- {failure}" for failure in result.failures)
        lines.append("")
    for index, step in enumerate(result.steps, start=1):
        lines.extend([f"## Step {index}", "", f"User: `{step.user_text}`", ""])
        if not step.bot_replies:
            lines.extend(["No bot reply captured.", ""])
            continue
        for reply_index, reply in enumerate(step.bot_replies, start=1):
            lines.extend([f"Bot reply {reply_index}:", "", "```text", reply.text, "```", ""])
            if reply.buttons:
                lines.extend(["Buttons:", ""])
                lines.extend(f"- {label}" for label in reply.buttons)
                lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live Telegram QA cases against a MemoCore bot.")
    parser.add_argument("case", type=Path, help="Path to a JSON QA case.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the case only.")
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("telegram_live_qa_reports"),
        help="Directory for JSON and Markdown run reports.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    case = load_case(args.case)
    if args.dry_run:
        print(f"Case OK: {case.name}")
        print(f"Bot: {case.bot_username}")
        print(f"Messages: {len(case.messages)}")
        return
    api_id = os.getenv("TELEGRAM_LIVE_QA_API_ID")
    api_hash = os.getenv("TELEGRAM_LIVE_QA_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("TELEGRAM_LIVE_QA_API_ID and TELEGRAM_LIVE_QA_API_HASH are required")
    session_path = os.getenv("TELEGRAM_LIVE_QA_SESSION", ".telegram-live-qa/memocore-live-qa")
    phone = os.getenv("TELEGRAM_LIVE_QA_PHONE")
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(
        run_case(
            case,
            session_path=session_path,
            api_id=int(api_id),
            api_hash=api_hash,
            phone=phone,
        )
    )
    json_path, md_path = write_reports(result, args.report_dir)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    if result.failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
