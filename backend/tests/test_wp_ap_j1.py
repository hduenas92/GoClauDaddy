"""WP-AP J1 frozen oracle (Sonnet, 2026-10-13): the 'Allow always' rule store and the permission decision log.
Module under test: app.services.permissions (+ migration 21). Copied into backend/tests/ by oracle_run.py; uses conftest's temp_db."""
import sys

import pytest

from app.db import connection as connection_module
from app.logging_setup import get_logger

import app.services.permissions as P

CWD = r"C:\work\proj"
SECRET = "sk" + "-" + "ant-api03-" + "A" * 28           # fake, built in pieces so no scanner mistakes it for a key


def _tables():
    with connection_module.get_connection() as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}


def _version():
    with connection_module.get_connection() as c:
        return c.execute("select max(version) from schema_version").fetchone()[0]


def test_migration_21_creates_both_tables(temp_db):
    assert {"permission_rules", "permission_log"} <= _tables()
    assert _version() >= 21
    from app.db.migrations import MIGRATIONS
    versions = [m[0] for m in MIGRATIONS]
    assert 21 in versions and versions == sorted(set(versions)), versions     # appended, never edited in place


def test_rule_pattern_for_bash(temp_db):
    f = P.rule_pattern_for
    assert f("Bash", {"command": "echo hi"}) == "echo hi"
    assert f("Bash", {"command": "  echo hi "}) == "  echo hi "          # verbatim, never stripped
    assert f("Bash", {"command": "echo hi", "description": "d", "timeout": 5, "run_in_background": False}) == "echo hi"
    assert f("Bash", {"command": "x" * 4000}) == "x" * 4000
    for bad in ({"command": ""}, {"command": "   "}, {"command": "x" * 4001}, {"command": "a\x00b"}, {"command": 5},
                {"command": None}, {}, {"command": "ls", "dangerouslyDisableSandbox": True},
                {"command": "ls", "dangerouslyDisableSandbox": False}, {"command": "ls", "extra": 1}):
        assert f("Bash", bad) is None, bad
    for tool in ("Write", "Edit", "WebFetch", "bash", "mcp__x__y", "", None):
        assert f(tool, {"command": "ls"}) is None, tool


def test_add_rule_roundtrip_is_verbatim(temp_db):
    cmd = "echo 'it''s' > \"a b.txt\"; -- " + "DR" + "OP TABLE permission_rules;"
    r = P.add_rule("Bash", {"command": cmd, "description": "whatever"}, CWD)
    assert r.pattern == cmd and r.cwd == CWD and r.tool_name == "Bash"
    assert isinstance(r.id, str) and r.id and isinstance(r.created_at, str) and r.created_at and r.revoked_at is None
    got = P.list_rules()
    assert len(got) == 1 and got[0].id == r.id and got[0].pattern == cmd and got[0].cwd == CWD
    assert "permission_rules" in _tables()


def test_add_rule_rejects_what_it_cannot_express(temp_db):
    with pytest.raises(ValueError):
        P.add_rule("Write", {"file_path": "a", "content": "b"}, CWD)
    with pytest.raises(ValueError):
        P.add_rule("Bash", {"command": ""}, CWD)
    with pytest.raises(ValueError):
        P.add_rule("Bash", {"command": "ls", "dangerouslyDisableSandbox": True}, CWD)
    with pytest.raises(ValueError):
        P.add_rule("Bash", {"command": "ls"}, "")
    assert P.list_rules() == []


def test_add_rule_is_idempotent_and_list_is_newest_first(temp_db):
    a = P.add_rule("Bash", {"command": "ls"}, CWD)
    again = P.add_rule("Bash", {"command": "ls", "description": "other words"}, CWD)
    assert again.id == a.id and len(P.list_rules()) == 1
    b = P.add_rule("Bash", {"command": "pwd"}, CWD)
    c = P.add_rule("Bash", {"command": "ls"}, r"C:\work\other")
    assert [r.id for r in P.list_rules()] == [c.id, b.id, a.id]


def test_match_positive(temp_db):
    r = P.add_rule("Bash", {"command": "echo hi > a.txt"}, CWD)
    m = P.match_rule("Bash", {"command": "echo hi > a.txt"}, CWD)
    assert m is not None and m.id == r.id
    assert P.match_rule("Bash", {"command": "echo hi > a.txt", "description": "different words", "timeout": 9000,
                                 "run_in_background": False}, CWD).id == r.id


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spelling")
def test_match_cwd_spelling_on_windows(temp_db):
    r = P.add_rule("Bash", {"command": "ls"}, CWD)
    for spelling in (r"c:\WORK\proj", "C:/work/proj", "C:\\work\\proj\\", "C:/work/proj/"):
        m = P.match_rule("Bash", {"command": "ls"}, spelling)
        assert m is not None and m.id == r.id, spelling


def test_match_negatives_are_exact(temp_db):
    P.add_rule("Bash", {"command": "echo hi > a.txt"}, CWD)
    base = {"command": "echo hi > a.txt"}
    cases = [
        ("Bash", {"command": "echo hi > a.txt "}, CWD),                 # trailing space
        ("Bash", {"command": " echo hi > a.txt"}, CWD),
        ("Bash", {"command": "ECHO hi > a.txt"}, CWD),                  # case
        ("Bash", {"command": "echo hi > b.txt"}, CWD),                  # different command
        ("Bash", {"command": "echo hi"}, CWD),                          # a prefix of the rule
        ("Bash", {"command": "echo hi > a.txt && del x"}, CWD),         # rule is a prefix of the call
        ("Bash", {"command": "echo hi > a.txt; echo x"}, CWD),
        ("Bash", {"command": "echo hi > a.txt\n"}, CWD),
        ("Bash", base, r"C:\work\other"),                                # other folder
        ("Bash", base, r"C:\work\proj\sub"),                             # sub folder
        ("Bash", base, r"C:\work"),                                      # parent folder
        ("Bash", base, ""),
        ("Write", base, CWD),
        ("bash", base, CWD),
        ("Bash", {"command": "echo hi > a.txt", "dangerouslyDisableSandbox": True}, CWD),
        ("Bash", {"command": "echo hi > a.txt", "extra": "x"}, CWD),
        ("Bash", {"command": ["echo hi > a.txt"]}, CWD),
        ("Bash", {"command": None}, CWD),
        ("Bash", {}, CWD),
        ("Bash", None, CWD),
    ]
    for tool, inp, cwd in cases:
        assert P.match_rule(tool, inp, cwd) is None, (tool, inp, cwd)


def test_match_never_treats_the_rule_as_a_pattern(temp_db):
    P.add_rule("Bash", {"command": "echo *"}, CWD)
    P.add_rule("Bash", {"command": "rm -rf ."}, CWD)
    P.add_rule("Bash", {"command": "ls .*"}, CWD)
    assert P.match_rule("Bash", {"command": "echo hi"}, CWD) is None
    assert P.match_rule("Bash", {"command": "rm -rf ./x"}, CWD) is None
    assert P.match_rule("Bash", {"command": "ls abc"}, CWD) is None
    assert P.match_rule("Bash", {"command": "echo *"}, CWD) is not None


def test_revoke(temp_db):
    r = P.add_rule("Bash", {"command": "ls"}, CWD)
    assert P.revoke_rule(r.id) is True
    assert P.revoke_rule(r.id) is False
    assert P.revoke_rule("no-such-id") is False
    assert P.match_rule("Bash", {"command": "ls"}, CWD) is None
    assert P.list_rules() == []
    allr = P.list_rules(include_revoked=True)
    assert len(allr) == 1 and allr[0].id == r.id and allr[0].revoked_at
    fresh = P.add_rule("Bash", {"command": "ls"}, CWD)
    assert fresh.id != r.id and fresh.revoked_at is None
    assert P.match_rule("Bash", {"command": "ls"}, CWD).id == fresh.id


def _open(**kw):
    args = dict(conversation_id="c1", request_id="req-1", tool_use_id="toolu_1", tool_name="Bash",
                tool_input={"command": "echo hi > a.txt", "description": "d"}, cwd=CWD)
    args.update(kw)
    return P.log_open(**args)


def test_log_open_then_resolve(temp_db):
    lid = _open()
    assert isinstance(lid, int)
    row = P.list_log(10)[0]
    assert row["id"] == lid and row["conversation_id"] == "c1" and row["request_id"] == "req-1"
    assert row["tool_use_id"] == "toolu_1" and row["tool_name"] == "Bash" and row["cwd"] == CWD
    assert row["input"] == {"command": "echo hi > a.txt", "description": "d"}
    assert row["status"] == "pending" and row["source"] is None and row["rule_id"] is None
    assert row["reason"] is None and row["resolved_at"] is None and row["ts"]
    r = P.add_rule("Bash", {"command": "echo hi > a.txt"}, CWD)
    P.log_resolve(lid, status="allow", source="rule", rule_id=r.id, reason="matched")
    row = P.list_log(10)[0]
    assert (row["status"], row["source"], row["rule_id"], row["reason"]) == ("allow", "rule", r.id, "matched")
    assert row["resolved_at"]


def test_log_resolve_validates_and_never_rewrites(temp_db):
    lid = _open()
    for st, src in (("maybe", "rule"), ("allow", "vibes"), ("pending", "rule"), ("", "rule"), ("allow", "")):
        with pytest.raises(ValueError):
            P.log_resolve(lid, status=st, source=src)
    for src in ("rule", "user_once", "user_always", "user_deny", "timeout", "no_ui", "error", "stopped", "abandoned"):
        l2 = _open(request_id="r-" + src)
        P.log_resolve(l2, status="deny", source=src, reason="x")
    P.log_resolve(lid, status="deny", source="user_deny")
    with pytest.raises(ValueError):
        P.log_resolve(lid, status="allow", source="user_once")      # a decision is never rewritten
    with pytest.raises(ValueError):
        P.log_resolve(987654, status="deny", source="error")          # unknown id
    row = [r for r in P.list_log(100) if r["id"] == lid][0]
    assert (row["status"], row["source"]) == ("deny", "user_deny")


def test_log_redacts_secrets_but_rules_stay_exact(temp_db):
    cmd = f"curl -H 'Authorization: Bearer {SECRET}' https://x.example"
    _open(tool_input={"command": cmd})
    stored = repr(P.list_log(10)[0]["input"])
    assert SECRET not in stored and "[REDACTED]" in stored
    with connection_module.get_connection() as c:
        raw = " ".join(str(v) for row in c.execute("select * from permission_log") for v in tuple(row))
    assert SECRET not in raw
    r = P.add_rule("Bash", {"command": cmd}, CWD)
    assert r.pattern == cmd                                       # a rule must match byte for byte, so it is not redacted


def test_log_order_and_limit(temp_db):
    ids = [_open(request_id=f"r{i}") for i in range(5)]
    got = [r["id"] for r in P.list_log(3)]
    assert got == [ids[4], ids[3], ids[2]]
    assert len(P.list_log(100)) == 5
    assert P.list_log(0) == [] and P.list_log(-4) == []


def test_abandon_open_entries(temp_db):
    a, b, c = _open(request_id="a"), _open(request_id="b"), _open(request_id="c")
    P.log_resolve(c, status="allow", source="user_once")
    assert P.abandon_open_entries() == 2
    rows = {r["request_id"]: r for r in P.list_log(10)}
    for k in ("a", "b"):
        assert (rows[k]["status"], rows[k]["source"]) == ("deny", "abandoned")
        assert rows[k]["reason"] and rows[k]["resolved_at"]
    assert (rows["c"]["status"], rows["c"]["source"]) == ("allow", "user_once")
    assert P.abandon_open_entries() == 0


def test_every_open_and_resolve_is_also_written_to_the_app_log(temp_db):
    import logging

    seen = []

    class H(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    lg = get_logger("permission")
    h = H(); lg.addHandler(h)
    old = lg.level; lg.setLevel(logging.INFO)
    try:
        lid = _open(request_id="REQ-LOGLINE")
        P.log_resolve(lid, status="allow", source="user_once")
    finally:
        lg.removeHandler(h); lg.setLevel(old)
    assert any("REQ-LOGLINE" in m for m in seen), seen
    assert any("REQ-LOGLINE" in m and "allow" in m and "user_once" in m for m in seen), seen


def test_log_accepts_the_odd_values_the_broker_will_send_it(temp_db):
    a = _open(conversation_id=None, tool_use_id=None, request_id="odd-1")
    b = _open(tool_input="this is not a dict", request_id="odd-2")
    c = _open(tool_input=None, tool_name=None, request_id="odd-3")
    rows = {r["request_id"]: r for r in P.list_log(10)}
    assert rows["odd-1"]["conversation_id"] is None and rows["odd-1"]["tool_use_id"] is None
    assert rows["odd-2"]["input"] == "this is not a dict" and rows["odd-3"]["input"] is None and rows["odd-3"]["tool_name"] is None
    for lid in (a, b, c):
        P.log_resolve(lid, status="deny", source="error", reason="odd input")
