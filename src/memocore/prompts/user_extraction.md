Current datetime: {current_datetime}
Current date: {current_date}
Tomorrow date: {tomorrow_date}
Day after tomorrow date: {day_after_tomorrow_date}

Date resolution rules:
- "tomorrow", "mai", "ngày mai" → {tomorrow_date}
- "today", "hôm nay" → {current_date}
- "day after tomorrow", "ngày mốt" → {day_after_tomorrow_date}
- "next Monday", "thứ hai tuần sau" → {next_monday_date}
- All dates must be ISO 8601 format with timezone.
- If a date cannot be resolved, set the field to null.

Context:
{context}

Note:
{raw_text}

Return one JSON object now.
