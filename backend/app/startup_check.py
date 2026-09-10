"""Fail loudly with one clear reason before serve_forever, rather than hanging or crashing deep inside a request."""

import os
import shutil
import socket

from app.config import ATTACHMENTS_DIR, DATA_DIR, HOST, PORT, REQUIRED_AUTH_ENV_VARS
from app.logging_setup import get_logger

log = get_logger("startup")


class StartupCheckError(RuntimeError):
    pass


def _check_writable(path) -> None:
    probe = path / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise StartupCheckError(f"Cannot write to {path}: {exc}") from exc


def _check_port_free() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, PORT))
        except OSError as exc:
            raise StartupCheckError(
                f"Port {PORT} is already in use — is another ClaudioUI instance running? ({exc})"
            ) from exc


def _check_claude_on_path() -> None:
    if shutil.which("claude") is None:
        raise StartupCheckError(
            "The `claude` CLI was not found on PATH. Install it before running ClaudioUI."
        )


def _check_auth_env_vars() -> None:
    # Presence only — never read the value into a log line or exception message.
    missing = [name for name in REQUIRED_AUTH_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise StartupCheckError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". These must be set as PERSISTENT user environment variables "
            + "(e.g. via `setx`, not a one-off `$env:` in a single PowerShell window) — "
            + "see the 'One-time setup' section at the top of README.md for the exact commands."
        )


def run_startup_checks() -> None:
    _check_writable(DATA_DIR)
    _check_writable(ATTACHMENTS_DIR)
    _check_port_free()
    _check_claude_on_path()
    _check_auth_env_vars()
    log.info(
        "Startup checks passed (data dir writable, port %d free, claude CLI + auth env vars present)", PORT
    )
