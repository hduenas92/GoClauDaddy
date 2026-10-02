"""Launch a throwaway GoClaudaddy clone for the NVDA screen-reader check.

Same isolation pattern as backend/tests/test_live_survival.py:
free port (never 8765), temp USERPROFILE/HOME so Path.home()/.goclaudaddy
lands in a temp dir, and a claude.cmd shim first on PATH that runs
backend/tests/fixtures/fake_claude_cli.py instead of the real Claude CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DEFAULT_HARNESS = r"C:\Users\hduenas\LLMs\Claude\Projects\GoClaudaddy\ops\_nvda\nvda-check.mjs"
HEALTH_PATH = "/api/server/info"
HEALTH_TIMEOUT = 30.0

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FAKE_CLI_SCRIPT = BACKEND_DIR / "tests" / "fixtures" / "fake_claude_cli.py"


def _server_python() -> Path:
    venv_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return venv_python if venv_python.exists() else Path(sys.executable)


def _free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port != 8765:  # never, ever the live app's port
            return port


def _env_for_server(home: Path, bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["ANTHROPIC_AUTH_TOKEN"] = "nvda-clone-dummy"
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"
    return env


def _wait_for_server(url: str, proc: subprocess.Popen, timeout: float = HEALTH_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with opener.open(url + HEALTH_PATH, timeout=1.0) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _close_log(log_fh) -> None:
    if log_fh is not None and not log_fh.closed:
        try:
            log_fh.close()
        except OSError:
            pass


def _kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=20,
        )
    except Exception:
        pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass


def _remove_tree(tmp_dir: Path) -> None:
    shutil.rmtree(tmp_dir, ignore_errors=True)
    time.sleep(1)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _tail(path: Path, lines: int = 20) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="start the clone, print one JSON line, then exit 0",
    )
    parser.add_argument(
        "--harness",
        default=DEFAULT_HARNESS,
        help="path to the NVDA harness (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    tmp_dir = Path(tempfile.mkdtemp(prefix="nvda-clone-"))
    home = tmp_dir / "home"
    bin_dir = tmp_dir / "bin"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    claude_shim = bin_dir / "claude.cmd"
    claude_shim.write_text(
        f'@echo off\r\n"{Path(sys.executable)}" "{FAKE_CLI_SCRIPT}" %*\r\n',
        encoding="ascii",
    )

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log_path = tmp_dir / "server.log"
    log_fh = log_path.open("ab")
    server_proc: subprocess.Popen | None = None
    node_proc: subprocess.Popen | None = None

    try:
        server_proc = subprocess.Popen(
            [
                str(_server_python()),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(BACKEND_DIR),
            env=_env_for_server(home, bin_dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

        if not _wait_for_server(url, server_proc):
            _close_log(log_fh)
            tail = _tail(log_path)
            if tail:
                print(f"server log tail:\n{tail}", file=sys.stderr)
            print(
                f"server at {url} did not answer {HEALTH_PATH} within {HEALTH_TIMEOUT}s "
                f"(exit code: {server_proc.poll()})",
                file=sys.stderr,
            )
            return 3

        if args.selftest:
            print(json.dumps({
                "url": url,
                "port": port,
                "home": str(home),
                "claude_shim": str(claude_shim),
            }))
            return 0

        harness = Path(args.harness).resolve()
        node_env = os.environ.copy()
        node_env["GCA_URL"] = url
        node_proc = subprocess.Popen(
            ["node", str(harness)],
            cwd=str(harness.parent),
            env=node_env,
        )
        return node_proc.wait()
    finally:
        _close_log(log_fh)
        try:
            _kill_tree(server_proc)
        except Exception:
            pass
        try:
            _kill_tree(node_proc)
        except Exception:
            pass
        try:
            _remove_tree(tmp_dir)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
