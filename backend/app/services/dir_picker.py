"""Native folder picker dialog. Blocking (Tkinter has no async API) — callers
must run this via asyncio.to_thread so the event loop isn't blocked.

The dialog opens on the machine running the SERVER, not the machine running the
browser. On a desktop install those are the same machine and the feature works.
On a headless one they are not, and Tk has nothing to draw on: `tk.Tk()` raises
TclError. Before 4-D6 that escaped as an unhandled 500 reading "Something went
wrong. Check the logs folder for details." — which points a Phase 5
clean-machine installer at the logs for what is not a fault at all, just a
machine with no display. It now fails by name.
"""

from app.logging_setup import get_logger

log = get_logger("dir_picker")

_UNAVAILABLE_MSG = (
    "The folder picker needs a desktop session on the machine running the "
    "server, and there isn't one here. Type the path in instead."
)


class DirPickerUnavailable(RuntimeError):
    """No display (or no Tk at all), so the native dialog cannot be opened.

    Distinct from a failure: nothing is broken, the mechanism simply is not
    available on this machine. The router maps it to 503, not 500.
    """


def pick_directory(initial_dir: str | None = None) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:  # python built without _tkinter
        log.warning("Folder picker unavailable: tkinter is not installed (%s)", exc)
        raise DirPickerUnavailable(_UNAVAILABLE_MSG) from exc

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no DISPLAY / no window station
        log.warning("Folder picker unavailable: %s", exc)
        raise DirPickerUnavailable(_UNAVAILABLE_MSG) from exc

    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(initialdir=initial_dir or None)
    finally:
        root.destroy()
    # "" is what the dialog returns when the user cancels. It is passed through
    # unchanged and means exactly that; callers must not read it as "clear the
    # field".
    return path or ""
