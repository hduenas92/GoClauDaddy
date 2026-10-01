PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  working_dir   TEXT NOT NULL,
  system_prompt TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- project_files was removed in v19 (Phase 4, 4-D3). It held 0 rows and no code
-- ever read it: a placeholder for project-level file attachments that was never
-- built. Conversation attachments are the separate, live `attachments` table.
-- Removed HERE as well as in the migration, because a table defined in this file
-- is recreated on every fresh install — dropping it only in the migration would
-- leave new installs and existing ones permanently disagreeing.

CREATE TABLE IF NOT EXISTS conversations (
  id               TEXT PRIMARY KEY,
  project_id       TEXT REFERENCES projects(id) ON DELETE SET NULL,
  name             TEXT NOT NULL,
  session_id       TEXT,
  model            TEXT NOT NULL,
  permission_mode  TEXT,
  status           TEXT NOT NULL DEFAULT 'idle',
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at);

CREATE TABLE IF NOT EXISTS messages (
  id              TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL,
  content         TEXT NOT NULL,
  thinking        TEXT,
  input_tokens    INTEGER,
  output_tokens   INTEGER,
  seq             INTEGER NOT NULL,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);

CREATE TABLE IF NOT EXISTS attachments (
  id              TEXT PRIMARY KEY,
  message_id      TEXT REFERENCES messages(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  original_name   TEXT NOT NULL,
  stored_path     TEXT NOT NULL,
  mime_type       TEXT,
  size_bytes      INTEGER NOT NULL,
  created_at      TEXT NOT NULL
);

-- app_config was removed in v19 (Phase 4, 4-D3). 0 rows, read by nothing:
-- configuration lives in app/config.py and ~/.claude/settings.json. Same
-- reasoning as project_files above — removed from this file as well as dropped
-- in the migration, so fresh installs and existing ones agree.

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
);
