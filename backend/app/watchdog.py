"""Auto-restart the server process once on an unexpected exit, capped so a
persistent bug becomes a visible stopped state instead of an infinite crash
loop. Runs as the actual launcher entrypoint (invoked instead of run.py
directly); run.py itself stays a plain, restartable child process.
"""

import subprocess
import sys
import time
from pathlib import Path

from app.logging_setup import get_logger, setup_logging

log = get_logger("watchdog")

MAX_RESTARTS = 3
RESTART_WINDOW_SECONDS = 300  # 5 minutes


def main() -> int:
    setup_logging()
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    restart_times: list[float] = []

    while True:
        log.info("Starting ClaudioUI server process")
        proc = subprocess.run([sys.executable, str(run_py)])
        rc = proc.returncode
        if rc == 0:
            log.info("ClaudioUI server exited cleanly (rc=0) — not restarting")
            return 0

        now = time.time()
        restart_times.append(now)
        restart_times[:] = [t for t in restart_times if now - t < RESTART_WINDOW_SECONDS]

        log.warning("ClaudioUI server exited unexpectedly (rc=%s)", rc)
        if len(restart_times) > MAX_RESTARTS:
            log.error(
                "Exited %d times within %ds — not restarting again. "
                "Check the log at ~/.claudioui/logs/app.log before trying again.",
                len(restart_times),
                RESTART_WINDOW_SECONDS,
            )
            return 1

        log.info("Restarting (%d/%d in this window)...", len(restart_times), MAX_RESTARTS)
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
