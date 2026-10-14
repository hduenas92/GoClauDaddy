r"""RESTORATION CORE J2 frozen oracle (Opus, 2026-10-14): screen-reader announcements. Copied into backend/tests/ by oracle_run.py and run from
backend/. One Playwright run (plans\swarm\oraclesUI\core_j2_ui.mjs) against THIS clone's isolated server (random port, temp HOME, the WP-ST
replay fake CLI first on PATH; never :8765, never the real CLI) and one pytest test per check id. Contract: see the header of core_j2_ui.mjs
(Q4 ruling: a retry loop announces the first retry only; the settled error is spoken once; a failed bubble carries its reason; a normal turn
announces completion once). The recorder counts live-region announcements as an UPPER bound; hearing it is the J10 NVDA lane / Houston's box."""
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ORA = str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "st_fake_cli" / "st_fake_cli.py")
MJS = str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "core-j2-ui-check.mjs")
CHECKS = [
    "recorder_selftest",
    "ok_completion_once", "ok_single_announcement", "ok_no_turn_chatter", "ok_single_announcement_repeat", "spend_toast_once_per_session", "ok_reply_text_announced", "ok_quiet_when_idle", "ok_completion_once_repeat", "ok_repeat_reply_announced",
    "retry_loop_deadproxy_one", "retry_loop_keyinvalid_one", "retry_loop_recover_one",
    "error_once_deadproxy", "error_once_keyinvalid", "error_once_keymissing", "error_once_generic",
    "error_announced_at_least_once", "error_bubble_has_reason",
]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    if not shutil.which("node") or not (pathlib.Path(__file__).resolve().parents[2] / "node_modules" / "playwright").is_dir():
        pytest.skip("node or node_modules/playwright missing: run npm install in the app root")
    tmp = tmp_path_factory.mktemp("j2home")
    port = _free_port()
    assert port != 8765
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude.cmd").write_text(f'@"{sys.executable}" "{ORA}" %*\r\n', encoding="utf-8")
    ctrl, calls = tmp / "scenario.txt", tmp / "calls.txt"
    ctrl.write_text("ok", encoding="utf-8")
    env = dict(os.environ, USERPROFILE=str(tmp), HOME=str(tmp), ST_CTRL=str(ctrl), ST_CALLS=str(calls), ST_SLEEP="0.05",
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
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
                           encoding="utf-8", errors="replace", timeout=300)
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
    lines = {}
    for ln in r.stdout.splitlines():
        m = re.match(r"^(PASS|FAIL) (\w+)", ln)
        if m:
            lines.setdefault(m.group(2), (m.group(1), ln))
    return lines, r.stdout[-3000:] + r.stderr[-1500:]


@pytest.mark.parametrize("check", CHECKS)
def test_j2_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
