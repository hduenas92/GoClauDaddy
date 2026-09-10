#!/usr/bin/env python3
"""ClaudioUi Launcher — monitor and relaunch the PS1 server."""

import tkinter as tk
import subprocess
import threading
import os
import sys
import time
import webbrowser

try:
    import urllib.request as urlreq
    import urllib.error   as urlerr
except ImportError:
    urlreq = urlerr = None

PORT    = 8765
URL     = f"http://127.0.0.1:{PORT}"
SCRIPT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claudioui_server.py")

BG      = "#000308"
MANTLE  = "#040a12"
SURF0   = "#0d1f2d"
SURF1   = "#0f2535"
TEXT    = "#d6f4ff"
BLUE    = "#00d4ff"
GREEN   = "#00ff9c"
RED     = "#ff7518"
DIM     = "#38606f"

DETACHED = 0x00000008   # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS on Windows


def _ping() -> bool:
    """Return True if the server responds on /api/state."""
    try:
        r = urlreq.urlopen(f"{URL}/api/state", timeout=2)
        return r.getcode() == 200
    except Exception:
        return False


class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("ClaudioUi Launcher")
        root.geometry("340x182")
        root.resizable(False, False)
        root.configure(bg=BG)
        try:
            root.iconbitmap(default="")
        except Exception:
            pass

        self._launching = False
        self._build()
        self._poll()

    def _build(self):
        # ── Title bar ──────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=MANTLE, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ClaudioUi", bg=MANTLE, fg=BLUE,
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=16)
        tk.Label(hdr, text=f":{PORT}", bg=MANTLE, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left")

        # ── Status row ─────────────────────────────────────────
        mid = tk.Frame(self.root, bg=BG, pady=14)
        mid.pack(fill="x")

        self._dot = tk.Label(mid, text="◆", bg=BG, fg=DIM,
                              font=("Segoe UI", 11))
        self._dot.pack(side="left", padx=(20, 8))

        self._lbl = tk.Label(mid, text="Checking…", bg=BG, fg=DIM,
                              font=("Segoe UI", 10))
        self._lbl.pack(side="left")

        # ── Buttons ────────────────────────────────────────────
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(pady=(0, 14))

        def btn(parent, text, cmd, fg=TEXT, **kw):
            return tk.Button(parent, text=text, command=cmd,
                             bg=SURF1, fg=fg, activebackground=SURF0,
                             activeforeground=fg, relief="flat", bd=0,
                             font=("Segoe UI", 9), padx=14, pady=6,
                             cursor="hand2", **kw)

        self._launch_btn  = btn(btns, "Launch",       self._do_launch, fg=BLUE)
        self._stop_btn    = btn(btns, "Stop",          self._do_stop,   fg=RED)
        self._restart_btn = btn(btns, "↺  Restart",   self._do_restart, fg=BLUE)
        self._browser_btn = btn(btns, "Open Browser", self._do_browser, fg=GREEN)

        for b in (self._launch_btn, self._stop_btn,
                  self._restart_btn, self._browser_btn):
            b.pack(side="left", padx=3)

        self._set_state(None)   # unknown until first poll

    def _set_state(self, online):
        if online is None:
            self._dot.config(fg=DIM)
            self._lbl.config(text="Checking…", fg=DIM)
            self._launch_btn.config(state="disabled")
            self._stop_btn.config(state="disabled")
            self._restart_btn.config(state="disabled")
            self._browser_btn.config(state="disabled")
        elif online:
            self._dot.config(fg=GREEN)
            self._lbl.config(text="Server running", fg=GREEN)
            self._launch_btn.config(state="disabled")
            self._stop_btn.config(state="normal")
            self._restart_btn.config(state="normal")
            self._browser_btn.config(state="normal")
        else:
            self._dot.config(fg=RED)
            self._lbl.config(text="Server offline", fg=RED)
            self._launch_btn.config(state="normal" if not self._launching else "disabled")
            self._stop_btn.config(state="disabled")
            self._restart_btn.config(state="disabled")
            self._browser_btn.config(state="disabled")

    def _poll(self):
        if not self._launching:
            online = _ping()
            self._set_state(online)
        self.root.after(3000, self._poll)

    def _launch_server(self):
        """Start the PS1 server in a detached process (background thread)."""
        self._launching = True
        self.root.after(0, lambda: self._lbl.config(text="Starting…", fg=BLUE))
        self.root.after(0, lambda: self._launch_btn.config(state="disabled"))

        flags = DETACHED if sys.platform == "win32" else 0
        subprocess.Popen(
            [sys.executable, SCRIPT],
            cwd=os.path.dirname(SCRIPT),
            creationflags=flags,
            close_fds=True,
        )

        # Wait up to 12 s for it to come up
        for _ in range(24):
            time.sleep(0.5)
            if _ping():
                self._launching = False
                self.root.after(0, lambda: self._set_state(True))
                return

        self._launching = False
        self.root.after(0, lambda: self._set_state(False))
        self.root.after(0, lambda: self._lbl.config(text="Launch failed — check server", fg=RED))

    def _do_launch(self):
        threading.Thread(target=self._launch_server, daemon=True).start()

    def _do_stop(self):
        try:
            req = urlreq.Request(f"{URL}/api/shutdown", data=b"", method="POST")
            urlreq.urlopen(req, timeout=3)
        except Exception:
            pass
        self.root.after(1800, lambda: self._set_state(_ping()))

    def _do_restart(self):
        self._lbl.config(text="Restarting…", fg=BLUE)
        try:
            req = urlreq.Request(f"{URL}/api/restart", data=b"", method="POST")
            urlreq.urlopen(req, timeout=3)
        except Exception:
            pass
        # Give the new process time to bind the port
        self.root.after(3500, lambda: self._set_state(_ping()))

    def _do_browser(self):
        webbrowser.open(URL)


if __name__ == "__main__":
    root = tk.Tk()
    Launcher(root)
    root.mainloop()
