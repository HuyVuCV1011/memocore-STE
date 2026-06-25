from __future__ import annotations


class ConversationComposer:
    def tasks_merged(self, task, display_timezone) -> str:
        schedule = ""
        if task.due_at is not None:
            schedule = (
                f", hạn {task.due_at.astimezone(display_timezone).strftime('%H:%M %d/%m/%Y')}"
            )
        return f"Dạ, em đã gộp thành một task: “{task.title}”{schedule}."

    def missing_knowledge_target(self) -> str:
        return (
            "Anh muốn cập nhật thông tin cho project, người hoặc tổ chức nào? "
            "Nói rõ tên entity, hoặc hỏi về entity đó trước giúp em nha."
        )

    def missing_knowledge_payload(self, entity_name: str | None) -> str:
        return f"Anh muốn bổ sung thông tin gì cho {entity_name or 'entity này'}?"

    def generic_clarification(self) -> str:
        return "Em chưa rõ ý anh. Anh có thể nói rõ hơn được không?"

    def unhandled(self, vietnamese: bool = True) -> str:
        return (
            "Em chưa xử lý được yêu cầu này. Anh nói rõ hơn anh muốn hỏi hay muốn em làm gì nha?"
            if vietnamese
            else "I could not handle this request. Could you clarify what you want me to answer or do?"
        )
