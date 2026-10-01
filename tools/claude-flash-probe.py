"""Probe the REAL claude CLI process tree for console-window flashes (P2-P2).

Spawns the real claude CLI exactly the way backend/app/services/claude_cli.py
does (same argv shape, same resolved executable, inherited environment, same
stdin/stdout/stderr pipes) and polls the descendant process tree + visible
top-level windows every 100 ms from spawn until exit.

Variants:
  A  creationflags=CREATE_NO_WINDOW, cwd=<repo>           (current behaviour)
  B  CREATE_NEW_CONSOLE + STARTF_USESHOWWINDOW/SW_HIDE, cwd=<repo>
  C  like A, but cwd=<fresh temp dir that is not a git repo>  (control)

Also honours GCA_SPAWN_LEGACY for the post-fix comparison:
  GCA_SPAWN_LEGACY=1  -> legacy CREATE_NO_WINDOW kwargs (A-style)
  otherwise           -> the fix kwargs (CREATE_NEW_CONSOLE + hidden STARTUPINFO)

Exit codes:
  0  every run finished and no INCONCLUSIVE desktop/session situation
  2  session 0 / non-interactive desktop (window checks meaningless here)
  3  a run failed to produce a stream-json result line
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from ctypes import wintypes

POLL_INTERVAL = 0.1
RUN_TIMEOUT = 120.0
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
DESKTOP_READOBJECTS = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

MODEL = "claude-sonnet-4-6"  # backend/app/config.py:47 DEFAULT_MODEL
PROMPT = "Reply with the single word ok."

REPO_CWD = r"C:\Users\hduenas\LLMs\Claude\Projects\GoClaudaddy\app"

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


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", wintypes.WORD),
        ("wMonth", wintypes.WORD),
        ("wDayOfWeek", wintypes.WORD),
        ("wDay", wintypes.WORD),
        ("wHour", wintypes.WORD),
        ("wMinute", wintypes.WORD),
        ("wSecond", wintypes.WORD),
        ("wMilliseconds", wintypes.WORD),
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
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.FileTimeToLocalFileTime.argtypes = [ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
    kernel32.FileTimeToLocalFileTime.restype = wintypes.BOOL
    kernel32.FileTimeToSystemTime.argtypes = [ctypes.POINTER(FILETIME), ctypes.POINTER(SYSTEMTIME)]
    kernel32.FileTimeToSystemTime.restype = wintypes.BOOL

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


def _snapshot() -> dict[int, tuple[int, str]]:
    """Full process table: pid -> (ppid, exe name)."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return {}
    procs: dict[int, tuple[int, str]] = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            procs[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return procs


def _descendants(procs: dict[int, tuple[int, str]], root_pid: int) -> set[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for pid, (ppid, _exe) in procs.items():
        children[ppid].append(pid)
    descendants: set[int] = set()
    frontier = [root_pid]
    while frontier:
        pid = frontier.pop()
        for child in children.get(pid, []):
            if child not in descendants:
                descendants.add(child)
                frontier.append(child)
    return descendants


def _descendants_verified(
    procs: dict[int, tuple[int, str]],
    root_pid: int,
    seen: dict[int, tuple[int, str, str]],
) -> set[int]:
    """BFS from root over ppid edges, guarded against pid reuse.

    An edge parent->child is trusted only when parent is the root (its pid
    cannot be reused while it is alive) or the current snapshot's process at
    `parent` still matches the create time / exe name we recorded when the
    parent was first verified. Without this, a short-lived tree process whose
    pid is reused lets unrelated processes be misattributed to our tree
    (observed on the first probe run: an ex-where.exe pid was reused and
    pulled M365Copilot.exe + msedgewebview2.exe into the "tree").
    """
    children: dict[int, list[int]] = defaultdict(list)
    for pid, (ppid, _exe) in procs.items():
        children[ppid].append(pid)
    result: set[int] = set()
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        for child in children.get(parent, []):
            if child in result or child == root_pid:
                continue
            if parent != root_pid:
                stored = seen.get(parent)
                if stored is None:
                    continue
                stored_exe = stored[1]
                stored_created = stored[2]
                cur_exe = procs.get(parent, (0, ""))[1]
                if cur_exe.lower() != stored_exe.lower():
                    continue
                if stored_created != "?":
                    cur_created = _create_time(parent)
                    if cur_created != stored_created:
                        continue
            result.add(child)
            frontier.append(child)
    return result


def _create_time(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "?"
    created = FILETIME()
    exited = FILETIME()
    kernel_t = FILETIME()
    user_t = FILETIME()
    ok = kernel32.GetProcessTimes(
        handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_t), ctypes.byref(user_t)
    )
    kernel32.CloseHandle(handle)
    if not ok:
        return "?"
    local = FILETIME()
    if not kernel32.FileTimeToLocalFileTime(ctypes.byref(created), ctypes.byref(local)):
        return "?"
    st = SYSTEMTIME()
    if not kernel32.FileTimeToSystemTime(ctypes.byref(local), ctypes.byref(st)):
        return "?"
    return (
        f"{st.wYear:04d}-{st.wMonth:02d}-{st.wDay:02d} "
        f"{st.wHour:02d}:{st.wMinute:02d}:{st.wSecond:02d}.{st.wMilliseconds:03d}"
    )


def _visible_windows(
    descendants: set[int],
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Return (tree-owned visible windows, visible windows whose title contains git.exe)."""
    tree_windows: list[tuple[int, str]] = []
    git_windows: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(length + 1, 1))
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if "git.exe" in title.lower():
            git_windows.append((pid.value, title))
        if pid.value in descendants:
            tree_windows.append((pid.value, title))
        return True

    user32.EnumWindows(_callback, 0)
    return tree_windows, git_windows


def _spawn_kwargs_for_variant(variant: str) -> dict:
    if variant == "B":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
        return {"creationflags": CREATE_NEW_CONSOLE, "startupinfo": startupinfo}
    return {"creationflags": CREATE_NO_WINDOW}


def _fix_kwargs() -> dict:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = SW_HIDE
    return {"creationflags": CREATE_NEW_CONSOLE, "startupinfo": startupinfo}


def _legacy_kwargs() -> dict:
    return {"creationflags": CREATE_NO_WINDOW}


def _resolve_variant(variant: str) -> tuple[dict, str, str]:
    """Return (spawn kwargs, cwd, label)."""
    if variant == "A":
        return _spawn_kwargs_for_variant("A"), REPO_CWD, "A"
    if variant == "B":
        return _spawn_kwargs_for_variant("B"), REPO_CWD, "B"
    if variant == "C":
        tmp = tempfile.mkdtemp(prefix="claude-flash-probe-C-")
        return _spawn_kwargs_for_variant("A"), tmp, "C"
    # "auto": legacy vs fix decided by GCA_SPAWN_LEGACY
    if os.environ.get("GCA_SPAWN_LEGACY") == "1":
        return _legacy_kwargs(), REPO_CWD, "legacy"
    return _fix_kwargs(), REPO_CWD, "fix"


def _read_stream(stream, sink: list[bytes]) -> None:
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return
        sink.append(chunk)


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_probe(variant: str, run_index: int) -> int:
    spawn_kwargs, cwd, label = _resolve_variant(variant)
    resolved = shutil.which("claude")
    if not resolved:
        print(f"FATAL claude not found on PATH")
        return 3
    cmd = [resolved, "-p", "--output-format", "stream-json", "--verbose", "--model", MODEL, PROMPT]
    flags_desc = (
        "CREATE_NEW_CONSOLE+STARTF_USESHOWWINDOW/SW_HIDE"
        if spawn_kwargs.get("creationflags") == CREATE_NEW_CONSOLE
        else "CREATE_NO_WINDOW"
    )
    print(f"=== RUN variant={label} run={run_index} flags={flags_desc} cwd={cwd} ===")
    print(f"SPAWN argv={cmd}")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        **spawn_kwargs,
    )
    root_pid = proc.pid
    spawn_monotonic = time.monotonic()
    print(f"ROOT pid={root_pid} exe=claude.exe created={_create_time(root_pid)}")

    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    t_out = threading.Thread(target=_read_stream, args=(proc.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_read_stream, args=(proc.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()

    seen: dict[int, tuple[int, str, str]] = {}  # pid -> (ppid, exe, created)
    known_conhosts: dict[int, tuple[int, str]] = {}  # pid -> (ppid, created)
    tree_windows_acc: set[tuple[int, str]] = set()
    git_windows_acc: set[tuple[int, str]] = set()
    git_pids: set[int] = set()
    baseline_conhosts: set[int] = set()
    first_snapshot = _snapshot()
    for pid, (ppid, exe) in first_snapshot.items():
        if exe.lower() == "conhost.exe":
            baseline_conhosts.add(pid)

    timed_out = False
    while True:
        procs = _snapshot()
        descendants = _descendants_verified(procs, root_pid, seen)

        for pid in descendants:
            if pid in seen:
                continue
            ppid, exe = procs.get(pid, (0, ""))
            created = _create_time(pid)
            seen[pid] = (ppid, exe, created)
            print(f"PROC pid={pid} ppid={ppid} exe={exe} created={created}")
            if exe.lower() == "git.exe":
                git_pids.add(pid)

        for pid, (ppid, exe) in procs.items():
            if exe.lower() != "conhost.exe":
                continue
            if pid in baseline_conhosts:
                continue
            if pid in known_conhosts:
                continue
            created = _create_time(pid)
            known_conhosts[pid] = (ppid, created)
            in_tree = "tree" if pid in descendants else "other"
            print(f"CONHOST pid={pid} ppid={ppid} created={created} scope={in_tree}")

        tree_windows, git_windows = _visible_windows(descendants)
        for entry in tree_windows:
            if entry not in tree_windows_acc:
                tree_windows_acc.add(entry)
                print(f"WINDOW pid={entry[0]} title={entry[1]!r}")
        for entry in git_windows:
            if entry not in git_windows_acc:
                git_windows_acc.add(entry)
                print(f"GITWINDOW pid={entry[0]} title={entry[1]!r}")

        if proc.poll() is not None:
            break
        if time.monotonic() - spawn_monotonic > RUN_TIMEOUT:
            timed_out = True
            print("TIMEOUT killing tree")
            _kill_tree(proc)
            break
        time.sleep(POLL_INTERVAL)

    # One final pass right after exit so late tree members are still caught.
    procs = _snapshot()
    descendants = _descendants_verified(procs, root_pid, seen)
    for pid in descendants:
        if pid in seen:
            continue
        ppid, exe = procs.get(pid, (0, ""))
        created = _create_time(pid)
        seen[pid] = (ppid, exe, created)
        print(f"PROC pid={pid} ppid={ppid} exe={exe} created={created}")
        if exe.lower() == "git.exe":
            git_pids.add(pid)
    tree_windows, git_windows = _visible_windows(descendants)
    for entry in tree_windows:
        if entry not in tree_windows_acc:
            tree_windows_acc.add(entry)
            print(f"WINDOW pid={entry[0]} title={entry[1]!r}")
    for entry in git_windows:
        if entry not in git_windows_acc:
            git_windows_acc.add(entry)
            print(f"GITWINDOW pid={entry[0]} title={entry[1]!r}")

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    rc = proc.returncode

    last_procs = _snapshot()
    for pid in sorted(git_pids):
        ppid, exe, created = seen.get(pid, (0, "", "?"))
        parent_exe = seen.get(ppid, (0, "", "?"))[1] or last_procs.get(ppid, (0, ""))[1] or "?"
        print(f"GITPROC pid={pid} ppid={ppid} parent_exe={parent_exe} created={created}")

    for pid, title in sorted(tree_windows_acc):
        print(f"WINDOW_FINAL pid={pid} title={title!r}")
    for pid, title in sorted(git_windows_acc):
        print(f"GITWINDOW_FINAL pid={pid} title={title!r}")

    stdout_text = b"".join(out_chunks).decode("utf-8", errors="replace")
    stderr_text = b"".join(err_chunks).decode("utf-8", errors="replace")
    for line in stdout_text.splitlines():
        print(f"CLAUDE_OUT {line}")
    for line in stderr_text.splitlines():
        print(f"CLAUDE_ERR {line}")

    result_received = any('"type":"result"' in line for line in stdout_text.splitlines())
    print(f"CHECK probe variant={label} visible_windows={len(tree_windows_acc)} git_procs={len(git_pids)}")
    print(f"CHECK result_received={1 if result_received else 0} rc={rc} timed_out={1 if timed_out else 0}")
    if not result_received:
        return 3
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default="auto", help="A, B, C, or auto (default: GCA_SPAWN_LEGACY decides)")
    parser.add_argument("--runs", type=int, default=1, help="number of runs for the chosen variant")
    parser.add_argument("--all", action="store_true", help="run A, B, C twice each (the 6-call experiment)")
    args = parser.parse_args()

    _configure_ctypes()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    session = _session_id()
    interactive = _interactive_desktop_available()
    if session == 0 or not interactive:
        print(f"INCONCLUSIVE session={session} interactive_desktop={interactive}")
        return 2

    rc_total = 0
    if args.all:
        for variant in ("A", "B", "C"):
            for run_index in (1, 2):
                rc = run_probe(variant, run_index)
                if rc:
                    rc_total = rc
                if variant == "C":
                    # temp dir was created inside run_probe; nothing to clean here
                    pass
    else:
        for run_index in range(1, args.runs + 1):
            rc = run_probe(args.variant, run_index)
            if rc:
                rc_total = rc
    return rc_total


if __name__ == "__main__":
    sys.exit(main())
