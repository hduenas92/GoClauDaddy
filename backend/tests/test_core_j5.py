r"""RESTORATION CORE J5 frozen oracle: effort control (section A of SPEC.md) + the UI wrapper.

Backend half runs under pytest from backend/ against THIS clone's app. Covers the
argv unit surface, the end-to-end websocket turn, the conversations API/DB column
and the migration path. The capture seam replaces
``claude_cli.asyncio.create_subprocess_exec`` with a fake that records argv+env,
so no subprocess and no model tokens are spent; the only real CLI call is
``claude --help``.

UI half shells out to the J5 Playwright harness
(``plans\swarm\oraclesUI\core_j5_ui.mjs``) against an isolated uvicorn on a
random port with a temp HOME (never :8765).

One pytest test per backend id (44 ids in ids_backend.txt), one parametrized
``test_j5_ui`` per UI id (64 ids in ids_ui.txt).
"""

import asyncio
import hashlib
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient

import app.services.claude_cli as claude_cli
from app.services import permission_broker
from app.db import migrations as migrations_mod
from app.db.connection import get_connection
from app.main import app

# ---------------------------------------------------------------------------
# Frozen constants (SPEC.md)
# ---------------------------------------------------------------------------

MJS = r"C:\Users\hduenas\LLMs\Claude\Projects\plans\swarm\oraclesUI\core_j5_ui.mjs"

# The five levels `claude --help` lists (CLI 2.1.296) and the backend accepts.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
PERMISSION_MODES = ("manual", "acceptEdits", "auto", "bypassPermissions", "dontAsk", "plan")
SHIPPED_MIGRATIONS_SHA256 = "37e290d0a628ff0df1c9baacdfde5705e2d0556e77c635c18643274246ba370a"

# Exactly the 64 UI ids, in ids_ui.txt order.
UI_CHECKS = [
    "instrument_selftest",
    "me_exists_one_in_row",
    "me_name_has_model_label",
    "me_menu_opens_with_models",
    "me_models_one_checked",
    "me_effort_simple_options",
    "me_effort_advanced_options",
    "me_effort_default_new_conv",
    "me_model_pick_patches_api",
    "me_effort_pick_patches_api",
    "me_name_has_effort_label",
    "me_model_survives_reload",
    "me_effort_survives_reload",
    "me_effort_per_conversation",
    "me_header_model_follows",
    "me_keyboard",
    "me_dismiss_outside_click",
    "me_menu_single_open",
    "chip_exists_in_row",
    "chip_name_has_label",
    "chip_default_is_manual",
    "chip_simple_two_choices",
    "chip_simple_no_bypass_anywhere",
    "chip_advanced_six_values",
    "chip_advanced_labels_human",
    "chip_pick_patches_api",
    "chip_survives_reload",
    "chip_per_conversation",
    "chip_simple_coerces_bypass",
    "chip_simple_coerces_other",
    "chip_advanced_keeps_stored_bypass",
    "chip_corrupt_ui_mode_is_simple",
    "chip_keyboard",
    "drawer_no_model_select",
    "drawer_no_permission_select",
    "drawer_no_thinking",
    "drawer_keeps_max_tokens",
    "drawer_keeps_system_prompt",
    "drawer_keeps_theme_select",
    "attach_accessible_name",
    "attach_name_unique",
    "no_prompt_glyph_calm",
    "no_prompt_glyph_cyberpunk",
    "send_still_works",
    "layout_ok_1440",
    "layout_ok_1280",
    "layout_ok_1024",
    "controls_contrast_calm_dark",
    "controls_contrast_calm_light",
    "controls_contrast_dense_dark",
    "controls_contrast_dense_light",
    "controls_contrast_warm_dark",
    "controls_contrast_warm_light",
    "controls_contrast_bold_dark",
    "controls_contrast_bold_light",
    "menu_in_viewport_short",
    "no_page_errors",
    "chip_header_words_manual",
    "chip_header_words_acceptEdits",
    "chip_header_words_plan",
    "chip_header_words_bypassPermissions",
    "chip_header_words_auto",
    "chip_header_words_dontAsk",
    "chip_header_words_after_pick",
    "drawer_no_experimental_approval",
    "effective_echo_outside_drawer",
]
assert len(UI_CHECKS) == 66 and len(set(UI_CHECKS)) == 66, (
    f"UI_CHECKS must be 66 unique ids, got {len(UI_CHECKS)}/{len(set(UI_CHECKS))}"
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Capture seam (same shape as ref_test_claude_cli_env.py)
# ---------------------------------------------------------------------------


class _EmptyStream:
    """Async byte iterator that yields no chunks (subprocess with silent stdout)."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeProc:
    returncode = 0
    stdin = None
    stderr = None

    def __init__(self):
        self.stdout = _EmptyStream()

    async def wait(self):
        return 0


def _install_spawn_capture(monkeypatch):
    """Patch the spawn seam, clear any inherited thinking key, return the spawn log."""
    spawns: list = []

    async def fake_exec(*args, **spawn_kwargs):
        spawns.append((args, spawn_kwargs))
        return _FakeProc()

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.delenv("MAX_THINKING_TOKENS", raising=False)
    return spawns


def _collect(**kwargs):
    """Drive the REAL claude_cli.run to exhaustion and return its events."""

    async def _drain():
        return [event async for event in claude_cli.run(**kwargs)]

    return asyncio.run(_drain())


def _has_pair(cmd, flag, value):
    cmd = list(cmd)
    return any(cmd[i] == flag and cmd[i + 1] == value for i in range(len(cmd) - 1))


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _conv_body(payload):
    """Conversation object from either a bare object or ``{"conversation": {...}}``."""
    if isinstance(payload, dict) and isinstance(payload.get("conversation"), dict):
        return payload["conversation"]
    return payload


def _conv_id(payload):
    body = _conv_body(payload)
    if isinstance(body, dict) and "id" in body:
        return body["id"]
    assert isinstance(payload, dict) and "id" in payload, f"no conversation id in {payload!r}"
    return payload["id"]


def _new_conversation():
    r = client.post("/api/conversations", json={})
    assert r.status_code in (200, 201), r.text
    return _conv_id(r.json())


def _patch_settings(conv_id, body):
    r = client.patch(f"/api/conversations/{conv_id}/settings", json=body)
    assert r.status_code == 200, (body, r.status_code, r.text)
    return r


def _get_conv(conv_id):
    r = client.get(f"/api/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    return _conv_body(r.json())


def _config_models():
    r = client.get("/api/config")
    assert r.status_code == 200, r.text
    cfg = r.json()
    return cfg["models"] if isinstance(cfg, dict) else cfg


# ---------------------------------------------------------------------------
# Websocket turn helper (bounded read so a silent server cannot wedge the suite;
# pattern copied from ref_test_ws_chat_socket.py).
# ---------------------------------------------------------------------------


def _read_frames(ws, label: str, max_frames: int = 6, timeout: float = 10.0):
    frames: list = []
    result: dict = {"timed_out": False, "error": None}

    def _read() -> None:
        try:
            while len(frames) < max_frames:
                msg = ws.receive_json()
                frames.append(msg)
                if isinstance(msg, dict) and msg.get("type") == "done":
                    return
        except BaseException as exc:  # noqa: BLE001 - surfaced through `result`
            result["error"] = exc

    reader = threading.Thread(target=_read, name=f"ws-read-{label}", daemon=True)
    reader.start()
    reader.join(timeout)
    result["timed_out"] = reader.is_alive()
    return frames, result


def _ws_turn(conv_id, message="go", max_frames=8):
    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": message})
        frames, read = _read_frames(ws, "j5-turn", max_frames=max_frames)
    assert not read["timed_out"], f"turn never completed; frames={frames!r} error={read['error']!r}"
    assert any(isinstance(f, dict) and f.get("type") == "done" for f in frames), (
        f"no done frame; frames={frames!r}"
    )
    return frames


# ===========================================================================
# argv unit
# ===========================================================================


def test_b_argv_effort_low():
    cmd = claude_cli.build_command(prompt="hi", model="m", effort="low")
    assert _has_pair(cmd, "--effort", "low"), cmd


def test_b_argv_effort_medium():
    cmd = claude_cli.build_command(prompt="hi", model="m", effort="medium")
    assert _has_pair(cmd, "--effort", "medium"), cmd


def test_b_argv_effort_high():
    cmd = claude_cli.build_command(prompt="hi", model="m", effort="high")
    assert _has_pair(cmd, "--effort", "high"), cmd


def test_b_argv_effort_xhigh():
    cmd = claude_cli.build_command(prompt="hi", model="m", effort="xhigh")
    assert _has_pair(cmd, "--effort", "xhigh"), cmd


def test_b_argv_effort_max():
    cmd = claude_cli.build_command(prompt="hi", model="m", effort="max")
    assert _has_pair(cmd, "--effort", "max"), cmd


def test_b_argv_effort_none_absent():
    cmd = claude_cli.build_command(prompt="hi", model="m", effort=None)
    assert "--effort" not in cmd, cmd


def test_b_argv_effort_empty_absent():
    cmd = claude_cli.build_command(prompt="hi", model="m", effort="")
    assert "--effort" not in cmd, cmd


def test_b_argv_effort_unknown_absent():
    base = list(claude_cli.build_command(prompt="hi", model="m", effort=None))
    for value in ("HIGH", "turbo", "high extra", "x;y", "-1"):
        cmd = list(claude_cli.build_command(prompt="hi", model="m", effort=value))
        assert "--effort" not in cmd, (value, cmd)
        assert value not in cmd, (value, cmd)
    # SPEC's "the value does not appear anywhere in cmd" cannot hold literally for
    # "--model": that string is the model flag itself. The only consistent reading
    # is that rejecting the effort value leaves the command byte-identical to the
    # same command built with effort=None, so a leaked value adds nothing.
    cmd = list(claude_cli.build_command(prompt="hi", model="m", effort="--model"))
    assert "--effort" not in cmd, cmd
    assert cmd == base, ("effort='--model' must be rejected", cmd, base)


def test_b_argv_effort_before_prompt():
    cmd = list(claude_cli.build_command(prompt="hi", model="m", effort="high"))
    assert cmd[-1] == "hi", cmd
    assert cmd.index("--effort") < cmd.index("hi"), cmd


def test_b_argv_permission_manual():
    cmd = claude_cli.build_command(prompt="hi", model="m", permission_mode="manual")
    assert _has_pair(cmd, "--permission-mode", "manual"), cmd


def test_b_argv_permission_acceptEdits():
    cmd = claude_cli.build_command(prompt="hi", model="m", permission_mode="acceptEdits")
    assert _has_pair(cmd, "--permission-mode", "acceptEdits"), cmd


def test_b_argv_permission_auto():
    cmd = claude_cli.build_command(prompt="hi", model="m", permission_mode="auto")
    assert _has_pair(cmd, "--permission-mode", "auto"), cmd


def test_b_argv_permission_bypassPermissions():
    cmd = claude_cli.build_command(prompt="hi", model="m", permission_mode="bypassPermissions")
    assert _has_pair(cmd, "--permission-mode", "bypassPermissions"), cmd


def test_b_argv_permission_dontAsk():
    cmd = claude_cli.build_command(prompt="hi", model="m", permission_mode="dontAsk")
    assert _has_pair(cmd, "--permission-mode", "dontAsk"), cmd


def test_b_argv_permission_plan():
    cmd = claude_cli.build_command(prompt="hi", model="m", permission_mode="plan")
    assert _has_pair(cmd, "--permission-mode", "plan"), cmd


def test_b_argv_permission_unknown_absent():
    for value in ("bogus", "bypassPermissions --x"):
        cmd = list(claude_cli.build_command(prompt="hi", model="m", permission_mode=value))
        assert "--permission-mode" not in cmd, (value, cmd)


def test_b_argv_model_flag():
    cmd = claude_cli.build_command(prompt="hi", model="claude-opus-5-5")
    assert _has_pair(cmd, "--model", "claude-opus-5-5"), cmd


def test_b_run_effort_in_spawn_argv(monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    events = _collect(prompt="hi", model="m", cwd=".", effort="medium")
    assert events[-1] == {"type": "done"}, events
    assert _has_pair(spawns[0][0], "--effort", "medium"), spawns[0][0]

    spawns.clear()
    events = _collect(prompt="hi", model="m", cwd=".")
    assert events[-1] == {"type": "done"}, events
    assert "--effort" not in spawns[0][0], spawns[0][0]


# ===========================================================================
# end to end through chat_socket
# ===========================================================================


def test_b_e2e_effort_reaches_argv(temp_db, monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"effort": "high"})
    _ws_turn(conv_id)
    assert _has_pair(spawns[-1][0], "--effort", "high"), spawns[-1][0]

    _patch_settings(conv_id, {"effort": "max"})
    _ws_turn(conv_id)
    assert _has_pair(spawns[-1][0], "--effort", "max"), spawns[-1][0]


def test_b_e2e_effort_default_no_flag(temp_db, monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    conv_id = _new_conversation()
    _ws_turn(conv_id)
    assert "--effort" not in spawns[-1][0], spawns[-1][0]


def test_b_e2e_effort_change_next_turn(temp_db, monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"effort": "high"})
    _ws_turn(conv_id)
    assert _has_pair(spawns[-1][0], "--effort", "high"), spawns[-1][0]

    _patch_settings(conv_id, {"effort": "medium"})
    _ws_turn(conv_id)
    args = list(spawns[-1][0])
    assert args.count("--effort") == 1, args
    assert _has_pair(args, "--effort", "medium"), args
    assert "high" not in args, args


def test_b_e2e_model_reaches_argv(temp_db, monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"model": "claude-opus-5-5"})
    _ws_turn(conv_id)
    assert _has_pair(spawns[-1][0], "--model", "claude-opus-5-5"), spawns[-1][0]


def test_b_e2e_permission_reaches_argv(temp_db, monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"permission_mode": "acceptEdits"})
    _ws_turn(conv_id)
    assert _has_pair(spawns[-1][0], "--permission-mode", "acceptEdits"), spawns[-1][0]

    default_conv = _new_conversation()
    _ws_turn(default_conv)
    assert _has_pair(spawns[-1][0], "--permission-mode", "manual"), spawns[-1][0]


def test_b_e2e_legacy_thinking_budget_not_in_env(temp_db, monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"thinking_budget": 4096})
    _ws_turn(conv_id)
    args, spawn_kwargs = spawns[-1]
    env = spawn_kwargs["env"]
    assert "MAX_THINKING_TOKENS" not in env, env.get("MAX_THINKING_TOKENS")
    assert "--thinking" not in args, args
    assert "--max-tokens" not in args, args


def test_b_e2e_thinking_never_in_env_default(temp_db, monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    conv_id = _new_conversation()
    _ws_turn(conv_id)
    env = spawns[-1][1]["env"]
    assert "MAX_THINKING_TOKENS" not in env, env.get("MAX_THINKING_TOKENS")


# ===========================================================================
# API + DB
# ===========================================================================


def test_b_api_effort_null_default(temp_db):
    r = client.post("/api/conversations", json={})
    assert r.status_code in (200, 201), r.text
    body = _conv_body(r.json())
    assert "effort" in body, body
    assert body["effort"] is None, body

    got = _get_conv(_conv_id(r.json()))
    assert "effort" in got, got
    assert got["effort"] is None, got


def test_b_api_effort_roundtrip(temp_db):
    conv_id = _new_conversation()
    r = _patch_settings(conv_id, {"effort": "high"})
    assert _conv_body(r.json())["effort"] == "high", r.text
    assert _get_conv(conv_id)["effort"] == "high"


def test_b_api_effort_all_levels(temp_db):
    conv_id = _new_conversation()
    for level in EFFORT_LEVELS:
        _patch_settings(conv_id, {"effort": level})
        assert _get_conv(conv_id)["effort"] == level, level


def test_b_api_effort_invalid_422(temp_db):
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"effort": "high"})
    for bad in ("turbo", "HIGH", "", 5):
        r = client.patch(f"/api/conversations/{conv_id}/settings", json={"effort": bad})
        assert r.status_code == 422, (bad, r.status_code, r.text)
        assert _get_conv(conv_id)["effort"] == "high", bad


def test_b_api_effort_clear(temp_db):
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"effort": "high"})
    _patch_settings(conv_id, {"clear_effort": True})
    assert _get_conv(conv_id)["effort"] is None


def test_b_api_effort_other_patch_keeps(temp_db):
    conv_id = _new_conversation()
    _patch_settings(conv_id, {"effort": "high"})
    model_value = _config_models()[0]["value"]
    _patch_settings(conv_id, {"model": model_value})
    assert _get_conv(conv_id)["effort"] == "high"
    assert _get_conv(conv_id)["model"] == model_value


def test_b_api_effort_per_conversation(temp_db):
    conv_a = _new_conversation()
    conv_b = _new_conversation()
    _patch_settings(conv_a, {"effort": "high"})
    _patch_settings(conv_b, {"effort": "low"})
    assert _get_conv(conv_a)["effort"] == "high"
    assert _get_conv(conv_b)["effort"] == "low"


def test_b_api_effort_db_column(temp_db):
    with get_connection() as conn:
        rows = conn.execute("PRAGMA table_info(conversations)").fetchall()
    effort_cols = [r for r in rows if r["name"] == "effort"]
    assert len(effort_cols) == 1, rows
    col = effort_cols[0]
    assert (col["type"] or "").upper() == "TEXT", col["type"]
    assert col["notnull"] == 0, dict(col)

    conv_id = _new_conversation()
    _patch_settings(conv_id, {"effort": "high"})
    with get_connection() as conn:
        row = conn.execute("SELECT effort FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    assert row is not None and row["effort"] == "high", row


def test_b_api_permission_roundtrip(temp_db):
    conv_id = _new_conversation()
    for mode in PERMISSION_MODES:
        _patch_settings(conv_id, {"permission_mode": mode})
        assert _get_conv(conv_id)["permission_mode"] == mode, mode

    r = client.patch(f"/api/conversations/{conv_id}/settings", json={"permission_mode": "bogus"})
    assert r.status_code == 422, r.text
    assert _get_conv(conv_id)["permission_mode"] == PERMISSION_MODES[-1]


def test_b_api_model_roundtrip(temp_db):
    values = [m["value"] for m in _config_models()]
    assert len(values) == 3, values

    conv_id = _new_conversation()
    for value in values:
        r = _patch_settings(conv_id, {"model": value})
        assert _conv_body(r.json())["model"] == value, r.text
        assert _get_conv(conv_id)["model"] == value

    r = client.patch(f"/api/conversations/{conv_id}/settings", json={"model": "gpt-4"})
    assert r.status_code == 422, r.text
    assert _get_conv(conv_id)["model"] == values[-1]


# ===========================================================================
# migration
# ===========================================================================


def _schema_version(conn):
    """The app's own version reader (measured: migrations._current_version(conn))."""
    return int(migrations_mod._current_version(conn))


def _insert_conversation_row(conn):
    """Insert a conversation filling every NOT NULL, default-less column."""
    cols = conn.execute("PRAGMA table_info(conversations)").fetchall()
    values = {
        "id": "j5-conv-1",
        "system_prompt": "keep me",
        "permission_mode": "acceptEdits",
        "thinking_budget": 4096,
    }
    for col in cols:
        name = col["name"]
        if name in values or col["pk"]:
            continue
        if col["notnull"] and col["dflt_value"] is None:
            values[name] = 0 if "INT" in (col["type"] or "").upper() else "x"
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO conversations ({names}) VALUES ({marks})", tuple(values.values()))
    conn.commit()


def test_b_migrations_shipped_entries_frozen():
    parts = [
        f"{v}|{d}|{b if isinstance(b, str) else (list(b) if isinstance(b, (list, tuple)) else b.__name__)}"
        for v, d, b in migrations_mod.MIGRATIONS
        if v <= 21
    ]
    digest = hashlib.sha256(repr(parts).encode()).hexdigest()
    assert digest == SHIPPED_MIGRATIONS_SHA256, parts


def test_b_migration_new_entry_appended():
    versions = [v for v, _d, _b in migrations_mod.MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1)), versions
    assert len(versions) >= 22, versions

    effort_versions = []
    for v, d, body in migrations_mod.MIGRATIONS:
        text = d if isinstance(d, str) else ""
        if isinstance(body, str):
            text += " " + body
        elif callable(body):
            text += " " + (getattr(body, "__name__", "") or "")
            text += " " + (getattr(body, "__doc__", "") or "")
        if "effort" in text.lower():
            effort_versions.append(v)
    assert effort_versions, "no migration entry mentions effort"
    assert min(effort_versions) >= 22, effort_versions


def _db(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def test_b_migration_upgrade_from_v21_preserves_rows(tmp_path, monkeypatch):
    # apply_migrations() takes no argument: it opens the DB at connection.DB_PATH.
    from app.db import connection as connection_module
    full = list(migrations_mod.MIGRATIONS)
    db_path = tmp_path / "j5_v21.sqlite"
    monkeypatch.setattr(connection_module, "DB_PATH", db_path)
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", [e for e in full if e[0] <= 21])
    migrations_mod.apply_migrations()
    conn = _db(db_path)
    try:
        _insert_conversation_row(conn)
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(migrations_mod, "MIGRATIONS", full)
    migrations_mod.apply_migrations()
    conn = _db(db_path)
    try:
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        assert "effort" in columns, columns
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", ("j5-conv-1",)).fetchone()
        assert row is not None, "the pre-upgrade conversation row vanished"
        assert row["system_prompt"] == "keep me", dict(row)
        assert row["permission_mode"] == "acceptEdits", dict(row)
        assert row["thinking_budget"] == 4096, dict(row)
        assert row["effort"] is None, dict(row)
        version = _schema_version(conn)
        assert version == len(full), f"schema version {version!r} != {len(full)}"
    finally:
        conn.close()


def test_b_migration_idempotent_rerun(tmp_path, monkeypatch):
    from app.db import connection as connection_module
    db_path = tmp_path / "j5_idempotent.sqlite"
    monkeypatch.setattr(connection_module, "DB_PATH", db_path)
    migrations_mod.apply_migrations()
    migrations_mod.apply_migrations()
    conn = _db(db_path)
    try:
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        assert columns.count("effort") == 1, columns
    finally:
        conn.close()


# ===========================================================================
# other
# ===========================================================================


def test_b_cli_help_effort_levels():
    exe = shutil.which("claude")
    assert exe, (
        "the claude CLI must be installed for this oracle (SPEC: a missing CLI is a failure, not a skip)"
    )
    if exe.lower().endswith((".cmd", ".bat")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", exe, "--help"]
    else:
        argv = [exe, "--help"]
    proc = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, (proc.returncode, text[-2000:])

    lines = text.splitlines()
    line_index = next((i for i, ln in enumerate(lines) if "--effort" in ln), None)
    assert line_index is not None, f"--effort not in `claude --help`:\n{text[-2000:]}"
    window_lines = [lines[line_index]]
    if line_index + 1 < len(lines):
        window_lines.append(lines[line_index + 1])
    window = "\n".join(window_lines)

    found = [
        level
        for level in EFFORT_LEVELS
        if re.search(rf"(?<![\w-]){re.escape(level)}(?![\w-])", window)
    ]
    assert set(found) == set(EFFORT_LEVELS), (
        f"levels printed after --effort {found!r} != {list(EFFORT_LEVELS)!r}\n{window!r}"
    )


def test_b_instrument_selftest(monkeypatch):
    spawns = _install_spawn_capture(monkeypatch)
    events = _collect(prompt="hi", model="claude-opus-5-5", cwd=".")
    assert events[-1] == {"type": "done"}, events

    args, spawn_kwargs = spawns[-1]
    assert _has_pair(args, "--model", "claude-opus-5-5"), args
    env = spawn_kwargs.get("env")
    assert isinstance(env, dict) and env, "the capture seam did not record the spawn env"
    assert "PATH" in env, sorted(env)[:10]


# ===========================================================================
# D1 backend guards (SPEC.md section D): the parent env is passed through and
# the app adds only the two documented token/mcp overrides. Driven through the
# same spawn-capture seam as ref_test_claude_cli_env.py; build_env is never
# called directly (its signature may change).
# ===========================================================================


def test_b_env_overrides_max_tokens_and_replaces_parent(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "999")
    parent_path = os.environ.get("PATH")
    spawns = _install_spawn_capture(monkeypatch)
    events = _collect(prompt="hi", model="m", cwd=".", max_tokens=4096)
    assert events[-1] == {"type": "done"}, events

    args, spawn_kwargs = spawns[-1]
    env = spawn_kwargs["env"]
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096", env.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
    assert env.get("PATH") == parent_path, (env.get("PATH"), parent_path)
    assert "MAX_THINKING_TOKENS" not in env, env.get("MAX_THINKING_TOKENS")
    assert "--max-tokens" not in args, args
    assert "--thinking" not in args, args


def test_b_env_mcp_timeout_only_with_prompt_tool(monkeypatch):
    monkeypatch.delenv("MCP_TOOL_TIMEOUT", raising=False)
    expected = str(int((permission_broker.APPROVAL_MAX_SECONDS + 30) * 1000))

    spawns = _install_spawn_capture(monkeypatch)
    events = _collect(
        prompt="hi",
        model="m",
        cwd=".",
        prompt_tool_url="http://127.0.0.1:1234/mcp/permission/t",
    )
    assert events[-1] == {"type": "done"}, events
    env = spawns[-1][1]["env"]
    assert env.get("MCP_TOOL_TIMEOUT") == expected, (env.get("MCP_TOOL_TIMEOUT"), expected)

    spawns.clear()
    events = _collect(prompt="hi", model="m", cwd=".")
    assert events[-1] == {"type": "done"}, events
    env = spawns[-1][1]["env"]
    assert "MCP_TOOL_TIMEOUT" not in env, env.get("MCP_TOOL_TIMEOUT")


def test_b_env_parent_plus_only_overrides(monkeypatch):
    monkeypatch.delenv("MAX_THINKING_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)

    spawns = _install_spawn_capture(monkeypatch)
    events = _collect(prompt="hi", model="m", cwd=".", max_tokens=4096)
    assert events[-1] == {"type": "done"}, events
    env = spawns[-1][1]["env"]
    want = {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"}
    assert env == want, set(env.items()) ^ set(want.items())

    spawns.clear()
    events = _collect(prompt="hi", model="m", cwd=".")
    assert events[-1] == {"type": "done"}, events
    env = spawns[-1][1]["env"]
    assert env == dict(os.environ), sorted(set(env.items()) ^ set(os.environ.items()))


# ===========================================================================
# UI half: isolated server on a random port, temp HOME, never :8765
# ===========================================================================


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    assert shutil.which("node"), "node is required for the J5 UI oracle"
    tmp = tmp_path_factory.mktemp("j5home")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert port != 8765, "refusing to run against the live app on :8765"
    env = dict(os.environ, USERPROFILE=str(tmp), HOME=str(tmp))
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
        result = subprocess.run(
            ["node", MJS],
            env=dict(env, GCA_URL=url),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
    lines = {}
    for ln in result.stdout.splitlines():
        m = re.match(r"^(PASS|FAIL) ([\w-]+)", ln)
        if m:
            lines.setdefault(m.group(2), (m.group(1), ln))
    return lines, result.stdout[-3000:] + result.stderr[-1500:]


@pytest.mark.parametrize("check", UI_CHECKS)
def test_j5_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
