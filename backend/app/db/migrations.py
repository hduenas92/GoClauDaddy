"""Numbered migration runner. Adding a feature later that needs a new table is
a new entry appended to MIGRATIONS, not a rewrite of schema.sql.
"""

from pathlib import Path

from app.db.connection import get_connection
from app.logging_setup import get_logger

log = get_logger("migrations")

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# (version, description, sql). Append new entries here for future schema changes Ã¢â‚¬â€
# never edit an already-shipped entry.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema", _SCHEMA_SQL),
    (2, "conversation system_prompt", "ALTER TABLE conversations ADD COLUMN system_prompt TEXT"),
    (3, "conversation thinking_budget", "ALTER TABLE conversations ADD COLUMN thinking_budget INTEGER"),
    (4, "messages model column", "ALTER TABLE messages ADD COLUMN model TEXT"),
    (5, "messages cache_read_tokens column", "ALTER TABLE messages ADD COLUMN cache_read_tokens INTEGER DEFAULT 0"),
    (6, "messages cache_creation_tokens column", "ALTER TABLE messages ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0"),
    (7, "conversations source column", "ALTER TABLE conversations ADD COLUMN source TEXT DEFAULT 'web'"),
    (8, "messages tool_calls column", "ALTER TABLE messages ADD COLUMN tool_calls TEXT"),
    (9, "conversations started_at column", "ALTER TABLE conversations ADD COLUMN started_at TEXT"),
    (10, "conversations completed_at column", "ALTER TABLE conversations ADD COLUMN completed_at TEXT"),
    (11, "flow_templates table", """CREATE TABLE IF NOT EXISTS flow_templates (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT,
  body        TEXT NOT NULL,
  category    TEXT NOT NULL,
  is_builtin  INTEGER NOT NULL DEFAULT 0,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
)"""),
    (12, "team_sessions table", """CREATE TABLE IF NOT EXISTS team_sessions (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  completed_at TEXT
)"""),
    (13, "team_members table", """CREATE TABLE IF NOT EXISTS team_members (
  team_id         TEXT NOT NULL REFERENCES team_sessions(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            TEXT NOT NULL DEFAULT 'member',
  PRIMARY KEY (team_id, conversation_id)
)"""),
    (14, "conversations max_tokens column", "ALTER TABLE conversations ADD COLUMN max_tokens INTEGER"),

    # --- v15: a stopped turn is a column, not a marker inside content -------
    # chat_socket used to append "\n\n<!-- claudioui:stopped -->" to content.
    # Metadata inside content means every consumer must strip it (copy button,
    # FTS index, export), and each missed site fails silently. Backfill moves
    # any existing marker into the column and removes it from the text.
    (15, "messages stopped column + marker backfill", [
        "ALTER TABLE messages ADD COLUMN stopped INTEGER NOT NULL DEFAULT 0",
        "UPDATE messages SET stopped = 1 WHERE content LIKE '%<!-- claudioui:stopped -->%'",
        "UPDATE messages SET content = replace(content, char(10) || char(10) || '<!-- claudioui:stopped -->', '') "
        "WHERE stopped = 1",
        # Defensive: catch a marker that was not preceded by the two newlines.
        "UPDATE messages SET content = replace(content, '<!-- claudioui:stopped -->', '') WHERE stopped = 1",
    ]),

    # --- v16: soft delete ---------------------------------------------------
    # Edit+regenerate supersedes rather than deletes, so history is reversible.
    # NULL = live. Non-NULL = the id of the message that replaced it.
    # Deliberately no FK: ALTER TABLE cannot enforce one retroactively, and the
    # app owns this invariant. Read semantics differ by caller and are NOT
    # uniform Ã¢â‚¬â€ see the module docstring in conversations_service.
    (16, "messages superseded_by column", [
        "ALTER TABLE messages ADD COLUMN superseded_by TEXT",
        "CREATE INDEX IF NOT EXISTS idx_messages_live ON messages(conversation_id, superseded_by, seq)",
    ]),

    # --- v17: conversation tags --------------------------------------------
    (17, "conversation_tags table", [
        """CREATE TABLE IF NOT EXISTS conversation_tags (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  tag             TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  PRIMARY KEY (conversation_id, tag)
)""",
        "CREATE INDEX IF NOT EXISTS idx_conversation_tags_tag ON conversation_tags(tag)",
    ]),

    # --- v18: full-text search over message content ------------------------
    # External-content FTS5: the index stores no copy of the text, it points at
    # messages.rowid. Verified available in this build (SQLite 3.50.4,
    # ENABLE_FTS5).
    #
    # The index deliberately contains ALL messages, including superseded ones.
    # Filtering happens at query time via the join back to messages. Making the
    # triggers superseded-aware would mean an UPDATE that flips superseded_by
    # has to delete-and-reinsert index rows, and the failure mode of getting
    # that wrong is a silently stale index. Filtering on read cannot drift.
    (18, "messages_fts FTS5 index + sync triggers", [
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
        "USING fts5(content, content='messages', content_rowid='rowid')",
        """CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END""",
        """CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END""",
        """CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END""",
        # Backfill existing rows. Runs after the triggers exist, but triggers
        # only fire on INSERT/UPDATE/DELETE of messages, so there is no double-add.
        "INSERT INTO messages_fts(rowid, content) SELECT rowid, content FROM messages",
    ]),

    # --- v19: drop three tables nothing reads -------------------------------
    # Phase 3 (4-P4) and Phase 4 (4-D3), decided by Houston 2026-09-19. The exit
    # criterion for Phase 3 is "no schema exists that nothing reads", and these
    # were the remainder.
    #
    # MEASURED before dropping, on the live database:
    #   conversation_tags  0 rows   read by nothing (v17 created it; no router,
    #                               no service, no query ever referenced it)
    #   project_files      0 rows   read by nothing; a placeholder for
    #                               project-level file attachments that was
    #                               never built. Conversation attachments are a
    #                               separate, live table.
    #   app_config         0 rows   read by nothing; configuration lives in
    #                               app/config.py and ~/.claude/settings.json
    #
    # A FORWARD migration, never an edit to v17 Ã¢â‚¬â€ v17 has already run on real
    # databases and rewriting history there would leave installs disagreeing
    # about what version 17 means.
    #
    # schema.sql is edited in the same commit. That is not optional: these three
    # were defined THERE rather than in a migration, so dropping them here alone
    # would leave every fresh install creating them again and the two paths
    # permanently out of step. Existing installs are fixed by this migration;
    # new installs are fixed by schema.sql.
    #
    # IF TAGS OR PROJECT FILES ARE EVER WANTED: add a new forward migration with
    # a schema designed for the real requirement. Do not resurrect these Ã¢â‚¬â€ they
    # were guesses at features that did not exist.
    (19, "drop unconsumed tables: conversation_tags, project_files, app_config", [
        "DROP TABLE IF EXISTS conversation_tags",
        "DROP TABLE IF EXISTS project_files",
        "DROP TABLE IF EXISTS app_config",
    ]),
]


def _current_version(conn) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return row["version"] if row else 0


def _set_version(conn, version: int) -> None:
    # Unconditional DELETE + INSERT prevents a double-row from a prior partial
    # run silently hiding behind LIMIT 1 on the next version read.
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def apply_migrations() -> None:
    with get_connection() as conn:
        current = _current_version(conn)
        applied_any = False
        for version, description, sql in MIGRATIONS:
            if version <= current:
                continue
            log.info("Applying migration %d: %s", version, description)
            if version == 1:
                # v1 baseline is multi-statement Ã¢â‚¬â€ executescript is the only way
                # to run it. It issues an implicit COMMIT first, then runs schema.sql
                # in autocommit. Safe because schema.sql is all CREATE TABLE IF NOT
                # EXISTS (idempotent on retry if the version write crashes after).
                conn.executescript(sql)
            elif isinstance(sql, (list, tuple)):
                # A migration may be a sequence of statements. Same atomicity
                # guarantee as the single-statement path below: every statement
                # plus the version write share one transaction, so a crash
                # part-way leaves the schema at the PREVIOUS version rather than
                # half-applied. Needed because sqlite3 refuses multiple
                # statements in one execute(), and executescript() would break
                # atomicity (see the note below).
                for stmt in sql:
                    conn.execute(stmt)
            else:
                # ponytail: executescript issues implicit COMMIT before running, so
                # DDL executes in autocommit Ã¢â‚¬â€ a crash between the ALTER TABLE and
                # the version write leaves the column added with version unchanged,
                # causing a duplicate-column crash loop on the next restart.
                # conn.execute() keeps the DDL and version write in the same
                # transaction (committed atomically by get_connection's conn.commit).
                conn.execute(sql)
            _set_version(conn, version)
            current = version
            applied_any = True
        if applied_any:
            log.info("Database schema at version %d", current)
