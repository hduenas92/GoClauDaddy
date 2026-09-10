import pytest

from app.db import connection as connection_module
from app.db.migrations import apply_migrations


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Points the DB layer at a throwaway file for the duration of one test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(connection_module, "DB_PATH", db_path)
    apply_migrations()
    return db_path
