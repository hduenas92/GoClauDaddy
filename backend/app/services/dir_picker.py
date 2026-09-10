"""Native folder picker dialog. Blocking (Tkinter has no async API) — callers
must run this via asyncio.to_thread so the event loop isn't blocked.
"""

from app.logging_setup import get_logger

log = get_logger("dir_picker")


def pick_directory(initial_dir: str | None = None) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(initialdir=initial_dir or None)
    finally:
        root.destroy()
    return path or ""
