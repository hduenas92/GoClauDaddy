"""P2-T guard tests.

The guard lives at import time in `backend/tests/conftest.py`. These tests call
the same function so the session-level protection is exercised directly: a test
that re-points `app.config.DB_PATH` back at `<profile>/.goclaudaddy` must make
the guard raise, and a tmp DB path must leave it quiet.
"""

import os
import tempfile
from pathlib import Path

import pytest

import app.config as _app_config
from tests.conftest import _fail_if_real_profile


def test_guard_trips_when_db_path_points_at_profile_gca(monkeypatch):
    profile = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))
    monkeypatch.setattr(_app_config, "DB_PATH", profile / ".goclaudaddy" / "goclaudaddy.db")
    with pytest.raises(RuntimeError, match="Refusing to run tests"):
        _fail_if_real_profile()


def test_guard_stays_quiet_for_tmp_db_path(monkeypatch):
    monkeypatch.setattr(_app_config, "DB_PATH", Path(tempfile.gettempdir()) / "goclaudaddy.db")
    _fail_if_real_profile()  # must not raise


def test_guard_trips_when_log_file_points_at_profile_gca(monkeypatch):
    profile = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))
    monkeypatch.setattr(_app_config, "LOG_FILE", profile / ".goclaudaddy" / "logs" / "app.log")
    with pytest.raises(RuntimeError, match="Refusing to run tests"):
        _fail_if_real_profile()


def test_guard_stays_quiet_for_tmp_log_file_path(monkeypatch):
    monkeypatch.setattr(_app_config, "LOG_FILE", Path(tempfile.gettempdir()) / "goclaudaddy" / "logs" / "app.log")
    _fail_if_real_profile()  # must not raise
