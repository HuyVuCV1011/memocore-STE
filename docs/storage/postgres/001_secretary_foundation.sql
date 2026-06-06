CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE captures (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    source_chat_id text,
    source_message_id text,
    raw_text text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL,
    UNIQUE (source, source_chat_id, source_message_id)
);

CREATE TABLE projects (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE,
    summary text NOT NULL DEFAULT '',
    status text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE people (
    id uuid PRIMARY KEY,
    display_name text NOT NULL,
    aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
    relationship text NOT NULL DEFAULT '',
    notes text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE memory_claims (
    id uuid PRIMARY KEY,
    bucket text NOT NULL,
    kind text NOT NULL,
    content text NOT NULL,
    status text NOT NULL,
    confidence double precision NOT NULL,
    source_capture_id uuid NOT NULL REFERENCES captures(id),
    project_id uuid REFERENCES projects(id),
    person_id uuid REFERENCES people(id),
    embedding vector,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE memory_revisions (
    id uuid PRIMARY KEY,
    memory_claim_id uuid NOT NULL REFERENCES memory_claims(id),
    previous_status text NOT NULL,
    next_status text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE tasks (
    id uuid PRIMARY KEY,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    status text NOT NULL,
    priority text NOT NULL,
    due_at timestamptz,
    project_id uuid REFERENCES projects(id),
    person_id uuid REFERENCES people(id),
    source_capture_id uuid NOT NULL REFERENCES captures(id),
    confidence double precision NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE reminders (
    id uuid PRIMARY KEY,
    title text NOT NULL,
    remind_at timestamptz,
    status text NOT NULL,
    task_id uuid REFERENCES tasks(id),
    source_capture_id uuid NOT NULL REFERENCES captures(id),
    attempt_count integer NOT NULL DEFAULT 0,
    claimed_at timestamptz,
    recurrence_rule text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE meetings (
    id uuid PRIMARY KEY,
    title text NOT NULL,
    starts_at timestamptz,
    ends_at timestamptz,
    project_id uuid REFERENCES projects(id),
    person_id uuid REFERENCES people(id),
    source_capture_id uuid NOT NULL REFERENCES captures(id),
    notes text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE followups (
    id uuid PRIMARY KEY,
    title text NOT NULL,
    due_at timestamptz,
    status text NOT NULL,
    person_id uuid REFERENCES people(id),
    project_id uuid REFERENCES projects(id),
    source_capture_id uuid REFERENCES captures(id),
    notes text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE commitments (
    id uuid PRIMARY KEY,
    title text NOT NULL,
    direction text NOT NULL,
    status text NOT NULL,
    person_id uuid REFERENCES people(id),
    project_id uuid REFERENCES projects(id),
    due_at timestamptz,
    source_capture_id uuid REFERENCES captures(id),
    notes text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE meeting_people (
    meeting_id uuid NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    person_id uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    PRIMARY KEY (meeting_id, person_id)
);

CREATE TABLE events (
    id uuid PRIMARY KEY,
    event_type text NOT NULL,
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL
);

CREATE TABLE entity_links (
    id uuid PRIMARY KEY,
    from_type text NOT NULL,
    from_id uuid NOT NULL,
    relation text NOT NULL,
    to_type text NOT NULL,
    to_id uuid NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE INDEX memory_claims_search_idx
ON memory_claims USING gin (to_tsvector('simple', content));

CREATE INDEX tasks_status_due_idx ON tasks(status, due_at);
CREATE INDEX reminders_status_due_idx ON reminders(status, remind_at);
CREATE INDEX followups_status_due_idx ON followups(status, due_at);
CREATE INDEX tasks_person_idx ON tasks(person_id);
CREATE INDEX meetings_person_idx ON meetings(person_id);
CREATE INDEX memory_claims_person_idx ON memory_claims(person_id);
CREATE INDEX commitments_person_status_idx ON commitments(person_id, status);
CREATE INDEX commitments_project_status_idx ON commitments(project_id, status);
