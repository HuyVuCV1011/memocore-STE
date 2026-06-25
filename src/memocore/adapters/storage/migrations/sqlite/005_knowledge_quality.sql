ALTER TABLE decisions ADD COLUMN supersedes_decision_id TEXT REFERENCES decisions(id);

ALTER TABLE memory_items ADD COLUMN canonical_memory_id TEXT REFERENCES memory_items(id);
ALTER TABLE memory_items ADD COLUMN conflict_state TEXT NOT NULL DEFAULT 'none';

CREATE TABLE IF NOT EXISTS knowledge_relations (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, source_id, target_type, target_id, relation_type, source_note_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_source
ON knowledge_relations(source_type, source_id, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
ON knowledge_relations(target_type, target_id, status);
CREATE INDEX IF NOT EXISTS idx_decisions_supersedes ON decisions(supersedes_decision_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_canonical ON memory_items(canonical_memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_conflict ON memory_items(conflict_state);
