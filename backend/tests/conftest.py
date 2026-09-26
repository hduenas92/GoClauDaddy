"""Shared pytest fixtures.

The import-time block below must run BEFORE any other `app.*` module is
imported: `app.config` computes DATA_DIR/LOG_FILE at import time, and
`app.main` calls `setup_logging()` at import time. Re-pointing the path
constants on the already-imported `app.config` module — before
`app.logging_setup` / `app.db.connection` bind them with `from app.config
import ...` — means the RotatingFileHandler is bound to a tmp log file instead
of the user's real ~/.goclaudaddy/logs/app.log. The real environment is never
touched, so subprocess tests (test_live_survival.py) still inherit a clean
environment and build their own data dirs from USERPROFILE exactly as before.
"""

import os
import tempfile
from pathlib import Path

import app.config as _app_config

# Capture the profile from the environment BEFORE any re-pointing below. In a
# fake-home run this is the fake profile; in an unprotected real run it is the
# user's real profile. The guard refuses either way to let the session touch
# <profile>/.goclaudaddy.
_REAL_PROFILE = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))

_GCA_PYTEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="goclaudaddy-pytest-"))
_app_config.DATA_DIR = _GCA_PYTEST_DATA_DIR
_app_config.DB_PATH = _GCA_PYTEST_DATA_DIR / "goclaudaddy.db"
_app_config.LOG_DIR = _GCA_PYTEST_DATA_DIR / "logs"
_app_config.LOG_FILE = _GCA_PYTEST_DATA_DIR / "logs" / "app.log"
_app_config.ATTACHMENTS_DIR = _GCA_PYTEST_DATA_DIR / "attachments"


def _fail_if_real_profile() -> None:
    """P2-T guard: fail the session immediately if DB_PATH or LOG_FILE still
    resolves under the profile's real `.goclaudaddy` data dir.

    The profile is the one captured above, before the re-pointing block ran, so
    a test that points `app.config.DB_PATH` back at `<profile>/.goclaudaddy`
    trips this guard and the session dies before a single connection is opened.
    """
    real_gca = _REAL_PROFILE / ".goclaudaddy"
    offenders = []
    for name, path in (("DB_PATH", _app_config.DB_PATH), ("LOG_FILE", _app_config.LOG_FILE)):
        try:
            if Path(path).resolve().is_relative_to(real_gca.resolve()):
                offenders.append(f"{name}={path}")
        except OSError:
            continue
    if offenders:
        raise RuntimeError(
            "Refusing to run tests against the real GoClaudaddy data dir "
            f"{real_gca}: " + ", ".join(offenders)
        )


_fail_if_real_profile()

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
