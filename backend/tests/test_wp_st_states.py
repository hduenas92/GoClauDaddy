r"""WP-ST J1 frozen oracle (Opus, 2026-10-13): connection states from CLI stream events, backend half.

Evidence: real CLI 2.1.291 stream-json captured by probes P0.7/P0.8 (fixtures in plans\swarm\oraclesST\fx, built by
wST\make_fx.py, replayed by st_fake_cli.py through a claude.cmd shim first on PATH). Rulings: finalize item 3 (mirror the
CLI, change state at the FIRST api_retry), D2 (startup auth check is a warning), D3 (key_invalid at the first 401 retry),
D4 (key missing = result text "Not logged in" with no api_error_status; URL missing = same text WITH a status).

Contract checked here:
  conn_state frame  {"type": "conn_state", "state": S, ...}; retry frames also carry "attempt" and "max_retries".
  S in: retrying, key_invalid, connected, key_missing, url_missing, gateway_unreachable.
  error frame       {"type": "error", "code": C, "error": <plain copy>} for C in key_missing, url_missing, key_invalid,
                    gateway_unreachable; exactly ONE error frame per failed turn; never the CLI's raw error text.
Run from backend/ (conftest's temp_db applies).

Shipped 2026-10-13 (WP-ST close-out) from plans/swarm/oraclesST, frozen copy; only the paths changed (fixtures now live
in tests/fixtures/wp_st, byte-identical to the originals). Original SHA256 per plans/swarm/oraclesST/SHA256.txt: aba9ee3425e55cadaccd4e47f1340115c42948b7a6d90db3d4f9136fded962be swarmST_j1_states.py
"""
import asyncio
import json
import logging
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import claude_cli, stream_parser
from app import startup_check
from app.config import REQUIRED_AUTH_ENV_VARS

ORA = pathlib.Path(__file__).resolve().parent / "fixtures" / "wp_st"
FX = ORA / "fx"
CODES = ("key_missing", "url_missing", "key_invalid", "gateway_unreachable")
RAW = ("Not logged in", "/login", "LiteLLM", "Virtual Key", "inva****", "ECONNREFUSED", "API Error", "authentication_failed")
SECRET = "s" + "k-ST-ORACLE-SECRET-VALUE-0123456789"  # built from pieces: never a real credential
client = TestClient(app)


def _lines(name):
    return [l for l in (FX / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def _parse_all(name):
    out = []
    for l in _lines(name):
        out.extend(stream_parser.parse_line(l))
    return out


def _of(evs, t):
    return [e for e in evs if e.get("type") == t]


@pytest.fixture
def shim(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude.cmd").write_text(f'@"{sys.executable}" "{ORA / "st_fake_cli.py"}" %*\r\n', encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("ST_SLEEP", "0.02")
    monkeypatch.delenv("ST_CTRL", raising=False)
    return lambda scenario: monkeypatch.setenv("ST_SCENARIO", scenario)


# ---- parser: api_retry -----------------------------------------------------------------------------------------

def test_retry_401_is_key_invalid_from_the_first_attempt():
    first = [l for l in _lines("key_invalid") if '"api_retry"' in l][0]
    evs = stream_parser.parse_line(first)
    assert len(evs) == 1, evs
    e = evs[0]
    assert (e["type"], e["state"], e["attempt"], e["max_retries"]) == ("conn_state", "key_invalid", 1, 10), e


def test_retry_without_status_is_retrying_with_attempt():
    retries = [l for l in _lines("deadproxy") if '"api_retry"' in l]
    states = [stream_parser.parse_line(l) for l in retries]
    assert all(len(s) == 1 and s[0]["type"] == "conn_state" and s[0]["state"] == "retrying" for s in states), states
    assert [s[0]["attempt"] for s in states] == list(range(1, 11))
    assert all(s[0]["max_retries"] == 10 for s in states)


def test_retry_429_is_retrying_not_key_invalid():
    line = [l for l in _lines("rate429") if '"api_retry"' in l][0]
    (e,) = stream_parser.parse_line(line)
    assert (e["type"], e["state"], e["attempt"]) == ("conn_state", "retrying", 1), e


# ---- parser: final result ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("scenario,code", [
    ("key_missing", "key_missing"), ("url_missing", "url_missing"),
    ("key_invalid", "key_invalid"), ("deadproxy", "gateway_unreachable"),
])
def test_failed_turn_maps_to_one_plain_error(scenario, code):
    evs = _parse_all(scenario)
    errs = _of(evs, "error")
    assert len(errs) == 1, errs
    assert errs[0].get("code") == code, errs
    text = errs[0].get("error") or ""
    assert text.strip(), "error frame needs plain copy"
    for raw in RAW:
        assert raw not in text, f"raw CLI text {raw!r} leaked into {text!r}"
    assert not _of(evs, "text"), "the CLI's synthetic error message must not become assistant text"
    states = _of(evs, "conn_state")
    assert states and states[-1]["state"] == code, states


def test_copy_keywords():
    t = {c: _of(_parse_all(s), "error")[0]["error"].lower() for s, c in
         [("key_missing", "key_missing"), ("url_missing", "url_missing"), ("key_invalid", "key_invalid"),
          ("deadproxy", "gateway_unreachable")]}
    assert "key" in t["key_missing"] and "key" in t["key_invalid"], t
    assert "address" in t["url_missing"], t
    assert "vpn" in t["gateway_unreachable"], t


def test_key_invalid_retry_states_all_key_invalid_in_order():
    states = _of(_parse_all("key_invalid"), "conn_state")
    retry = [s for s in states if "attempt" in s]
    assert [s["attempt"] for s in retry] == list(range(1, 11)), retry
    assert all(s["state"] == "key_invalid" for s in retry), retry


def test_success_keeps_result_and_reports_connected():
    evs = _parse_all("ok")
    assert any(e.get("text") for e in _of(evs, "text")), "a real reply must still stream as text"
    res = _of(evs, "result")
    assert len(res) == 1 and res[0].get("usage"), res
    assert [s["state"] for s in _of(evs, "conn_state")] == ["connected"], evs
    assert not _of(evs, "error")


def test_unclassified_error_stays_generic():
    evs = _parse_all("generic_error")
    errs = _of(evs, "error")
    assert len(errs) == 1 and errs[0].get("code") not in CODES, errs
    assert not [s for s in _of(evs, "conn_state") if s["state"] in CODES], evs
    assert not _of(evs, "text"), "synthetic error text must not become assistant text"


# ---- claude_cli.run: one terminal error, never a second exit-code error -----------------------------------------

def _run_events(scenario, shim):
    shim(scenario)

    async def go():
        return [e async for e in claude_cli.run(prompt="hi", model="claude-sonnet-5-5", cwd=str(ORA))]
    return asyncio.run(go())


@pytest.mark.parametrize("scenario", ["key_missing", "url_missing", "key_invalid", "deadproxy", "generic_error"])
def test_run_yields_exactly_one_error_then_done(scenario, shim):
    evs = _run_events(scenario, shim)
    assert len(_of(evs, "error")) == 1, [e for e in evs if e["type"] in ("error", "done")]
    assert evs[-1] == {"type": "done"}, evs[-3:]


def test_run_success_has_no_error(shim):
    evs = _run_events("ok", shim)
    assert not _of(evs, "error") and evs[-1] == {"type": "done"}, evs


# ---- end to end over the WebSocket ----------------------------------------------------------------------------

def _turn(temp_db, scenario, shim):
    shim(scenario)
    conv = client.post("/api/conversations", json={}).json()["id"]
    frames = []
    with client.websocket_connect(f"/ws/chat/{conv}") as ws:
        ws.send_json({"type": "send", "message": "Reply with exactly: OK"})
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f.get("type") == "done":
                break
    return conv, frames


def test_ws_deadproxy_retrying_counts_up_then_gateway_unreachable(temp_db, shim):
    _, frames = _turn(temp_db, "deadproxy", shim)
    cs = _of(frames, "conn_state")
    retry = [f for f in cs if f["state"] == "retrying"]
    assert [f["attempt"] for f in retry] == list(range(1, 11)), cs
    first_retry = frames.index(retry[0])
    err_idx = [i for i, f in enumerate(frames) if f["type"] == "error"]
    assert len(err_idx) == 1 and frames[err_idx[0]]["code"] == "gateway_unreachable", frames
    assert first_retry < err_idx[0]
    assert not _of(frames, "text")


def test_ws_key_invalid_labelled_at_first_401_and_not_persisted(temp_db, shim):
    conv, frames = _turn(temp_db, "key_invalid", shim)
    cs = _of(frames, "conn_state")
    assert cs[0]["state"] == "key_invalid" and cs[0]["attempt"] == 1, cs
    assert [f["code"] for f in _of(frames, "error")] == ["key_invalid"], frames
    msgs = client.get(f"/api/conversations/{conv}").json()["messages"]
    blob = json.dumps(msgs)
    for raw in ("LiteLLM", "Virtual Key", "inva****"):
        assert raw not in blob, f"raw CLI error {raw!r} was stored as the reply"


@pytest.mark.parametrize("scenario,first", [("recover", "retrying"), ("recover401", "key_invalid"), ("rate429", "retrying")])
def test_ws_state_clears_with_the_flow_on_recovery(temp_db, shim, scenario, first):
    _, frames = _turn(temp_db, scenario, shim)
    cs = [f["state"] for f in _of(frames, "conn_state")]
    assert cs[0] == first and cs[-1] == "connected", cs
    assert _of(frames, "text") and not _of(frames, "error"), frames


@pytest.mark.parametrize("scenario,code", [("key_missing", "key_missing"), ("url_missing", "url_missing")])
def test_ws_setup_errors(temp_db, shim, scenario, code):
    _, frames = _turn(temp_db, scenario, shim)
    assert [f.get("code") for f in _of(frames, "error")] == [code], frames
    assert not _of(frames, "text")


# ---- D2: startup auth check is a warning, never a refusal; values never logged ---------------------------------

def test_startup_missing_auth_vars_warns_and_starts(monkeypatch, caplog):
    for n in REQUIRED_AUTH_ENV_VARS:
        monkeypatch.delenv(n, raising=False)
    caplog.set_level(logging.WARNING)
    startup_check._check_auth_env_vars()  # must not raise
    warn = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    for n in REQUIRED_AUTH_ENV_VARS:
        assert n in warn, f"warning must name {n}: {warn!r}"


def test_startup_partial_and_malformed_never_raise_or_leak(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[0], SECRET)
    for n in REQUIRED_AUTH_ENV_VARS[1:]:
        monkeypatch.delenv(n, raising=False)
    startup_check._check_auth_env_vars()
    monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[0], SECRET + " with spaces")
    for n in REQUIRED_AUTH_ENV_VARS[1:]:
        monkeypatch.setenv(n, "http://[::1")
    startup_check._check_auth_env_vars()
    assert all(SECRET not in r.getMessage() for r in caplog.records), "a credential value reached a log record"


def test_run_startup_checks_passes_without_auth_vars(monkeypatch):
    for n in REQUIRED_AUTH_ENV_VARS:
        monkeypatch.delenv(n, raising=False)
    for name in ("_check_writable", "_check_port_free", "_check_claude_on_path"):
        monkeypatch.setattr(startup_check, name, lambda *a, **k: None)
    startup_check.run_startup_checks()
