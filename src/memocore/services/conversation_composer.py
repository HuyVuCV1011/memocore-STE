from __future__ import annotations


class ConversationComposer:
    def missing_knowledge_target(self) -> str:
        return (
            "Anh muốn cập nhật thông tin cho project, người hoặc tổ chức nào? "
            "Nói rõ tên entity, hoặc hỏi về entity đó trước giúp em nha."
        )

    def missing_knowledge_payload(self, entity_name: str | None) -> str:
        return f"Anh muốn bổ sung thông tin gì cho {entity_name or 'entity này'}?"

    def generic_clarification(self) -> str:
        return "Em chưa rõ ý anh. Anh có thể nói rõ hơn được không?"
