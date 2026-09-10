from app.db.connection import get_connection


def test_migrations_create_all_tables(temp_db):
    with get_connection() as conn:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    for expected in (
        "projects",
        "project_files",
        "conversations",
        "messages",
        "attachments",
        "app_config",
        "schema_version",
    ):
        assert expected in tables


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
