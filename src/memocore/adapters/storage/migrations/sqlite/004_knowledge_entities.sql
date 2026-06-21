CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'decided',
    decided_at TEXT NOT NULL,
    project_id TEXT REFERENCES projects(id),
    person_id TEXT REFERENCES people(id),
    organization_id TEXT REFERENCES organizations(id),
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

ALTER TABLE memory_items ADD COLUMN organization_id TEXT REFERENCES organizations(id);
ALTER TABLE memory_items ADD COLUMN decision_id TEXT REFERENCES decisions(id);

CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id, decided_at);
CREATE INDEX IF NOT EXISTS idx_decisions_person ON decisions(person_id, decided_at);
CREATE INDEX IF NOT EXISTS idx_decisions_organization ON decisions(organization_id, decided_at);
CREATE INDEX IF NOT EXISTS idx_memory_items_organization ON memory_items(organization_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_decision ON memory_items(decision_id);
