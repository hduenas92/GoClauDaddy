"""Regression coverage for a real crash on a genuinely fresh machine: the
first run's log directory (~/.claudioui/logs/) doesn't exist yet, and
RotatingFileHandler never creates it — it only opens the file. On a dev
machine that already had the directory from a prior run this never
surfaced, but the watchdog entrypoint calls setup_logging() before any
other startup code runs, so a missing directory crashed the app instantly
on first launch.
"""

import logging

import app.logging_setup as logging_setup


def test_setup_logging_creates_missing_log_directory(tmp_path, monkeypatch):
    fresh_log_file = tmp_path / "claudioui_home" / ".claudioui" / "logs" / "app.log"
    assert not fresh_log_file.parent.exists()

    monkeypatch.setattr(logging_setup, "LOG_FILE", fresh_log_file)
    monkeypatch.setattr(logging_setup, "_configured", False)

    root = logging.getLogger("claudioui")
    added_handlers = [h for h in root.handlers]
    try:
        logging_setup.setup_logging()
        assert fresh_log_file.parent.is_dir()
    finally:
        # Don't leak handlers bound to a tmp_path file into the rest of the suite.
        for h in list(root.handlers):
            if h not in added_handlers:
                root.removeHandler(h)
                h.close()
        logging_setup._configured = False
