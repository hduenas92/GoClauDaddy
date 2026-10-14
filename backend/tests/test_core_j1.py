r"""RESTORATION CORE J1 frozen oracle (Opus, 2026-10-14). Copied into backend/tests/ by oracle_run.py and run from backend/.
Two halves: (A) backend contract tests (stream_parser copy, permission_broker plan-exit behaviour), (B) the Playwright UI check
plans\swarm\oraclesUI\core_j1_ui.mjs run once against THIS clone's isolated server (chat WebSocket mocked; never :8765, no CLI).
Contract (frozen; reference fix and build must match):
  copy      error frame for gateway_unreachable == COPY exactly.
  plan exit broker.decide(ExitPlanMode) -> approval_needed with can_always False; on allow_once the broker first notifies
            {'type':'turn_mode','mode':'acceptEdits','source':'plan_approved'} and returns
            {'behavior':'allow','updatedInput':<input>,'updatedPermissions':[{'type':'setMode','mode':'acceptEdits','destination':'session'}]};
            deny returns a plain deny, no turn_mode, no updatedPermissions; no rule is ever created for ExitPlanMode; the scope is
            per turn (a new turn's Write prompts normally); other tools never get updatedPermissions.
  UI half   see the header of core_j1_ui.mjs.
LIVE mechanism check (not in this file, J1 build acceptance): the real CLI 2.1.296 honours setMode through
--permission-prompt-tool; a Write after plan approval runs with no second card, a Bash still raises a card."""
import asyncio
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

import app.services.permission_broker as B
import app.services.permissions as P
from app.services.stream_parser import parse_line

COPY = "Can't reach Claude. Check your internet connection. If your company requires the VPN, connect to it and try again."
SETMODE = [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
CWD = r"C:\work\proj"
PLAN = {"plan": "1. Create p.txt\n2. Write hello"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def fast(monkeypatch, temp_db):
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(B, "APPROVAL_MAX_SECONDS", 2.0)
    return B.PermissionBroker()


class Ui:
    def __init__(self, broker, conv, answer=None):
        self.frames, self.broker, self.conv, self.answer = [], broker, conv, answer

    async def notify(self, frame):
        self.frames.append(frame)
        if frame["type"] == "approval_needed" and self.answer:
            asyncio.get_running_loop().call_later(0.05, self.broker.resolve, self.conv, frame["request_id"], self.answer)

    def of(self, t):
        return [f for f in self.frames if f["type"] == t]


# ------------------------------------------------------------------ 2. can't-reach copy
def test_cant_reach_copy_exact():
    line = '{"type":"result","subtype":"success","is_error":true,"api_error_status":null,"result":"API Error: Unable to connect to API"}'
    errs = [e for e in parse_line(line) if e["type"] == "error"]
    assert len(errs) == 1 and errs[0]["code"] == "gateway_unreachable", errs
    assert errs[0]["error"] == COPY, errs[0]["error"]


def test_cant_reach_copy_has_no_old_diagnosis():
    from app.services import stream_parser
    t = stream_parser._CONN_COPY["gateway_unreachable"]
    assert t == COPY and "CaaS" not in t and "meant to be used" not in t, t


# ------------------------------------------------------------------ 1. plan exit (backend half)
def test_plan_card_frame_has_no_always(fast):
    ui = Ui(fast, "c1", "allow_once")
    tok = fast.open_turn("c1", CWD, ui.notify)
    _run(fast.decide(tok, "ExitPlanMode", PLAN, "tu1"))
    need = ui.of("approval_needed")[0]
    assert need["tool"] == "ExitPlanMode" and need["input"] == PLAN and need["can_always"] is False, need


def test_plan_allow_switches_this_turn_to_accept_edits(fast):
    ui = Ui(fast, "c1", "allow_once")
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "ExitPlanMode", PLAN, "tu1"))
    assert d.get("behavior") == "allow" and d.get("updatedInput") == PLAN, d
    assert d.get("updatedPermissions") == SETMODE, d
    tm = ui.of("turn_mode")
    assert len(tm) == 1 and tm[0]["mode"] == "acceptEdits" and tm[0]["source"] == "plan_approved", ui.frames
    types = [f["type"] for f in ui.frames]
    assert types.index("approval_resolved") < types.index("turn_mode"), types
    assert P.list_rules() == [], "a plan approval must never create a saved rule"


def test_plan_deny_changes_nothing(fast):
    ui = Ui(fast, "c1", "deny")
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "ExitPlanMode", PLAN, "tu1"))
    assert d["behavior"] == "deny" and "updatedPermissions" not in d and "updatedInput" not in d, d
    assert not ui.of("turn_mode") and P.list_rules() == []


def test_plan_timeout_changes_nothing(fast):
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "ExitPlanMode", PLAN, "tu1"))
    assert d["behavior"] == "deny" and "updatedPermissions" not in d and not ui.of("turn_mode"), (d, ui.frames)


def test_other_tools_never_get_a_mode_switch(fast):
    ui = Ui(fast, "c1", "allow_once")
    tok = fast.open_turn("c1", CWD, ui.notify)
    for name, inp in (("Bash", {"command": "echo hi"}), ("Write", {"file_path": "a.txt", "content": "x"})):
        d = _run(fast.decide(tok, name, inp, "t_" + name))
        assert d == {"behavior": "allow", "updatedInput": inp}, d
    assert not ui.of("turn_mode")


def test_scope_is_this_turn_only(fast):
    ui = Ui(fast, "c1", "allow_once")
    t1 = fast.open_turn("c1", CWD, ui.notify)
    _run(fast.decide(t1, "ExitPlanMode", PLAN, "tu1"))
    fast.close_turn(t1)
    n_before = len(ui.of("approval_needed"))
    t2 = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(t2, "Write", {"file_path": "b.txt", "content": "x"}, "tu2"))
    assert len(ui.of("approval_needed")) == n_before + 1, "a new turn must ask again; the plan approval does not carry over"
    assert "updatedPermissions" not in d and len(ui.of("turn_mode")) == 1


# ------------------------------------------------------------------ UI half
MJS = str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "core-j1-ui-check.mjs")
CHECKS = [
    "plan_card_shows_plan", "plan_card_no_raw_tool_name", "plan_card_no_always", "plan_card_one_decision", "plan_card_escapes_html",
    "turn_mode_notice_visible", "turn_mode_notice_gone_after_done",
    "saved_rule_notice_absent_without_rule", "saved_rule_notice_after_done",
    "mode_label_manual", "mode_label_acceptEdits", "mode_label_plan", "mode_label_bypassPermissions",
    "interrupted_row_terminal_label", "live_tool_row_still_running",
]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    if not shutil.which("node") or not (pathlib.Path(__file__).resolve().parents[2] / "node_modules" / "playwright").is_dir():
        pytest.skip("node or node_modules/playwright missing: run npm install in the app root")
    home = tmp_path_factory.mktemp("j1home")
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
def test_j1_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
