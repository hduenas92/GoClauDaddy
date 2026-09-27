"""Rotating file logging — the primary debugging tool for an app nobody is watching live."""

import logging
from logging.handlers import RotatingFileHandler

from app.config import LOG_FILE

_configured = False


def setup_logging(debug: bool = False) -> None:
    global _configured
    if _configured:
        return
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger("goclaudaddy")
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"goclaudaddy.{name}")
