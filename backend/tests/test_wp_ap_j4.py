r"""WP-AP J4 frozen oracle wrapper (Opus, 2026-10-13): frontend for the approval card, the 4-option permission-mode picker
with the CLI-reported effective mode, and the Always-allow rules list. Copied into backend/tests/ by oracle_run.py and run
from backend/. Boots THIS clone's server on a free port with an isolated home (never :8765, never the live DB, no CLI:
the chat WebSocket is mocked in the browser) and runs the Playwright check plans\swarm\oraclesUI\ap_j4_ui.mjs once; each
test below asserts one check line. Contract (ids, frames, texts) is in the .mjs header and in the J4 task JSON."""
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

MJS = str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "wp-ap-ui-check.mjs")
CHECKS = [
    "card_shows_without_flag", "card_tool", "card_command", "card_cwd", "card_three_buttons", "no_auto_deny",
    "allow_once_frame", "allow_once_closes", "allow_always_frame", "deny_frame", "no_always_when_cannot",
    "resolved_dismisses", "resolved_other_id_keeps", "card_escapes_html", "extend_carries_request_id",
    "effective_ok", "effective_mismatch", "rules_empty", "rules_listed", "rules_escape_html", "revoke_calls_delete",
    "revoke_rerenders",
]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    if not shutil.which("node") or not (pathlib.Path(__file__).resolve().parents[2] / "node_modules" / "playwright").is_dir():
        pytest.skip("node or node_modules/playwright missing: run npm install in the app root")
    home = tmp_path_factory.mktemp("j4home")
    port = _free_port()
    assert port != 8765
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home))
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
def test_j4_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
