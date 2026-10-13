"""WP-AP J2 frozen oracle (Sonnet, 2026-10-13): the permission broker (app.services.permission_broker) and the in-app
MCP endpoint the claude CLI calls as --permission-prompt-tool (app.routers.permission_mcp, POST /mcp/permission/{token}).
Builds on J1 (app.services.permissions). Copied into backend/tests/ by oracle_run.py; uses conftest's temp_db."""
import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app.services.permission_broker as B
import app.services.permissions as P
from app.main import app

CWD = r"C:\work\proj"
CMD = "echo hi > a.txt"
INP = {"command": CMD, "description": "write a file"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def fast(monkeypatch, temp_db):
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(B, "APPROVAL_MAX_SECONDS", 2.0)
    monkeypatch.setattr(B, "EXTEND_SECONDS", 0.5)
    return B.PermissionBroker()


class Ui:
    """A fake socket: records frames; optionally answers the first approval_needed after `delay` seconds."""

    def __init__(self, broker, conv, answer=None, delay=0.05):
        self.frames, self.broker, self.conv, self.answer, self.delay = [], broker, conv, answer, delay

    async def notify(self, frame):
        self.frames.append(frame)
        if frame["type"] == "approval_needed" and self.answer:
            asyncio.get_running_loop().call_later(self.delay, self.broker.resolve, self.conv, frame["request_id"], self.answer)

    def types(self):
        return [f["type"] for f in self.frames]


def _row(request_id=None):
    rows = P.list_log(50)
    return rows[0] if request_id is None else [r for r in rows if r["request_id"] == request_id][0]


# ---------------------------------------------------------------- broker

def test_tokens_are_long_random_and_distinct(fast):
    a, b = fast.open_turn("c1", CWD, None), fast.open_turn("c1", CWD, None)
    assert isinstance(a, str) and len(a) >= 20 and a != b and a.replace("-", "").replace("_", "").isalnum()


def test_unknown_token_denies_and_logs(fast):
    d = _run(fast.decide("nope", "Bash", INP, "t1"))
    assert d["behavior"] == "deny" and isinstance(d["message"], str) and d["message"]
    row = _row()
    assert row["status"] == "deny" and row["source"] == "error" and row["reason"]


def test_no_ui_denies(fast):
    tok = fast.open_turn("c1", CWD, None)
    d = _run(fast.decide(tok, "Bash", INP, "t1"))
    assert d["behavior"] == "deny" and d["message"]
    row = _row()
    assert (row["status"], row["source"]) == ("deny", "no_ui") and row["conversation_id"] == "c1" and row["input"] == INP


def test_allow_once_runs_exactly_that_input_and_logs(fast):
    ui = Ui(fast, "c1", "allow_once")
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "Bash", INP, "toolu_9"))
    assert d == {"behavior": "allow", "updatedInput": INP}
    need = [f for f in ui.frames if f["type"] == "approval_needed"][0]
    assert need["tool"] == "Bash" and need["input"] == INP and need["tool_use_id"] == "toolu_9" and need["cwd"] == CWD
    assert CMD in need["action"] and need["can_always"] is True and need["always_pattern"] == CMD
    assert isinstance(need["request_id"], str) and need["request_id"]
    assert 0 < need["remaining"] <= need["timeout"] and need["timeout"] == B.APPROVAL_TIMEOUT_SECONDS
    res = [f for f in ui.frames if f["type"] == "approval_resolved"][0]
    assert res["request_id"] == need["request_id"] and res["decision"] == "allow" and res["source"] == "user_once"
    row = _row(need["request_id"])
    assert (row["status"], row["source"], row["tool_use_id"]) == ("allow", "user_once", "toolu_9") and row["resolved_at"]
    assert P.list_rules() == []                                    # allow ONCE leaves no rule behind


def test_second_different_command_prompts_again(fast):
    ui = Ui(fast, "c1", "allow_once")
    tok = fast.open_turn("c1", CWD, ui.notify)
    _run(fast.decide(tok, "Bash", INP, "t1"))
    _run(fast.decide(tok, "Bash", {"command": "echo two > b.txt"}, "t2"))
    assert ui.types().count("approval_needed") == 2


def test_deny_blocks_and_creates_nothing(fast):
    ui = Ui(fast, "c1", "deny")
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "Bash", INP, "t1"))
    assert d["behavior"] == "deny" and "updatedInput" not in d and d["message"]
    row = _row()
    assert (row["status"], row["source"]) == ("deny", "user_deny")
    assert P.list_rules() == []
    assert [f for f in ui.frames if f["type"] == "approval_resolved"][0]["decision"] == "deny"


def test_allow_always_creates_the_exact_rule_then_auto_allows_only_that(fast):
    ui = Ui(fast, "c1", "allow_always")
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "Bash", INP, "t1"))
    assert d == {"behavior": "allow", "updatedInput": INP}
    rules = P.list_rules()
    assert len(rules) == 1 and rules[0].pattern == CMD and rules[0].cwd == CWD and rules[0].tool_name == "Bash"
    first = _row()
    assert (first["status"], first["source"], first["rule_id"]) == ("allow", "user_always", rules[0].id)

    ui2 = Ui(fast, "c1", None)                                      # a later turn: nobody answers, rule must decide
    tok2 = fast.open_turn("c1", CWD, ui2.notify)
    d2 = _run(fast.decide(tok2, "Bash", {"command": CMD, "description": "other words"}, "t2"))
    assert d2["behavior"] == "allow"
    assert "approval_needed" not in ui2.types()
    auto = [f for f in ui2.frames if f["type"] == "permission_auto_allowed"][0]
    assert auto["rule_id"] == rules[0].id and auto["tool"] == "Bash" and auto["pattern"] == CMD and auto["request_id"]
    row = _row(auto["request_id"])
    assert (row["status"], row["source"], row["rule_id"]) == ("allow", "rule", rules[0].id)

    d3 = _run(fast.decide(tok2, "Bash", {"command": "echo hi > a.txt && echo more"}, "t3"))   # different command: not allowed by the rule
    assert d3["behavior"] == "deny" and ui2.types().count("approval_needed") == 1

    tok3 = fast.open_turn("c1", r"C:\work\elsewhere", ui2.notify)                            # same command, other folder
    d4 = _run(fast.decide(tok3, "Bash", INP, "t4"))
    assert d4["behavior"] == "deny" and ui2.types().count("approval_needed") == 2

    P.revoke_rule(rules[0].id)                                                               # revoked: back to asking
    d5 = _run(fast.decide(tok2, "Bash", INP, "t5"))
    assert d5["behavior"] == "deny" and ui2.types().count("approval_needed") == 3


def test_allow_always_on_a_tool_without_rules_degrades_to_allow_once(fast):
    ui = Ui(fast, "c1", "allow_always")
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "Write", {"file_path": r"C:\work\proj\x.txt", "content": "hi"}, "t1"))
    assert d["behavior"] == "allow"
    need = [f for f in ui.frames if f["type"] == "approval_needed"][0]
    assert need["can_always"] is False and need["always_pattern"] is None
    assert P.list_rules() == []
    row = _row()
    assert (row["status"], row["source"]) == ("allow", "user_once") and row["reason"]


def test_timeout_denies_and_says_why(fast):
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)
    t0 = time.monotonic()
    d = _run(fast.decide(tok, "Bash", INP, "t1"))
    took = time.monotonic() - t0
    assert d["behavior"] == "deny" and d["message"] and 0.3 <= took < 1.5
    row = _row()
    assert (row["status"], row["source"]) == ("deny", "timeout") and row["reason"]
    assert [f for f in ui.frames if f["type"] == "approval_resolved"][0]["decision"] == "deny"
    assert not fast.has_pending("c1")


def test_extend_adds_time_but_never_past_the_cap(fast, monkeypatch):
    monkeypatch.setattr(B, "APPROVAL_MAX_SECONDS", 0.9)
    monkeypatch.setattr(B, "EXTEND_SECONDS", 10.0)
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)

    async def go():
        task = asyncio.ensure_future(fast.decide(tok, "Bash", INP, "t1"))
        await asyncio.sleep(0.2)
        left = fast.extend("c1")
        assert left is not None and left > 0.3                                 # extended past the original 0.4 s
        assert fast.extend("c1", "no-such-request") is None
        t0 = time.monotonic()
        d = await task
        return d, time.monotonic() - t0

    d, since = _run(go())
    assert d["behavior"] == "deny" and since < 1.3                                # clamped to the 0.9 s cap, not 10 s
    ext = [f for f in ui.frames if f["type"] == "approval_extended"]
    assert ext and ext[0]["remaining"] > 0 and ext[0]["request_id"]
    assert _row()["source"] == "timeout"


def test_extend_with_nothing_pending_is_none(fast):
    assert fast.extend("c1") is None


def test_extend_then_answer_in_time_allows(fast, monkeypatch):
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(B, "EXTEND_SECONDS", 1.0)
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)

    async def go():
        task = asyncio.ensure_future(fast.decide(tok, "Bash", INP, "t1"))
        await asyncio.sleep(0.15)
        fast.extend("c1")
        await asyncio.sleep(0.4)                                               # past the original deadline
        rid = [f for f in ui.frames if f["type"] == "approval_needed"][0]["request_id"]
        assert fast.resolve("c1", rid, "allow_once") is True
        return await task

    assert _run(go())["behavior"] == "allow"


def test_resolve_guards(fast):
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)

    async def go():
        task = asyncio.ensure_future(fast.decide(tok, "Bash", INP, "t1"))
        await asyncio.sleep(0.1)
        rid = [f for f in ui.frames if f["type"] == "approval_needed"][0]["request_id"]
        assert fast.has_pending("c1") and not fast.has_pending("c2")
        assert fast.resolve("c2", rid, "allow_once") is False                  # wrong conversation
        assert fast.resolve("c1", "nope", "allow_once") is False
        assert fast.resolve("c1", rid, "sure why not") is False                # junk decision does not resolve it
        assert fast.has_pending("c1")
        assert fast.resolve("c1", rid, "deny") is True
        assert fast.resolve("c1", rid, "allow_once") is False                  # already decided
        return await task

    assert _run(go())["behavior"] == "deny"


def test_resolve_is_thread_safe(fast, monkeypatch):
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 5.0)          # a missed wake-up must show as a hang, not hide behind the timeout
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)

    async def go():
        task = asyncio.ensure_future(fast.decide(tok, "Bash", INP, "t1"))
        await asyncio.sleep(0.1)
        rid = [f for f in ui.frames if f["type"] == "approval_needed"][0]["request_id"]
        out = []
        t = threading.Thread(target=lambda: out.append(fast.resolve("c1", rid, "allow_once")))
        t0 = time.monotonic()
        t.start()
        d = await asyncio.wait_for(task, 2)
        took = time.monotonic() - t0
        t.join()
        return d, out, took

    d, out, took = _run(go())
    assert d["behavior"] == "allow" and out == [True]
    assert took < 0.8, took                                   # a wake-up from another thread must be immediate, not found at a later timer


def test_two_requests_in_one_turn_resolve_independently(fast):
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)

    async def go():
        t1 = asyncio.ensure_future(fast.decide(tok, "Bash", {"command": "one"}, "t1"))
        t2 = asyncio.ensure_future(fast.decide(tok, "Bash", {"command": "two"}, "t2"))
        await asyncio.sleep(0.1)
        needs = {f["input"]["command"]: f["request_id"] for f in ui.frames if f["type"] == "approval_needed"}
        assert set(needs) == {"one", "two"} and needs["one"] != needs["two"]
        fast.resolve("c1", needs["two"], "allow_once")
        fast.resolve("c1", needs["one"], "deny")
        return (await t1)["behavior"], (await t2)["behavior"]

    assert _run(go()) == ("deny", "allow")


def test_every_failure_denies_instead_of_raising(fast, monkeypatch):
    async def boom(frame):
        raise RuntimeError("socket gone")

    tok = fast.open_turn("c1", CWD, boom)
    d = _run(fast.decide(tok, "Bash", INP, "t1"))
    assert d["behavior"] == "deny" and _row()["source"] in ("error", "no_ui")

    async def hang(frame):
        await asyncio.sleep(30)

    tok = fast.open_turn("c1", CWD, hang)
    t0 = time.monotonic()
    d = _run(fast.decide(tok, "Bash", {"command": "slow"}, "t2"))
    assert d["behavior"] == "deny" and time.monotonic() - t0 < 2.5

    tok = fast.open_turn("c1", CWD, Ui(fast, "c1", "allow_once").notify)
    with monkeypatch.context() as m:
        m.setattr(P, "match_rule", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked")))
        d = _run(fast.decide(tok, "Bash", {"command": "after db failure"}, "t3"))
    assert d["behavior"] == "deny"

    ui = Ui(fast, "c1", "allow_once")
    tok = fast.open_turn("c1", CWD, ui.notify)
    d = _run(fast.decide(tok, "Bash", "this is not a dict", "t4"))
    assert d["behavior"] == "deny"
    d = _run(fast.decide(tok, "Bash", None, None))
    assert d["behavior"] == "deny"
    d = _run(fast.decide(tok, None, {"command": "x"}, "t5"))
    assert d["behavior"] == "deny"


def test_log_failure_denies(fast, monkeypatch):
    tok = fast.open_turn("c1", CWD, Ui(fast, "c1", "allow_once").notify)
    monkeypatch.setattr(P, "log_open", lambda **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    d = _run(fast.decide(tok, "Bash", INP, "t1"))
    assert d["behavior"] == "deny"          # no log, no action: an unrecorded approval is not allowed


def test_close_turn_denies_pending_and_forgets_the_token(fast):
    ui = Ui(fast, "c1", None)
    tok = fast.open_turn("c1", CWD, ui.notify)

    async def go():
        task = asyncio.ensure_future(fast.decide(tok, "Bash", INP, "t1"))
        await asyncio.sleep(0.1)
        fast.close_turn(tok)
        return await asyncio.wait_for(task, 2)

    d = _run(go())
    assert d["behavior"] == "deny"
    assert _row()["source"] == "stopped"
    assert _run(fast.decide(tok, "Bash", INP, "t2"))["behavior"] == "deny"
    assert not fast.has_pending("c1")


# ---------------------------------------------------------------- MCP endpoint

client = TestClient(app)


def rpc(token, method, params=None, id_=1, raw=None, headers=None):
    body = raw if raw is not None else {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if raw is None and id_ is not None:
        body["id"] = id_
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    h.update(headers or {})
    if isinstance(body, (dict, list)):
        return client.post(f"/mcp/permission/{token}", json=body, headers=h)
    return client.post(f"/mcp/permission/{token}", content=body, headers=h)


@pytest.fixture()
def turn(monkeypatch, temp_db):
    monkeypatch.setattr(B, "APPROVAL_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(B, "APPROVAL_MAX_SECONDS", 2.0)
    frames = []

    async def notify(frame):
        frames.append(frame)
        if frame["type"] == "approval_needed":
            asyncio.get_running_loop().call_later(0.05, B.broker.resolve, "c-mcp", frame["request_id"], "allow_once")

    tok = B.broker.open_turn("c-mcp", CWD, notify)
    yield tok, frames
    B.broker.close_turn(tok)


def _decision(resp):
    j = resp.json()
    assert resp.status_code == 200 and j["jsonrpc"] == "2.0" and "error" not in j, j
    content = j["result"]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"
    return json.loads(content[0]["text"])


def test_mcp_handshake(turn):
    tok, _ = turn
    r = rpc(tok, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "claude-code", "version": "2.1.291"}})
    j = r.json()
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert j["jsonrpc"] == "2.0" and j["id"] == 1
    assert j["result"]["protocolVersion"] == "2025-11-25" and j["result"]["capabilities"] == {"tools": {}}
    assert j["result"]["serverInfo"]["name"] and j["result"]["serverInfo"]["version"]
    n = rpc(tok, "notifications/initialized", id_=None)
    assert n.status_code == 202 and n.content == b""
    assert rpc(tok, "ping").json()["result"] == {}


def test_mcp_lists_exactly_one_tool_called_approve(turn):
    tok, _ = turn
    tools = rpc(tok, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["approve"]
    sch = tools[0]["inputSchema"]
    assert sch["type"] == "object" and {"tool_name", "input", "tool_use_id"} <= set(sch["properties"])
    assert {"tool_name", "input"} <= set(sch["required"]) and tools[0]["description"]


def test_mcp_unknown_methods_are_jsonrpc_errors_not_http_errors(turn):
    tok, _ = turn
    r = rpc(tok, "server/discover")
    assert r.status_code == 200 and r.json()["error"]["code"] == -32601 and r.json()["id"] == 1


def test_mcp_get_is_not_supported(turn):
    tok, _ = turn
    assert client.get(f"/mcp/permission/{tok}", headers={"Accept": "text/event-stream"}).status_code == 405
    assert client.delete(f"/mcp/permission/{tok}").status_code in (200, 204, 405)


def test_mcp_call_round_trip_allow_and_log(turn):
    tok, frames = turn
    r = rpc(tok, "tools/call", {"name": "approve", "arguments": {"tool_name": "Bash", "input": INP, "tool_use_id": "toolu_mcp"}})
    assert _decision(r) == {"behavior": "allow", "updatedInput": INP}
    assert "approval_needed" in [f["type"] for f in frames]
    row = P.list_log(5)[0]
    assert (row["status"], row["source"], row["tool_use_id"], row["conversation_id"]) == ("allow", "user_once", "toolu_mcp", "c-mcp")


def test_mcp_call_with_unknown_token_is_a_deny_decision(temp_db):
    d = _decision(rpc("not-a-token", "tools/call", {"name": "approve", "arguments": {"tool_name": "Bash", "input": INP}}))
    assert d["behavior"] == "deny" and d["message"]
    assert P.list_log(5)[0]["source"] == "error"


def test_mcp_malformed_calls_never_allow(turn):
    tok, _ = turn
    bad_args = [{}, {"tool_name": "Bash"}, {"tool_name": "Bash", "input": "ls"}, {"tool_name": "Bash", "input": ["ls"]},
                {"tool_name": 5, "input": {}}, {"input": {"command": "ls"}}, None, "text"]
    for args in bad_args:
        r = rpc(tok, "tools/call", {"name": "approve", "arguments": args})
        assert r.status_code == 200, args
        j = r.json()
        if "error" in j:
            continue
        assert _decision(r)["behavior"] == "deny", args
    r = rpc(tok, "tools/call", {"name": "something_else", "arguments": {"tool_name": "Bash", "input": INP}})
    j = r.json()
    assert r.status_code == 200 and ("error" in j or j["result"].get("isError") is True)
    assert "allow" not in json.dumps(j)


def test_mcp_garbage_bodies_do_not_crash_or_allow(turn):
    tok, _ = turn
    for raw in (b"not json", b"", b"[1,2,3]", b'{"jsonrpc":"2.0"}', b'{"method":5}', b"null"):
        r = rpc(tok, None, raw=raw)
        assert r.status_code in (200, 202, 400), (raw, r.status_code)
        assert b"behavior" not in r.content, raw                  # no decision of any kind came out of garbage
    batch = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "approve", "arguments": {"tool_name": "Bash", "input": INP}}}]
    r = rpc(tok, None, raw=batch)
    assert r.status_code in (200, 400) and b"behavior" not in r.content      # batches are refused, not half-processed


def test_mcp_hang_and_crash_deny_with_http_200(turn, monkeypatch):
    tok, _ = turn
    quiet = B.broker.open_turn("c-quiet", CWD, lambda frame: asyncio.sleep(0))      # nobody ever answers
    t0 = time.monotonic()
    d = _decision(rpc(quiet, "tools/call", {"name": "approve", "arguments": {"tool_name": "Bash", "input": INP}}))
    assert d["behavior"] == "deny" and 0.4 <= time.monotonic() - t0 < 3
    assert P.list_log(3)[0]["source"] == "timeout"
    B.broker.close_turn(quiet)

    async def explode(*a, **k):
        raise RuntimeError("broker bug")

    monkeypatch.setattr(B.broker, "decide", explode)
    d = _decision(rpc(tok, "tools/call", {"name": "approve", "arguments": {"tool_name": "Bash", "input": INP}}))
    assert d["behavior"] == "deny" and d["message"]


def test_mcp_route_is_registered_once_and_only_posts_decide(turn):
    tok, _ = turn
    paths = [getattr(r, "path", "") for r in app.routes]
    assert paths.count("/mcp/permission/{token}") == 1
