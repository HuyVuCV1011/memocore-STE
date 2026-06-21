CREATE TABLE IF NOT EXISTS task_list_contexts (
    source_chat_id TEXT PRIMARY KEY,
    task_ids TEXT NOT NULL,
    source_view TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_recurrence_occurrence
ON tasks(recurrence_series_id, recurrence_occurrence_at)
WHERE recurrence_series_id IS NOT NULL AND recurrence_occurrence_at IS NOT NULL;
