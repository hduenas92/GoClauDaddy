PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  working_dir   TEXT NOT NULL,
  system_prompt TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_files (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  filename    TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  size_bytes  INTEGER NOT NULL,
  added_at    TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS app_config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
);
