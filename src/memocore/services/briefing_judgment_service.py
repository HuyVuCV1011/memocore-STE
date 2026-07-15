from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo


def briefing_assessment(
    *,
    overdue,
    due_today,
    meetings,
    waiting,
    overdue_followups,
    due_commitments,
    action_items,
    routine_count: int,
    undated_count: int,
    display_timezone: tzinfo,
    reference_date=None,
) -> str:
    pressure = (
        len(overdue) * 3
        + len(overdue_followups) * 2
        + len(due_commitments) * 2
        + len(due_today)
        + len(meetings)
        + len(waiting)
    )
    if pressure == 0:
        return (
            "Hôm nay chưa có áp lực bắt buộc trong hệ thống. Đây là khoảng trống tốt để "
            "chọn một ưu tiên chủ động thay vì chỉ phản ứng theo deadline."
        )
    if overdue or overdue_followups or due_commitments:
        overdue_names = _task_title_list(overdue)
        if overdue_names:
            return (
                f"Rủi ro lớn nhất là {overdue_names} đã quá hạn. Nên xử lý hoặc chốt lại "
                "cam kết trước khi mở thêm việc mới."
            )
        return (
            "Ngày có rủi ro trễ cam kết. Nên xử lý phần đã quá hạn hoặc liên quan người khác "
            "trước khi mở thêm việc mới."
        )
    if len(due_today) >= 2:
        task_names = _task_title_list(due_today)
        return (
            f"Các deadline hôm nay là {task_names}. Em chưa biết mỗi việc tốn bao lâu, "
            "nên anh chọn thứ tự và chừa buffer nha."
        )
    if len(due_today) == 1:
        task = due_today[0]
        if getattr(task, "recurrence_rule", None):
            return (
                f"Hôm nay có routine “{task.title}”. Nên giữ nhịp nếu còn năng lượng, "
                "nhưng briefing chưa thấy một kết quả chính; anh nên chọn thêm một priority chủ động."
            )
        return (
            f"Việc cần chốt hôm nay là “{task.title}”, hạn "
            f"{_format_due(task.due_at, display_timezone, reference_date)}. Khối lượng hiện vẫn ở mức "
            "kiểm soát được nếu anh bảo vệ thời gian cho việc này."
        )
    if action_items:
        top = action_items[0]
        task = top.task
        if getattr(task, "recurrence_rule", None):
            return (
                f"Việc hệ thống thấy rõ nhất là routine “{task.title}”. Nên giữ nhịp nếu còn năng lượng, "
                "nhưng đây chưa phải một ưu tiên chiến lược; anh nên chọn thêm một kết quả chính cho ngày."
            )
        return (
            f"Ưu tiên nên khóa trước là “{task.title}” vì {top.reason}. "
            "Sau khi xong việc này mới nên chuyển sang các việc nhỏ hơn."
        )
    if waiting and not (overdue or due_today or overdue_followups or due_commitments):
        return (
            "Không có việc làm-ngay thật sự nổi bật, nhưng có open loop đang chờ/bị chặn. "
            "Hôm nay nên quyết định follow-up, tiếp tục chờ, hoặc đóng loop thay vì tự tạo thêm việc."
        )
    if routine_count and pressure == routine_count:
        return (
            "Hôm nay chủ yếu là routine. Giữ nhịp là tốt, nhưng briefing chưa thấy một kết quả chính; "
            "anh nên chọn một priority chủ động nếu muốn ngày này có tiến triển rõ."
        )
    if undated_count and pressure == 0:
        return (
            "Không có deadline ép trong hôm nay, nhưng vẫn có việc chưa được quyết định hạn/bước tiếp theo. "
            "Nên chọn một việc để gắn deadline hoặc xác định next action."
        )
    if pressure >= 5:
        return (
            "Khối lượng hôm nay tương đối dày. Nên khóa một việc quan trọng trước, rồi mới "
            "chuyển sang meeting và các việc nhỏ."
        )
    return (
        "Khối lượng hôm nay ở mức kiểm soát được. Chọn một kết quả chính và bảo vệ thời gian "
        "để hoàn thành nó."
    )


def briefing_signals(
    *,
    overdue,
    due_today,
    reminders,
    meetings,
    waiting,
    overdue_followups,
    due_commitments,
    upcoming_top,
    display_timezone: tzinfo,
    reference_date=None,
) -> list[str]:
    signals: list[str] = []
    if overdue:
        signals.append(f"- Quá hạn: {_task_title_list(overdue)}.")
    if overdue_followups:
        signals.append(f"- {len(overdue_followups)} follow-up đã qua hạn, dễ làm đứt mạch phối hợp.")
    if due_commitments:
        signals.append(f"- {len(due_commitments)} cam kết đến hạn hoặc đã trễ, nên phản hồi sớm.")
    if due_today:
        signals.append(
            f"- Hôm nay: {_task_due_list(due_today, display_timezone, reference_date)}."
        )
    if upcoming_top:
        signals.append(
            f"- Sắp tới: {_task_due_list(upcoming_top, display_timezone, reference_date)}."
        )
    if meetings:
        first = min(
            meetings,
            key=lambda item: item.starts_at or datetime.max.replace(tzinfo=UTC),
        )
        signals.append(
            f"- {len(meetings)} meeting; lịch gần nhất là “{first.title}” lúc "
            f"{_format_time(first.starts_at, display_timezone)}."
        )
    if reminders:
        signals.append(f"- {len(reminders)} lời nhắc sẽ đến trong ngày.")
    if waiting:
        signals.append(f"- {len(waiting)} task đang chờ hoặc bị chặn; cần quyết định có thúc đẩy không.")
    return signals


def _task_title_list(tasks) -> str:
    titles = [f"“{task.title}”" for task in tasks]
    if len(titles) <= 1:
        return titles[0] if titles else ""
    return ", ".join(titles[:-1]) + f" và {titles[-1]}"


def _task_due_list(tasks, display_timezone: tzinfo, reference_date=None) -> str:
    return "; ".join(
        f"“{task.title}” hạn {_format_due(task.due_at, display_timezone, reference_date)}"
        for task in tasks
    )


def _format_time(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa rõ giờ"
    return value.astimezone(display_timezone).strftime("%H:%M")


def _format_due(
    value: datetime | None, display_timezone: tzinfo, reference_date=None
) -> str:
    if value is None:
        return "chưa có hạn"
    local_value = value.astimezone(display_timezone)
    today = reference_date or datetime.now(UTC).astimezone(display_timezone).date()
    day_label = _day_label(local_value.date(), today)
    return f"{_format_time(value, display_timezone)} {day_label}"


def _day_label(value: date, today: date) -> str:
    if value == today:
        return "hôm nay"
    delta = (value - today).days
    if delta == 1:
        return "ngày mai"
    if delta == -1:
        return "hôm qua"
    return value.strftime("%d/%m/%Y")
