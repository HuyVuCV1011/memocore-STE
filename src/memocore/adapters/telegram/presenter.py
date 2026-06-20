from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from memocore.domain.schemas import AssistantResponse


def present_response(response: AssistantResponse) -> tuple[str, InlineKeyboardMarkup | None]:
    lines = [response.title]
    if response.summary:
        lines.extend(["", response.summary])
    for section in response.sections:
        if section.heading:
            lines.extend(["", section.heading])
        lines.extend(f"- {line}" for line in section.lines)
    if response.footer:
        lines.extend(["", response.footer])

    keyboard = None
    if response.actions:
        rows: dict[int, list[InlineKeyboardButton]] = {}
        for action in response.actions:
            rows.setdefault(action.row, []).append(
                InlineKeyboardButton(action.label, callback_data=action.action_id)
            )
        keyboard = InlineKeyboardMarkup([rows[index] for index in sorted(rows)])
    return "\n".join(lines), keyboard
