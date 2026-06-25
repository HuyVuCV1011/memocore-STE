CREATE TABLE IF NOT EXISTS activity_links (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'same_activity',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, meeting_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_links_meeting
ON activity_links(meeting_id, relation_type);
