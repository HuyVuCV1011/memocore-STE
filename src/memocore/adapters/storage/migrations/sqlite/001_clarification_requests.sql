CREATE TABLE IF NOT EXISTS clarification_requests (
    id TEXT PRIMARY KEY,
    source_chat_id TEXT NOT NULL,
    source_message_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    answer_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_clarification_pending_chat
ON clarification_requests(source_chat_id, status, created_at);
