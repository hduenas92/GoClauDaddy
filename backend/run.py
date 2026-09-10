"""Entrypoint: runs startup checks, then serves the app on 127.0.0.1 only."""

import sys
import webbrowser
from pathlib import Path
from threading import Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app.config import HOST, PORT, ensure_dirs
from app.logging_setup import get_logger, setup_logging
from app.startup_check import StartupCheckError, run_startup_checks

log = get_logger("run")


def main() -> None:
    ensure_dirs()
    setup_logging()
    try:
        run_startup_checks()
    except StartupCheckError as exc:
        print(f"ClaudioUI failed to start: {exc}")
        log.error("Startup check failed: %s", exc)
        sys.exit(1)

    url = f"http://{HOST}:{PORT}"
    Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
