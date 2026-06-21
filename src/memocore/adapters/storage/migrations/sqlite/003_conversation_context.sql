CREATE TABLE IF NOT EXISTS chat_contexts (
    source_chat_id TEXT PRIMARY KEY,
    focused_entity_type TEXT,
    focused_entity_id TEXT,
    last_intent TEXT,
    last_result_entity_ids TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id TEXT PRIMARY KEY,
    source_chat_id TEXT NOT NULL,
    source_message_id TEXT,
    raw_text TEXT NOT NULL,
    intent TEXT NOT NULL,
    focused_entity_type TEXT,
    focused_entity_id TEXT,
    result_entity_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_chat_created
ON conversation_turns(source_chat_id, created_at);
