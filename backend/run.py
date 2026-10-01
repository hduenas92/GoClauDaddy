"""Entrypoint: runs startup checks, then serves the app on 127.0.0.1 only."""

import subprocess
import sys
import webbrowser
from pathlib import Path
from threading import Timer

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app.config import DATA_DIR, HOST, PORT, ensure_dirs
from app.data_migration import DataMigrationError, migrate_data_dir
from app.logging_setup import get_logger, setup_logging
from app.startup_check import StartupCheckError, run_startup_checks

log = get_logger("run")

_SHORTCUT_FLAG = DATA_DIR / ".shortcut_created"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # ClaudioUI/


def _create_desktop_shortcut() -> None:
    """Create a GoClaudaddy.lnk on the Windows desktop (best-effort, never raises)."""
    if sys.platform != "win32":
        return
    try:
        bat = _PROJECT_ROOT / "Launch GoClaudaddy.bat"
        if bat.exists():
            target = str(bat)
            args = ""
            workdir = str(_PROJECT_ROOT)
        else:
            # Fallback: run via pythonw (no console window)
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            target = str(pythonw if pythonw.exists() else sys.executable)
            args = f'"{Path(__file__).resolve()}"'
            workdir = str(Path(__file__).resolve().parent)

        # Resolve the desktop inside PowerShell: OneDrive Known Folder Move
        # redirects it away from Path.home() / "Desktop".
        ps = (
            f'$desktop=[Environment]::GetFolderPath(\'Desktop\');'
            f'$lnk=Join-Path $desktop \'GoClaudaddy.lnk\';'
            f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut($lnk);'
            f'$s.TargetPath="{target}";'
            f'$s.Arguments=\'{args}\';'
            f'$s.WorkingDirectory="{workdir}";'
            f'$s.Description="GoClaudaddy - Private Claude Interface";'
            f'$s.Save()'
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            _SHORTCUT_FLAG.touch()
            log.info("Created desktop shortcut")
        else:
            stderr = (result.stderr or b"").decode(errors="replace").strip()
            log.warning(
                "Could not create desktop shortcut (powershell exit %s): %s",
                result.returncode,
                stderr,
            )
    except Exception as exc:  # noqa: BLE001
        # Shortcut is a nice-to-have; never block startup.
        log.warning("Could not create desktop shortcut: %s", exc)


def main() -> None:
    ensure_dirs()
    setup_logging()
    if not _SHORTCUT_FLAG.exists():
        _create_desktop_shortcut()
    try:
        migrate_data_dir()
    except DataMigrationError as exc:
        print(f"GoClaudaddy data migration failed: {exc}")
        log.error("Data migration failed: %s", exc, exc_info=True)
        sys.exit(1)
    try:
        run_startup_checks()
    except StartupCheckError as exc:
        print(f"GoClaudaddy failed to start: {exc}")
        log.error("Startup check failed: %s", exc)
        sys.exit(1)

    url = f"http://{HOST}:{PORT}"
    Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
