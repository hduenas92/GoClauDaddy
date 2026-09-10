"""sqlite3 connection helper — WAL mode, busy_timeout, Row factory, parameterized queries only.

Single local user / light traffic, so a short-lived connection per operation
(via the `with get_connection() as conn:` context manager) is simpler and
plenty fast — no pooling needed.
"""

import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
