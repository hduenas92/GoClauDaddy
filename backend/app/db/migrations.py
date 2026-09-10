"""Numbered migration runner. Adding a feature later that needs a new table is
a new entry appended to MIGRATIONS, not a rewrite of schema.sql.
"""

from pathlib import Path

from app.db.connection import get_connection
from app.logging_setup import get_logger

log = get_logger("migrations")

_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# (version, description, sql). Append new entries here for future schema changes —
# never edit an already-shipped entry.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema", _SCHEMA_SQL),
]


def _current_version(conn) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return row["version"] if row else 0


def apply_migrations() -> None:
    with get_connection() as conn:
        current = _current_version(conn)
        applied_any = False
        for version, description, sql in MIGRATIONS:
            if version <= current:
                continue
            log.info("Applying migration %d: %s", version, description)
            conn.executescript(sql)
            if current == 0:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            else:
                conn.execute("UPDATE schema_version SET version = ?", (version,))
            current = version
            applied_any = True
        if applied_any:
            log.info("Database schema at version %d", current)
