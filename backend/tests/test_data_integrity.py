"""Pytest-level data-integrity tests for QA_AGENT_INSTRUCTIONS.md §2.

Each gap (G1-G8) is pinned here as a test that a real defect can fail:

G1  5-turn token accumulation through the WS handler, hand-computed totals.
G2  `created_at` is set, UTC, and round-trips through list_messages / the API.
G3  `messages.model` is the model the turn RAN under, even after a mid-thread switch.
G5  `seq` uniqueness/ordering under rapid sends (sequential + concurrent threads).
G6  PRAGMA integrity_check / foreign_key_check after a representative flow.
G7  Deleting a conversation removes its attachment files from disk.
G8  Monthly cost lands on the right side of the UTC month boundary.

G4 lives in test_ctx_window.py (CTX% is computed in frontend JS; the backend
supplies the two operands, so the backend data is tested there).
"""

import datetime
import threading
import time
from pathlib import Path

import app.services.claude_cli as claude_cli_mod
from app.config import MODELS
from app.db.connection import get_connection
from app.main import app
from app.routers import server as server_mod
from app.services import attachments_service as attsvc
from app.services import conversations_service as convs
from app.services import projects_service as projects
from app.services.cost import compute_cost_usd
from fastapi.testclient import TestClient

client = TestClient(app)

HAIKU = "claude-haiku-4-5-20251001"
OPUS = MODELS[1]["id"]
SONNET = MODELS[0]["id"]


# ---------------------------------------------------------------------------
# Shared helpers (thread-safe reads: the WS server runs in a background thread).
# ---------------------------------------------------------------------------

def _usage(inp, out, cache_read=0, cache_creation=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


def _read_frames(ws, label, max_frames=20, timeout=5.0):
    """Read frames off a WS with a deadline; never block the suite forever."""
    frames = []
    result = {"timed_out": False, "error": None}

    def _read():
        try:
            while len(frames) < max_frames:
                msg = ws.receive_json()
                frames.append(msg)
                if isinstance(msg, dict) and msg.get("type") == "done":
                    return
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    reader = threading.Thread(target=_read, name=f"ws-read-{label}", daemon=True)
    reader.start()
    reader.join(timeout)
    result["timed_out"] = reader.is_alive()
    return frames, result


def _wait_for_row_count(conv_id, expected, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)
            ).fetchone()[0]
        if n >= expected:
            return True
        time.sleep(0.01)
    return False


def _run_ws_turn(conv_id, events, expected_rows, monkeypatch):
    """One WS `send`, driven by a fake claude_cli.run, drained until `done`."""
    gen_done = threading.Event()

    async def fake_run(**kwargs):
        for e in events:
            yield e
        gen_done.set()

    monkeypatch.setattr(claude_cli_mod, "run", fake_run)
    with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
        ws.send_json({"type": "send", "message": "go"})
        frames, read = _read_frames(ws, "turn", max_frames=len(events) + 2)
        gen_done_ok = gen_done.wait(timeout=2.0)
        rows_ok = (not read["timed_out"]) and _wait_for_row_count(conv_id, expected_rows)

    assert not read["timed_out"], f"turn never finished; frames={frames!r}"
    assert gen_done_ok, "generator never exhausted"
    assert rows_ok, f"expected {expected_rows} rows after the turn; frames={frames!r}"
    return frames


def _assistant_rows(conv_id):
    return [m for m in convs.list_messages(conv_id) if m.role == "assistant"]


# ---------------------------------------------------------------------------
# G1 — item 1: 5-turn token accumulation, hand-computed per turn and in total.
# ---------------------------------------------------------------------------

# Per-turn usage emitted to the handler: (input, output, cache_read, cache_creation)
TURNS = [
    (100, 10, 1000, 200),
    (150, 20, 500, 300),
    (200, 30, 0, 0),
    (250, 40, 700, 100),
    (300, 50, 900, 250),
]

# Totals the DB must contain over the five assistant rows:
#   input          100+150+200+250+300 = 1000
#   output          10+20+30+40+50     =  150
#   cache_read     1000+500+0+700+900  = 3100
#   cache_creation  200+300+0+100+250  =  850


def test_five_turn_accumulation_matches_hand_computed_totals(temp_db, monkeypatch):
    conv_id = convs.create_conversation(model=SONNET).id

    for turn_no, (inp, out, cread, ccreat) in enumerate(TURNS, start=1):
        events = [
            {"type": "text", "text": f"answer {turn_no}"},
            {"type": "usage", "usage": _usage(inp, out, cache_read=cread, cache_creation=ccreat)},
        ]
        # Each turn adds one user row + one assistant row.
        _run_ws_turn(conv_id, events, expected_rows=2 * turn_no, monkeypatch=monkeypatch)

    rows = _assistant_rows(conv_id)
    assert len(rows) == 5, f"expected 5 assistant rows, got {len(rows)}"

    for row, (inp, out, cread, ccreat) in zip(rows, TURNS):
        assert row.input_tokens == inp, f"seq {row.seq}: input {row.input_tokens} != {inp}"
        assert row.output_tokens == out, f"seq {row.seq}: output {row.output_tokens} != {out}"
        assert row.cache_read_tokens == cread, f"seq {row.seq}: cache_read {row.cache_read_tokens} != {cread}"
        assert row.cache_creation_tokens == ccreat, f"seq {row.seq}: cache_creation {row.cache_creation_tokens} != {ccreat}"

    with get_connection() as conn:
        totals = conn.execute(
            """SELECT COALESCE(SUM(input_tokens), 0)            AS ti,
                      COALESCE(SUM(output_tokens), 0)           AS to_,
                      COALESCE(SUM(cache_read_tokens), 0)       AS tcr,
                      COALESCE(SUM(cache_creation_tokens), 0)   AS tcc
               FROM messages WHERE conversation_id = ? AND role = 'assistant'""",
            (conv_id,),
        ).fetchone()
    assert totals["ti"] == 1000, f"total input {totals['ti']} != 1000 (100+150+200+250+300)"
    assert totals["to_"] == 150, f"total output {totals['to_']} != 150 (10+20+30+40+50)"
    assert totals["tcr"] == 3100, f"total cache_read {totals['tcr']} != 3100 (1000+500+0+700+900)"
    assert totals["tcc"] == 850, f"total cache_creation {totals['tcc']} != 850 (200+300+0+100+250)"


# ---------------------------------------------------------------------------
# G2 — item 3: `created_at` is set, is UTC, and round-trips unchanged.
# ---------------------------------------------------------------------------

def test_created_at_is_utc_and_round_trips(temp_db):
    conv_id = convs.create_conversation().id
    msg = convs.add_message(conv_id, "user", "hello")

    assert msg.created_at, "created_at must be set"
    parsed = datetime.datetime.fromisoformat(msg.created_at)
    assert parsed.utcoffset() is not None, "created_at must carry an offset (UTC)"
    assert parsed.utcoffset() == datetime.timedelta(0), (
        f"created_at offset {parsed.utcoffset()} != UTC"
    )

    listed = convs.list_messages(conv_id)
    assert listed[0].created_at == msg.created_at, "list_messages changed created_at"

    api = client.get(f"/api/conversations/{conv_id}").json()
    assert api["messages"][0]["created_at"] == msg.created_at, "conversation API changed created_at"


# ---------------------------------------------------------------------------
# G3 — item 3: `messages.model` is the model the turn ran under.
# ---------------------------------------------------------------------------

def test_stored_model_is_the_turn_model_after_mid_conversation_switch(temp_db, monkeypatch):
    conv_id = convs.create_conversation(model=HAIKU).id
    cli_models = []

    def drive_turn(expected_rows):
        gen_done = threading.Event()

        async def fake_run(**kwargs):
            cli_models.append(kwargs["model"])
            yield {"type": "text", "text": "ok"}
            gen_done.set()

        monkeypatch.setattr(claude_cli_mod, "run", fake_run)
        with client.websocket_connect(f"/ws/chat/{conv_id}") as ws:
            ws.send_json({"type": "send", "message": "q"})
            frames, read = _read_frames(ws, "model-turn", max_frames=3)
            gen_done_ok = gen_done.wait(timeout=2.0)
            rows_ok = (not read["timed_out"]) and _wait_for_row_count(conv_id, expected_rows)
        assert not read["timed_out"], f"turn never finished; frames={frames!r}"
        assert gen_done_ok, "generator never exhausted"
        assert rows_ok, f"expected {expected_rows} rows; frames={frames!r}"

    drive_turn(expected_rows=2)
    client.patch(f"/api/conversations/{conv_id}/settings", json={"model": OPUS})
    drive_turn(expected_rows=4)

    rows = _assistant_rows(conv_id)
    assert len(rows) == 2
    assert rows[0].model == HAIKU, f"first turn ran on Haiku but row says {rows[0].model!r}"
    assert rows[1].model == OPUS, f"second turn ran on Opus but row says {rows[1].model!r}"
    assert cli_models == [HAIKU, OPUS], f"CLI saw {cli_models!r}, expected [Haiku, Opus]"


# ---------------------------------------------------------------------------
# G5 — item 7: `seq` uniqueness and ordering under rapid sends.
# ---------------------------------------------------------------------------

def _seqs(conv_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT seq FROM messages WHERE conversation_id = ? ORDER BY seq", (conv_id,)
        ).fetchall()
    return [r["seq"] for r in rows]


def test_seq_strictly_increasing_50_back_to_back_calls(temp_db):
    conv_id = convs.create_conversation().id
    for _ in range(50):
        convs.add_message(conv_id, "user", "x")
    assert _seqs(conv_id) == list(range(1, 51))


def test_seq_unique_and_increasing_8_threads_x_10_calls(temp_db):
    conv_id = convs.create_conversation().id
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(10):
            convs.add_message(conv_id, "user", "x")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = _seqs(conv_id)
    assert len(seqs) == 80, f"expected 80 rows, got {len(seqs)}"
    assert len(set(seqs)) == 80, f"duplicate seq values: {len(seqs) - len(set(seqs))} duplicates"
    assert seqs == list(range(1, 81)), f"seqs not exactly 1..80: {seqs[:20]}..."


# ---------------------------------------------------------------------------
# G6 — item 10: PRAGMA integrity_check / foreign_key_check after a real flow.
# ---------------------------------------------------------------------------

def test_pragma_integrity_and_foreign_keys_after_representative_flow(temp_db, tmp_path, monkeypatch):
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(attsvc, "ATTACHMENTS_DIR", att_dir)
    monkeypatch.setattr(convs, "ATTACHMENTS_DIR", att_dir)

    project = projects.create_project("p6", working_dir=str(tmp_path))
    conv1 = convs.create_conversation(project_id=project.id, model=SONNET)
    conv2 = convs.create_conversation(project_id=project.id, model=SONNET)

    user1 = convs.add_message(conv1.id, "user", "q1")
    convs.add_message(
        conv1.id, "assistant", "a1",
        tool_calls='[{"id": "t1", "name": "Read", "input": {"file_path": "/tmp/a"}}]',
        model=SONNET, input_tokens=12, output_tokens=34,
    )
    user2 = convs.add_message(conv2.id, "user", "q2")
    convs.add_message(conv2.id, "assistant", "a2", model=SONNET, input_tokens=5, output_tokens=6)

    att = attsvc.save_attachment(conv2.id, "note.txt", b"hello", "text/plain")
    attsvc.attach_to_message([att.id], user2.id)

    # Anti-vacuity guard: the PRAGMA assertions below only mean something if the
    # flow actually wrote rows before the delete.
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 1

    convs.delete_conversation(conv1.id)

    with get_connection() as conn:
        integrity = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert integrity == ["ok"], f"integrity_check returned {integrity!r}"
    assert fk_violations == [], f"foreign_key_check returned {len(fk_violations)} rows: {fk_violations[:3]}"


# ---------------------------------------------------------------------------
# G7 — item 11 gap: deleting a conversation removes attachment files on disk.
# ---------------------------------------------------------------------------

def test_deleting_conversation_removes_attachment_file_and_row(temp_db, tmp_path, monkeypatch):
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(attsvc, "ATTACHMENTS_DIR", att_dir)
    monkeypatch.setattr(convs, "ATTACHMENTS_DIR", att_dir)

    conv_id = convs.create_conversation().id
    att = attsvc.save_attachment(conv_id, "keep.txt", b"data", "text/plain")
    stored = Path(att.stored_path)
    assert stored.exists(), "attachment file was not created"

    convs.delete_conversation(conv_id)

    assert not stored.exists(), "attachment FILE survived conversation deletion"
    with get_connection() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM attachments WHERE conversation_id = ?", (conv_id,)
        ).fetchone()[0]
    assert remaining == 0, f"{remaining} attachments rows survived conversation deletion"


# ---------------------------------------------------------------------------
# G8 — item 12: monthly cost across the UTC boundary.
# ---------------------------------------------------------------------------

def test_monthly_cost_lands_on_the_right_side_of_the_utc_boundary(temp_db):
    # server.py:49 computes the cutoff as datetime.now(timezone.utc).date().replace(day=1)
    # — i.e. UTC, not local time. Build rows just either side of the CURRENT month
    # boundary so the real cutoff is exercised.
    now_utc = datetime.datetime.now(datetime.UTC)
    month_start = now_utc.date().replace(day=1)
    prev_last = month_start - datetime.timedelta(days=1)
    prev_ts = f"{prev_last.isoformat()}T23:59:59+00:00"
    cur_ts = f"{month_start.isoformat()}T00:00:01+00:00"

    conv_id = convs.create_conversation(model=SONNET).id
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, thinking, tool_calls, input_tokens,
                output_tokens, model, cache_read_tokens, cache_creation_tokens, seq, created_at, stopped)
               VALUES (?, ?, 'assistant', ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, 0)""",
            ("m-prev", conv_id, "previous month", 999, 9, SONNET, 0, 0, 1, prev_ts),
        )
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, thinking, tool_calls, input_tokens,
                output_tokens, model, cache_read_tokens, cache_creation_tokens, seq, created_at, stopped)
               VALUES (?, ?, 'assistant', ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, 0)""",
            ("m-cur", conv_id, "current month", 1000, 50, SONNET, 100, 200, 2, cur_ts),
        )
        # Anti-vacuity guard: both boundary rows must exist before we assert that
        # the monthly rollup excludes one of them.
        n = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)
        ).fetchone()[0]
    assert n == 2, f"expected 2 inserted boundary rows, found {n}"

    stats = server_mod.server_stats()

    assert stats["monthly_input"] == 1000, (
        f"monthly_input {stats['monthly_input']} != 1000 — the 23:59:59Z row leaked in"
    )
    assert stats["monthly_output"] == 50, (
        f"monthly_output {stats['monthly_output']} != 50 — the 23:59:59Z row leaked in"
    )
    # Sonnet: 1000/1e6*3 + 50/1e6*15 + 100/1e6*3*0.1 + 200/1e6*3*1.25 = 0.00453 -> 0.0045
    expected_cost = round(compute_cost_usd(SONNET, 1000, 50, 100, 200), 4)
    assert stats["monthly_cost_usd"] == expected_cost, (
        f"monthly_cost_usd {stats['monthly_cost_usd']} != {expected_cost}"
    )
