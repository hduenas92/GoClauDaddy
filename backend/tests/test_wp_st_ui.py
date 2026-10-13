r"""WP-ST J2 frozen oracle wrapper (Opus, 2026-10-13): boots THIS clone's server on a free port with an isolated home and
the WP-ST replay fake CLI first on PATH (never :8765, never the live DB, never the real CLI), then runs the Playwright check
tools/wp-st-states-check.mjs (copy of plans/swarm/oraclesUI/st_j2_states.mjs). Run from backend/.
Skips when node or the app's node_modules/playwright is missing (fresh clones have no node_modules).
Shipped 2026-10-13 (WP-ST close-out) from plans/swarm/oraclesST, frozen copy; only the paths changed (fixtures now live
in tests/fixtures/wp_st, byte-identical to the originals). Original SHA256 per plans/swarm/oraclesST/SHA256.txt: 3a497ccd723019a1c0a497e32d0d429ae89b1b0e665a07141e2dd8790e39579b swarmST_j2_ui.py
"""
import os
import shutil
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ORA = pathlib.Path(__file__).resolve().parent / "fixtures" / "wp_st"
APP = pathlib.Path(__file__).resolve().parents[2]
MJS = str(APP / "tools" / "wp-st-states-check.mjs")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_connection_states_ui(tmp_path):
    if not shutil.which("node") or not (APP / "node_modules" / "playwright").is_dir():
        pytest.skip("node or node_modules/playwright missing: run npm install in the app root")
    port = _free_port()
    assert port != 8765
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude.cmd").write_text(f'@"{sys.executable}" "{ORA / "st_fake_cli.py"}" %*\r\n', encoding="utf-8")
    ctrl, calls = tmp_path / "scenario.txt", tmp_path / "calls.txt"
    ctrl.write_text("ok", encoding="utf-8")
    env = dict(os.environ, USERPROFILE=str(tmp_path), HOME=str(tmp_path), ST_CTRL=str(ctrl), ST_CALLS=str(calls),
               ST_SLEEP="0.25", PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=os.getcwd(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = "http://127.0.0.1:%d" % port
    try:
        for _ in range(60):
            try:
                if urllib.request.urlopen(url + "/", timeout=1).status == 200:
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("clone server did not come up on " + url)
        r = subprocess.run(["node", MJS], env=dict(env, GCA_URL=url), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=420)
        print(r.stdout.encode("ascii", "replace").decode())  # cp1252 console: never crash the wrapper on output
        assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-1500:]
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
