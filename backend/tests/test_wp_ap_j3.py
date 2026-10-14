"""WP-AP J3 frozen oracle (Sonnet, 2026-10-13): wiring. The CLI argv/env, the websocket protocol, the REST routes, the startup
cleanup, the init-event permission-mode echo and the removal of the retired stdin y/n approval path.
Builds on J1 (permissions.py) and J2 (permission_broker.py, /mcp/permission/{token}). Copied into backend/tests/ by oracle_run.py.

The end-to-end tests run the REAL app on a real port (uvicorn in a thread) and a fake `claude` that behaves like the real CLI
does with --permission-prompt-tool (measured 2026-10-13 against CLI 2.1.291): it POSTs tools/call to the MCP url from --mcp-config and
runs its 'command' (writes a marker file) only if the decision says allow."""
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from websockets.sync.client import connect

import app.services.claude_cli as claude_cli
import app.services.permission_broker as B
import app.services.permissions as P
from app.config import PERMISSION_MODES
from app.main import app
from app.services.stream_parser import parse_line

FAKE = r'''
import json, os, sys, urllib.request
argv = sys.argv[1:]
def arg(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None
out = os.environ["FAKE_AP_OUT"]
mode = arg("--permission-mode")
cfg = json.loads(arg("--mcp-config")) if arg("--mcp-config") else None
rec = {"argv": argv, "mcp_tool_timeout": os.environ.get("MCP_TOOL_TIMEOUT"), "cwd": os.getcwd(), "tool": arg("--permission-prompt-tool"), "cfg": cfg}
def save():
    json.dump(rec, open(out, "w"))
def emit(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
save()
echo = os.environ.get("FAKE_AP_ECHO") or ("default" if mode in ("manual", "default") else (mode or "default"))
emit({"type": "system", "subtype": "init", "session_id": "sess-1", "permissionMode": echo})
cmd = os.environ.get("FAKE_AP_CMD", "echo hi > marker.txt")
emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_f1", "name": "Bash", "input": {"command": cmd, "description": "d"}}]}})
if cfg is None:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "no prompt tool"}]}})
    emit({"type": "result", "subtype": "success", "usage": {"input_tokens": 1, "output_tokens": 1}})
    sys.exit(0)
url = cfg["mcpServers"]["gca-permission"]["url"]
rec["url"] = url; save()
def rpc(method, params, id_=1):
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if id_ is not None: body["id"] = id_
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else None
rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fake", "version": "0"}})
rpc("notifications/initialized", {}, None)
rpc("tools/list", {}, 2)
res = rpc("tools/call", {"name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": cmd, "description": "d"}, "tool_use_id": "toolu_f1"}}, 3)
dec = json.loads(res["result"]["content"][0]["text"])
rec["decision"] = dec; save()
allowed = dec.get("behavior") == "allow"
if allowed:
    open(os.path.join(os.getcwd(), os.environ.get("FAKE_AP_MARKER", "marker.txt")), "w").write("ran")
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_f1", "content": "ok" if allowed else dec.get("message", "denied"), "is_error": not allowed}]}})
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "ran" if allowed else "blocked"}]}})
emit({"type": "result", "subtype": "success", "usage": {"input_tokens": 1, "output_tokens": 1}})
'''


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Srv:
    def __init__(self):
        self.port = _free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        t0 = time.time()
        while not self.server.started and time.time() - t0 < 20:
            time.sleep(0.05)
        assert self.server.started, "server did not start"
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(15)

    @property
    def http(self):
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture()
def env(tmp_path, monkeypatch, temp_db):
    script = tmp_path / "fake_cli_ap.py"
    script.write_text(FAKE, encoding="utf-8")
    out = tmp_path / "rec.json"
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("FAKE_AP_OUT", str(out))
    spawned = []
    real_exec = claude_cli.asyncio.create_subprocess_exec

    async def fake_exec(*cmd, **kw):
        assert cmd and cmd[0] == "claude", cmd
        p = await real_exec(sys.executable, str(script), *cmd[1:], **kw)
        spawned.append(p)
        return p

    monkeypatch.setattr(claude_cli.shutil, "which", lambda _n: "claude")
    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(B, "APPROVAL_MAX_SECONDS", 6.0)
    srv = Srv().start()
    r = httpx.post(srv.http + "/api/projects", json={"name": "P", "working_dir": str(proj)})
    assert r.status_code == 200, r.text
    ns = type("Env", (), {})()
    ns.srv, ns.proj, ns.out, ns.spawned, ns.project_id = srv, proj, out, spawned, r.json()["id"]
    yield ns
    for p in spawned:
        if p.returncode is None:
            try:
                p.kill()
            except Exception:
                pass
    srv.stop()


def _conv(e, mode="manual"):
    r = httpx.post(e.srv.http + "/api/conversations", json={"project_id": e.project_id})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    if mode:
        assert httpx.patch(f"{e.srv.http}/api/conversations/{cid}/settings", json={"permission_mode": mode}).status_code == 200
    return cid


def _turn(e, cid, answer="approve", message="go", after_needed=None, recv_timeout=20):
    """Run one turn over the real websocket; answer = approve|approve_always|deny|None|'stop'|'disconnect'. Returns frames."""
    frames = []
    ws = connect(f"ws://127.0.0.1:{e.srv.port}/ws/chat/{cid}")
    try:
        ws.send(json.dumps({"type": "send", "message": message}))
        while True:
            try:
                raw = ws.recv(timeout=recv_timeout)
            except TimeoutError:
                frames.append({"type": "_client_timeout"}); break
            except Exception:
                frames.append({"type": "_closed"}); break
            f = json.loads(raw)
            frames.append(f)
            if f["type"] == "approval_needed":
                if after_needed:
                    after_needed(f)
                if answer in ("approve", "approve_always", "deny"):
                    ws.send(json.dumps({"type": answer, "request_id": f["request_id"]}))
                elif answer == "stop":
                    ws.send(json.dumps({"type": "stop"}))
                elif answer == "disconnect":
                    ws.close(); frames.append({"type": "_client_left"}); break
            if f["type"] == "done":
                break
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return frames


def _types(frames):
    return [f["type"] for f in frames]


def _marker(e):
    return e.proj / "marker.txt"


def _rec(e):
    return json.loads(e.out.read_text())


def _wait(pred, secs=8):
    t0 = time.time()
    while time.time() - t0 < secs:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


# ------------------------------------------------------------------ argv / env

def test_build_command_unchanged_without_prompt_tool():
    base = claude_cli.build_command(prompt="hi", model="m", permission_mode="plan")
    assert "--permission-prompt-tool" not in base and "--mcp-config" not in base and base[-1] == "hi"


def test_build_command_with_prompt_tool():
    url = "http://127.0.0.1:8765/mcp/permission/TOK123"
    cmd = claude_cli.build_command(prompt="do it", model="m", permission_mode="manual", session_id="s9", prompt_tool_url=url)
    assert cmd[-1] == "do it"
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__gca-permission__approve"
    cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])
    assert cfg == {"mcpServers": {"gca-permission": {"type": "http", "url": url}}}
    assert "--strict-mcp-config" not in cmd                              # the user's own MCP servers must keep working
    assert cmd[cmd.index("--permission-mode") + 1] == "manual" and cmd[cmd.index("--resume") + 1] == "s9"


def test_build_command_refuses_a_non_local_prompt_tool_url():
    for bad in ("http://evil.example/mcp/permission/x", "https://127.0.0.1:8765/x", "file:///etc/passwd", "127.0.0.1:8765/x",
                "http://127.0.0.1.evil.example/x", "http://user@evil.example/x", "", "javascript:alert(1)",
                # with a port too (Opus 2026-10-13: without these, a host-agnostic regex passed every case above)
                "http://evil.example:80/x", "http://127.0.0.1.evil.example:8765/x", "http://user@evil.example:8765/x",
                "http://10.0.0.5:8765/mcp/permission/t", "http://127.0.0.1@evil.example:8765/x"):
        with pytest.raises(ValueError):
            claude_cli.build_command(prompt="hi", model="m", prompt_tool_url=bad)
    for good in ("http://127.0.0.1:1/x", "http://localhost:8799/mcp/permission/t", "http://[::1]:8765/mcp/permission/t"):
        assert "--mcp-config" in claude_cli.build_command(prompt="hi", model="m", prompt_tool_url=good)


def test_build_env_sets_the_mcp_tool_timeout_only_when_the_prompt_tool_is_used(monkeypatch):
    assert claude_cli.build_env(thinking_budget=None, max_tokens=None) == {}
    monkeypatch.setattr(B, "APPROVAL_MAX_SECONDS", 100.0)
    env = claude_cli.build_env(thinking_budget=None, max_tokens=None, permission_prompt=True)
    assert env == {"MCP_TOOL_TIMEOUT": "130000"}                           # cap + 30 s, in ms
    env = claude_cli.build_env(thinking_budget=0, max_tokens=500, permission_prompt=True)
    assert env["MAX_THINKING_TOKENS"] == "0" and env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "500" and env["MCP_TOOL_TIMEOUT"] == "130000"


# ------------------------------------------------------------------ parser

def test_parser_no_longer_guesses_approvals():
    assert parse_line("Allow Bash? [y/n]") == [{"type": "notice", "text": "Allow Bash? [y/n]"}]
    for sub in ("permission_denied", "tool_permission", "approval_request", "tool_use_confirm"):
        evs = parse_line(json.dumps({"type": "system", "subtype": sub, "tool": "Bash", "input": {"command": "ls"}}))
        assert all(e["type"] != "approval_needed" for e in evs), (sub, evs)


def test_parser_init_echo_is_additive():
    plain = parse_line(json.dumps({"type": "system", "subtype": "init", "session_id": "abc"}))
    assert plain == [{"type": "session", "session_id": "abc"}]                       # unchanged when the init has no mode
    assert parse_line(json.dumps({"type": "system", "subtype": "init"})) == []
    withmode = parse_line(json.dumps({"type": "system", "subtype": "init", "session_id": "abc", "permissionMode": "acceptEdits"}))
    assert {"type": "session", "session_id": "abc"} in withmode
    assert {"type": "permission_mode_echo", "mode": "acceptEdits"} in withmode
    only = parse_line(json.dumps({"type": "system", "subtype": "init", "permissionMode": "plan"}))
    assert only == [{"type": "permission_mode_echo", "mode": "plan"}]
    assert parse_line(json.dumps({"type": "system", "subtype": "init", "permissionMode": 7})) == []


# ------------------------------------------------------------------ REST + config + startup

def test_config_offers_four_modes_each_mapped_to_a_real_cli_mode():
    j = TestClient(app).get("/api/config").json()
    opts = j["permission_mode_options"]
    assert [o["value"] for o in opts] == ["manual", "acceptEdits", "plan", "bypassPermissions"]
    assert {o["value"]: o["expected_echo"] for o in opts} == {"manual": "default", "acceptEdits": "acceptEdits", "plan": "plan", "bypassPermissions": "bypassPermissions"}
    for o in opts:
        assert o["value"] in PERMISSION_MODES and o["label"].strip() and o["description"].strip()
    assert j["permission_modes"] == list(PERMISSION_MODES)                      # the old key is untouched


def test_rest_rules_list_revoke_and_log(temp_db):
    c = TestClient(app)
    assert c.get("/api/permissions/rules").json() == []
    r = P.add_rule("Bash", {"command": "echo 'a  b' > \"x y.txt\""}, r"C:\work\proj")
    P.add_rule("Bash", {"command": "ls"}, r"C:\work\proj")
    rows = c.get("/api/permissions/rules").json()
    assert len(rows) == 2
    mine = [x for x in rows if x["id"] == r.id][0]
    assert mine["pattern"] == "echo 'a  b' > \"x y.txt\"" and mine["cwd"] == r"C:\work\proj" and mine["tool_name"] == "Bash" and mine["created_at"]
    assert "revoked_at" not in mine or mine["revoked_at"] is None
    assert c.delete(f"/api/permissions/rules/{r.id}").status_code == 200
    assert c.delete(f"/api/permissions/rules/{r.id}").status_code == 404
    assert c.delete("/api/permissions/rules/nope").status_code == 404
    assert [x["pattern"] for x in c.get("/api/permissions/rules").json()] == ["ls"]
    assert P.match_rule("Bash", {"command": "echo 'a  b' > \"x y.txt\""}, r"C:\work\proj") is None
    for i in range(7):
        P.log_open(conversation_id="c", request_id=f"r{i}", tool_use_id=None, tool_name="Bash", tool_input={"command": "ls"}, cwd="x")
    assert len(c.get("/api/permissions/log").json()) == 7
    lim = c.get("/api/permissions/log?limit=3").json()
    assert len(lim) == 3 and lim[0]["request_id"] == "r6" and lim[0]["input"] == {"command": "ls"}
    assert c.get("/api/permissions/log?limit=0").status_code in (200, 422)
    assert c.get("/api/permissions/log?limit=100000").status_code in (200, 422)
    assert len(c.get("/api/permissions/log?limit=100000").json()) <= 500


def test_startup_marks_unanswered_requests_abandoned(temp_db):
    lid = P.log_open(conversation_id="c", request_id="left-open", tool_use_id="t", tool_name="Bash", tool_input={"command": "ls"}, cwd="x")
    with TestClient(app):                                                      # entering the context runs the lifespan startup
        pass
    row = [r for r in P.list_log(10) if r["id"] == lid][0]
    assert (row["status"], row["source"]) == ("deny", "abandoned") and row["reason"]


# ------------------------------------------------------------------ end to end over a real socket with a fake CLI

def test_e2e_argv_env_and_url(env):
    cid = _conv(env)
    frames = _turn(env, cid, "approve")
    rec = _rec(env)
    assert rec["tool"] == "mcp__gca-permission__approve" and rec["argv"][-1] == "go"
    url = rec["cfg"]["mcpServers"]["gca-permission"]["url"]
    assert url.startswith(f"http://127.0.0.1:{env.srv.port}/mcp/permission/") and cid not in url
    assert len(url.rsplit("/", 1)[1]) >= 20
    assert rec["mcp_tool_timeout"] == str(int((B.APPROVAL_MAX_SECONDS + 30) * 1000))
    assert rec["argv"][rec["argv"].index("--permission-mode") + 1] == "manual"
    assert _same(rec["cwd"], str(env.proj)) and "done" in _types(frames)


def test_e2e_allow_once_runs_exactly_that_command(env):
    cid = _conv(env)
    frames = _turn(env, cid, "approve")
    need = [f for f in frames if f["type"] == "approval_needed"]
    assert len(need) == 1 and need[0]["tool"] == "Bash" and need[0]["input"]["command"] == "echo hi > marker.txt"
    assert need[0]["can_always"] is True and need[0]["request_id"] and need[0]["remaining"] > 0
    assert _marker(env).exists()
    assert [f for f in frames if f["type"] == "approval_resolved"][0]["decision"] == "allow"
    assert _types(frames).index("approval_needed") < _types(frames).index("approval_resolved") < _types(frames).index("done")
    tr = [f for f in frames if f["type"] == "tool_result"]
    assert tr and tr[0]["is_error"] is False
    row = P.list_log(5)[0]
    assert (row["status"], row["source"], row["conversation_id"]) == ("allow", "user_once", cid)
    assert row["input"]["command"] == "echo hi > marker.txt" and _same(row["cwd"], str(env.proj))
    assert P.list_rules() == []


def test_e2e_a_second_different_command_prompts_again(env, monkeypatch):
    cid = _conv(env)
    _turn(env, cid, "approve")
    _marker(env).unlink()
    monkeypatch.setenv("FAKE_AP_CMD", "echo other > marker.txt")
    frames = _turn(env, cid, "approve")
    assert _types(frames).count("approval_needed") == 1 and _marker(env).exists()
    assert len([r for r in P.list_log(10) if r["source"] == "user_once"]) == 2


def test_e2e_deny_runs_nothing_and_records_the_denial(env):
    cid = _conv(env)
    frames = _turn(env, cid, "deny")
    assert not _marker(env).exists()
    tr = [f for f in frames if f["type"] == "tool_result"]
    assert tr and tr[0]["is_error"] is True and "done" in _types(frames)
    row = P.list_log(5)[0]
    assert (row["status"], row["source"]) == ("deny", "user_deny")
    assert [f for f in frames if f["type"] == "approval_resolved"][0]["decision"] == "deny"
    assert P.list_rules() == [] and _rec(env)["decision"]["behavior"] == "deny"


def test_e2e_allow_always_then_exact_pattern_only(env, monkeypatch):
    cid = _conv(env)
    f1 = _turn(env, cid, "approve_always")
    assert _marker(env).exists()
    rules = httpx.get(env.srv.http + "/api/permissions/rules").json()
    assert len(rules) == 1 and rules[0]["pattern"] == "echo hi > marker.txt" and _same(rules[0]["cwd"], str(env.proj))
    assert (P.list_log(5)[0]["source"], P.list_log(5)[0]["rule_id"]) == ("user_always", rules[0]["id"])

    _marker(env).unlink()                                                      # same command again: no prompt, runs, logged
    f2 = _turn(env, cid, None)
    assert "approval_needed" not in _types(f2) and _marker(env).exists()
    auto = [f for f in f2 if f["type"] == "permission_auto_allowed"]
    assert len(auto) == 1 and auto[0]["rule_id"] == rules[0]["id"] and auto[0]["pattern"] == "echo hi > marker.txt"
    row = P.list_log(5)[0]
    assert (row["status"], row["source"], row["rule_id"]) == ("allow", "rule", rules[0]["id"])

    _marker(env).unlink()                                                      # a different command: must prompt, must not run
    monkeypatch.setenv("FAKE_AP_CMD", "echo hi > marker.txt && echo more > other.txt")
    f3 = _turn(env, cid, "deny")
    assert _types(f3).count("approval_needed") == 1 and not _marker(env).exists()

    monkeypatch.setenv("FAKE_AP_CMD", "echo hi > marker.txt")                  # revoke: back to asking
    assert httpx.delete(f"{env.srv.http}/api/permissions/rules/{rules[0]['id']}").status_code == 200
    f4 = _turn(env, cid, "deny")
    assert _types(f4).count("approval_needed") == 1 and not _marker(env).exists()


def test_e2e_unanswered_prompt_times_out_to_deny_and_says_why(env, monkeypatch):
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 0.8)
    cid = _conv(env)
    t0 = time.time()
    frames = _turn(env, cid, None)
    assert time.time() - t0 < 15 and "done" in _types(frames)
    assert not _marker(env).exists()
    assert [f for f in frames if f["type"] == "approval_resolved"][0]["decision"] == "deny"
    row = P.list_log(5)[0]
    assert (row["status"], row["source"]) == ("deny", "timeout") and row["reason"]


def test_e2e_extend_keeps_the_prompt_open(env, monkeypatch):
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(B, "EXTEND_SECONDS", 3.0)
    cid = _conv(env)
    frames = []
    ws = connect(f"ws://127.0.0.1:{env.srv.port}/ws/chat/{cid}")
    try:
        ws.send(json.dumps({"type": "send", "message": "go"}))
        while True:
            f = json.loads(ws.recv(timeout=15)); frames.append(f)
            if f["type"] == "approval_needed":
                ws.send(json.dumps({"type": "approval_extend", "request_id": f["request_id"]}))
                time.sleep(1.6)                                              # past the original 1.0 s window
                ws.send(json.dumps({"type": "approve", "request_id": f["request_id"]}))
            if f["type"] == "done":
                break
    finally:
        ws.close()
    assert any(f["type"] == "approval_extended" and f["remaining"] > 0 for f in frames)
    assert _marker(env).exists()


def test_e2e_stop_while_pending_denies_and_ends(env):
    cid = _conv(env)
    frames = _turn(env, cid, "stop")
    assert "done" in _types(frames) and not _marker(env).exists()
    assert _wait(lambda: P.list_log(5)[0]["status"] != "pending")
    row = P.list_log(5)[0]
    assert row["status"] == "deny" and row["source"] in ("stopped", "timeout", "abandoned")


def test_e2e_closing_the_window_while_pending_denies_and_ends(env):
    cid = _conv(env)
    _turn(env, cid, "disconnect")
    assert _wait(lambda: not B.broker.has_pending(cid))
    assert _wait(lambda: P.list_log(5)[0]["status"] != "pending")
    assert P.list_log(5)[0]["status"] == "deny" and not _marker(env).exists()


def test_e2e_a_second_window_closing_does_not_end_the_owners_pending_prompt(env):
    """Replaces test_ws_owner_stop's approval case (Opus 2026-10-13): only the window that started the turn may end it."""
    cid = _conv(env)

    def other_window_comes_and_goes(_f):
        w2 = connect(f"ws://127.0.0.1:{env.srv.port}/ws/chat/{cid}")
        time.sleep(0.3)
        w2.close()
        time.sleep(0.7)
        assert B.broker.has_pending(cid), "a non-owning window's disconnect ended the owner's pending prompt"

    frames = _turn(env, cid, "approve", after_needed=other_window_comes_and_goes)
    assert "done" in _types(frames) and _marker(env).exists()
    assert _wait(lambda: P.list_log(5)[0]["status"] == "allow")


def test_e2e_token_dies_with_the_turn(env):
    cid = _conv(env)
    _turn(env, cid, "approve")
    url = _rec(env)["url"]
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "approve", "arguments": {"tool_name": "Bash", "input": {"command": "rm -rf /"}}}})
    assert r.status_code == 200
    assert json.loads(r.json()["result"]["content"][0]["text"])["behavior"] == "deny"


def test_e2e_mode_echo_frame(env, monkeypatch):
    cid = _conv(env, "manual")
    f = [x for x in _turn(env, cid, "approve") if x["type"] == "permission_mode"]
    assert f == [{"type": "permission_mode", "requested": "manual", "actual": "default", "ok": True}]
    cid2 = _conv(env, "acceptEdits")
    monkeypatch.setenv("FAKE_AP_ECHO", "default")                                # the CLI quietly ran a different mode
    f2 = [x for x in _turn(env, cid2, "approve") if x["type"] == "permission_mode"]
    assert f2 == [{"type": "permission_mode", "requested": "acceptEdits", "actual": "default", "ok": False}]
    monkeypatch.delenv("FAKE_AP_ECHO")
    # Ruling Q2 (Houston 2026-10-13; amended here by Opus 08:50, before the oracle was frozen): a chat with no stored mode
    # runs as `manual` and the app always passes --permission-mode, so the user's settings.json defaultMode (measured:
    # bypassPermissions on this machine) can never silently apply.
    cid3 = _conv(env, None)
    f3 = [x for x in _turn(env, cid3, "approve") if x["type"] == "permission_mode"]
    assert f3 == [{"type": "permission_mode", "requested": "manual", "actual": "default", "ok": True}]
    assert _rec(env)["argv"][_rec(env)["argv"].index("--permission-mode") + 1] == "manual"


def test_e2e_legacy_y_n_prompt_is_never_an_approval(env, monkeypatch):
    """A CLI that prints '[y/n]' on stdout (the retired path) must not make the app ask, or write to the CLI's stdin."""
    cid = _conv(env)
    src = FAKE.replace('emit({"type": "system", "subtype": "init"', 'sys.stdout.write("Allow Bash? [y/n]\\n"); sys.stdout.flush()\nemit({"type": "system", "subtype": "init"', 1)
    script = Path(os.environ["FAKE_AP_OUT"]).parent / "fake_cli_ap.py"
    script.write_text(src, encoding="utf-8")
    frames = _turn(env, cid, "approve")
    ap = [f for f in frames if f["type"] == "approval_needed"]
    assert len(ap) == 1 and ap[0]["request_id"] and ap[0]["tool"] == "Bash"       # exactly one, and it is the real prompt-tool one
    assert "notice" in _types(frames)


def test_retired_names_are_gone_from_the_server_source():
    src = (Path(__file__).resolve().parents[1] / "app" / "ws" / "chat_socket.py").read_text(encoding="utf-8")
    for needle in ("b\"y\\n\"", "approval_queue", "stdin.write", "_approval_pending"):
        assert needle not in src, needle
