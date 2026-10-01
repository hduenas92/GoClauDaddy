import sqlite3

from app.db.connection import get_connection


def test_fresh_db_has_system_prompt_column(temp_db):
    with get_connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    assert "system_prompt" in cols


def test_v1_db_upgrades_to_latest_without_data_loss(tmp_path, monkeypatch):
    from app.db import connection as cm
    from app.db.migrations import apply_migrations, _SCHEMA_SQL

    db_path = tmp_path / "v1.db"
    monkeypatch.setattr(cm, "DB_PATH", db_path)

    # Seed a v1 DB: schema + version row + one conversation (no system_prompt col yet)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_SQL)
    conn.execute(
        "INSERT INTO conversations (id, project_id, name, session_id, model, permission_mode, status, created_at, updated_at)"
        " VALUES ('c1', NULL, 'Old Chat', NULL, 'claude-sonnet-4-6', NULL, 'idle', 'now', 'now')"
    )
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    apply_migrations()

    with get_connection() as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
        row = conn.execute("SELECT * FROM conversations WHERE id = 'c1'").fetchone()

    from app.db.migrations import MIGRATIONS
    assert version == MIGRATIONS[-1][0]
    assert row["name"] == "Old Chat"
    assert row["system_prompt"] is None  # column added with NULL for existing rows
    assert row["source"] == "web"        # migration 7 default


def test_migrations_create_all_tables(temp_db):
    with get_connection() as conn:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    for expected in (
        "projects",
        "conversations",
        "messages",
        "attachments",
        "schema_version",
    ):
        assert expected in tables


def test_unconsumed_tables_are_gone(temp_db):
    """v19 dropped three tables nothing read (Phase 3 4-P4, Phase 4 4-D3).

    This asserts the ABSENCE deliberately. The test above only checks that
    expected tables are present, so simply deleting two names from its tuple
    would have left the removal completely unverified — the tables could come
    back via schema.sql and nothing would object.

    That matters more than usual here, because these three were defined in
    schema.sql rather than in a migration. A fresh database runs schema.sql AND
    every migration, so this fixture exercises exactly the path where the two
    could disagree: if schema.sql still created them, v19's DROP would run first
    and they would reappear, or vice versa. Either way this fails.
    """
    with get_connection() as conn:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    for gone in ("conversation_tags", "project_files", "app_config"):
        assert gone not in tables, (
            f"{gone} still exists — v19 dropped it, so either schema.sql is "
            f"recreating it or the migration did not run"
        )


def test_v18_db_loses_the_unconsumed_tables_on_upgrade(tmp_path, monkeypatch):
    """v19 must DROP the three tables on an EXISTING database.

    This exists because `test_unconsumed_tables_are_gone` cannot prove it. That
    test builds a fresh database, where schema.sql no longer creates the tables
    at all — so it passes whether or not v19 exists, and it would keep passing
    if the migration were deleted outright. It proves the schema.sql edit and
    nothing else.

    The migration only matters for databases that ALREADY HAVE the tables, and
    neither existing fixture produces one: `temp_db` and the v1-upgrade test
    both build from the current `_SCHEMA_SQL`. So this creates the pre-v19 state
    explicitly — the tables present, version pinned at 18 — and asserts the
    upgrade removes them while leaving real data untouched.

    Without this the DROP statements would be entirely unverified, which is the
    vacuous-assertion trap this project keeps finding: a test that cannot fail
    for the reason it claims to exist.
    """
    from app.db import connection as cm
    from app.db.migrations import apply_migrations, _SCHEMA_SQL

    db_path = tmp_path / "v18.db"
    monkeypatch.setattr(cm, "DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_SQL)
    # Recreate the pre-v19 world: the three tables as they were defined before
    # this commit removed them from schema.sql.
    conn.executescript(
        """
        -- IF NOT EXISTS on every one. Without it, this fixture COLLIDES rather
        -- than asserting the moment schema.sql also defines a table of the same
        -- name -- which is precisely the condition a mutation of schema.sql
        -- creates. A setup collision is an ambiguous failure, not a failing
        -- assertion, and it would read as "the drop is load-bearing" while
        -- proving nothing of the sort. Measured: it did exactly that.
        CREATE TABLE IF NOT EXISTS conversation_tags (
          conversation_id TEXT NOT NULL,
          tag             TEXT NOT NULL,
          created_at      TEXT NOT NULL,
          PRIMARY KEY (conversation_id, tag)
        );
        CREATE TABLE IF NOT EXISTS project_files (
          id          TEXT PRIMARY KEY,
          project_id  TEXT NOT NULL,
          filename    TEXT NOT NULL,
          stored_path TEXT NOT NULL,
          size_bytes  INTEGER NOT NULL,
          added_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.execute(
        "INSERT INTO conversations (id, project_id, name, session_id, model, permission_mode,"
        " status, created_at, updated_at)"
        " VALUES ('keep-me', NULL, 'Real Chat', NULL, 'claude-sonnet-4-6', NULL, 'idle', 'now', 'now')"
    )
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (18)")
    conn.commit()

    # The set must be NON-EMPTY before the upgrade, or this proves nothing.
    before = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert {"conversation_tags", "project_files", "app_config"} <= before, (
        "the pre-v19 fixture did not actually create the tables, so the upgrade "
        "below would have nothing to drop"
    )

    apply_migrations()

    with get_connection() as conn:
        after = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        row = conn.execute("SELECT name FROM conversations WHERE id = 'keep-me'").fetchone()

    for gone in ("conversation_tags", "project_files", "app_config"):
        assert gone not in after, f"v19 did not drop {gone} on an existing database"
    assert row["name"] == "Real Chat", "a destructive migration must not touch real data"


def test_migrations_are_idempotent(temp_db):
    from app.db.migrations import apply_migrations

    apply_migrations()  # running twice must not error or duplicate rows
    with get_connection() as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchall()
    assert len(version) == 1


def test_foreign_keys_enforced(temp_db):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO conversations (id, project_id, name, session_id, model, permission_mode, status, created_at, updated_at) "
            "VALUES ('c1', NULL, 'test', NULL, 'm', NULL, 'idle', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, seq, created_at) VALUES ('m1','c1','user','hi',1,'now')"
        )
        conn.execute("DELETE FROM conversations WHERE id = 'c1'")
        remaining = conn.execute("SELECT * FROM messages WHERE id = 'm1'").fetchall()
    assert remaining == []  # ON DELETE CASCADE removed the message too


# ---------------------------------------------------------------------------
# v20: drop the team tables only when both are empty
# ---------------------------------------------------------------------------


def _seed_v19_team_db(db_path):
    """Create a v19-shaped DB with team tables and one team_sessions row.

    v20 must leave both tables alone when either has data, so this fixture
    builds the data-bearing side explicitly rather than relying on the current
    migration list (which, after v20 lands, no longer creates the tables).
    """
    from app.db.migrations import _SCHEMA_SQL

    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_SQL)
    conn.executescript(
        """
        CREATE TABLE team_sessions (
          id           TEXT PRIMARY KEY,
          name         TEXT NOT NULL,
          created_at   TEXT NOT NULL,
          completed_at TEXT
        );
        CREATE TABLE team_members (
          team_id         TEXT NOT NULL REFERENCES team_sessions(id) ON DELETE CASCADE,
          conversation_id TEXT NOT NULL,
          role            TEXT NOT NULL DEFAULT 'member',
          PRIMARY KEY (team_id, conversation_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO team_sessions (id, name, created_at) VALUES ('t1', 'Keep Me', 'now')"
    )
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (19)")
    conn.commit()
    conn.close()


def test_fresh_db_ends_at_v20_without_team_tables(temp_db):
    with get_connection() as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert version == 20
    assert "team_sessions" not in tables
    assert "team_members" not in tables


def test_v19_db_with_a_team_row_keeps_team_tables_at_v20(tmp_path, monkeypatch):
    from app.db import connection as cm
    from app.db.migrations import apply_migrations

    db_path = tmp_path / "v19-team.db"
    monkeypatch.setattr(cm, "DB_PATH", db_path)
    _seed_v19_team_db(db_path)

    apply_migrations()

    with get_connection() as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
        team = conn.execute("SELECT name FROM team_sessions WHERE id = 't1'").fetchone()
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert version == 20
    assert team["name"] == "Keep Me"
    assert {"team_sessions", "team_members"} <= tables
