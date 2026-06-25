ALTER TABLE conversation_turns ADD COLUMN assistant_reply TEXT;
ALTER TABLE conversation_turns ADD COLUMN plan_json TEXT NOT NULL DEFAULT '{}';
