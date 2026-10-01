"""Detect a visible console window flashing from a spawned process tree.

P2-P hypothesis checker. It spawns a parent console program through the same
two flag sets the backend used / the proposed fix would use, then polls
EnumWindows for ~3s looking for VISIBLE top-level windows owned by any
descendant of the spawned tree (descendants found with a toolhelp snapshot;
no psutil dependency).

Flag sets (selected by GCA_SPAWN_LEGACY):
  - default (fix):  CREATE_NEW_CONSOLE + STARTF_USESHOWWINDOW/SW_HIDE
  - GCA_SPAWN_LEGACY=1 (legacy): CREATE_NO_WINDOW

Hypothesis under test:
  "A process started with CREATE_NO_WINDOW has no console. When it launches a
   console-subsystem child (git.exe) without CREATE_NO_WINDOW, Windows gives
   that child a NEW, VISIBLE console window."
  Predicted result: legacy -> visible_windows>0, fix -> visible_windows==0.

Measured result on this machine (session 1, Default desktop):
  legacy -> 0, fix -> 0. The hypothesis is therefore wrong: a console child of
  a console-less parent inherits nothing (it gets no console at all), per
  Microsoft's CreateProcess/console docs and Raymond Chen's Old New Thing.

Stand-in choice: the parent is python.exe, a console-subsystem program that,
like claude.exe, does NOT self-allocate a console when started without one.
cmd.exe and node.exe are NOT used as the parent because they self-allocate a
hidden console under CREATE_NO_WINDOW, which masks the behaviour under test.

Exit codes:
  0 - no visible descendant window
  1 - at least one visible descendant window (console flash reproduced)
  2 - INCONCLUSIVE: session 0 or non-interactive desktop; window visibility
      checks are meaningless here and must be run on the desktop instead.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

POLL_SECONDS = 3.0
POLL_INTERVAL = 0.05
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DESKTOP_READOBJECTS = 0x0001
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _configure_ctypes() -> None:
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

    user32.EnumWindows.argtypes = [
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
        wintypes.LPARAM,
    ]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HDESK
    user32.CloseDesktop.argtypes = [wintypes.HDESK]
    user32.CloseDesktop.restype = wintypes.BOOL


def _spawn_kwargs() -> dict:
    """The two flag sets under test (mirrors the app helper's contract)."""
    if os.environ.get("GCA_SPAWN_LEGACY") == "1":
        return {"creationflags": CREATE_NO_WINDOW}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = SW_HIDE
    return {
        "creationflags": CREATE_NEW_CONSOLE,
        "startupinfo": startupinfo,
    }


def _session_id() -> int:
    session = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        return -1
    return session.value


def _interactive_desktop_available() -> bool:
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desktop:
        return False
    user32.CloseDesktop(desktop)
    return True


def _descendant_pids(root_pid: int) -> set[int]:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return set()
    children: dict[int, list[int]] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    descendants: set[int] = set()
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child in children.get(pid, []):
            if child not in descendants:
                descendants.add(child)
                frontier.append(child)
    return descendants


def _visible_descendant_windows(descendants: set[int]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in descendants:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(length + 1, 1))
        user32.GetWindowTextW(hwnd, buf, length + 1)
        found.append((pid.value, buf.value))
        return True

    user32.EnumWindows(_callback, 0)
    return found


def _grandchild_command() -> str:
    """Console grandchild the parent will launch (long-lived ~3s)."""
    if shutil.which("git"):
        return "for /l %i in (1,1,60) do git --version >nul"
    return "ping -n 3 127.0.0.1 >nul"


_PARENT_CODE = r"""
import subprocess, sys, time
comspec = sys.argv[1]
grandchild_cmd = sys.argv[2]
proc = subprocess.Popen([comspec, "/c", grandchild_cmd])
try:
    proc.wait(timeout=6)
except Exception:
    proc.kill()
"""


def _spawn_tree() -> subprocess.Popen:
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    parent = [sys.executable, "-c", _PARENT_CODE, comspec, _grandchild_command()]
    return subprocess.Popen(
        parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_spawn_kwargs(),
    )


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            **_spawn_kwargs(),
        )
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    _configure_ctypes()

    session = _session_id()
    interactive = _interactive_desktop_available()
    if session == 0 or not interactive:
        print(f"INCONCLUSIVE session={session} interactive_desktop={interactive}")
        return 2

    proc = _spawn_tree()
    try:
        found: list[tuple[int, str]] = []
        deadline = time.monotonic() + POLL_SECONDS
        while time.monotonic() < deadline:
            descendants = _descendant_pids(proc.pid)
            found = _visible_descendant_windows(descendants)
            if found:
                break
            time.sleep(POLL_INTERVAL)
    finally:
        _kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    for pid, title in found:
        print(f"WINDOW pid={pid} title={title!r}")
    print(f"CHECK flash visible_windows={len(found)}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
