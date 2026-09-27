"""Dry-run pending migrations against a COPY of the real database.

Why this exists: the pytest `temp_db` fixture starts from an empty schema, so it
can never exercise a backfill over existing rows. v15 rewrites message content
and v18 populates an FTS index from existing data — both are only meaningfully
testable against real history.

The real DB is never opened for writing. It is copied, migrated, and compared.

    ../.venv/Scripts/python.exe dryrun_migrations.py
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.config import DB_PATH  # noqa: E402
from app.db import connection as connection_module  # noqa: E402
from app.db.migrations import MIGRATIONS, apply_migrations  # noqa: E402

FAIL = []
def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -> {detail}" if detail else ""))
    if not ok:
        FAIL.append(label)


def snapshot(conn):
    """Row counts for every real table, plus schema version."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "AND name NOT LIKE '%_fts%' ORDER BY name"
    )]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    ver = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    return ver, counts


def main():
    real = Path(DB_PATH)
    if not real.exists():
        print(f"FATAL: no database at {real}")
        return 1

    print(f"real db : {real}  ({real.stat().st_size / 1024:.1f} KB)")

    # ---- before, read-only on the real file ------------------------------
    ro = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    before_ver, before_counts = snapshot(ro)
    before_content = {
        r[0]: r[1] for r in ro.execute("SELECT id, content FROM messages")
    }
    marker_rows_before = ro.execute(
        "SELECT COUNT(*) FROM messages WHERE content LIKE '%<!-- claudioui:stopped -->%'"
    ).fetchone()[0]
    ro.close()

    target = max(v for v, _, _ in MIGRATIONS)
    print(f"schema  : v{before_ver} -> v{target} ({target - before_ver} pending)")
    print(f"rows    : {before_counts}")
    print(f"stopped-marker rows in real data: {marker_rows_before}")
    print()

    # ---- copy and migrate -------------------------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="gca-dryrun-")) / "copy.db"
    shutil.copy2(real, tmp)
    for sfx in ("-wal", "-shm"):
        s = Path(str(real) + sfx)
        if s.exists():
            shutil.copy2(s, str(tmp) + sfx)
    print(f"copy    : {tmp}")

    connection_module.DB_PATH = tmp          # every later connection uses the copy
    try:
        apply_migrations()
    except Exception as e:
        print(f"\nFATAL: migrations raised {type(e).__name__}: {e}")
        return 1
    print("migrations applied to the copy without raising\n")

    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row

    print("Schema:")
    after_ver, after_counts = snapshot(conn)
    check(f"version is v{target}", after_ver == target, f"got v{after_ver}")

    print("\nNo data loss:")
    for t, n in before_counts.items():
        check(f"{t}: {n} rows preserved", after_counts.get(t) == n, f"now {after_counts.get(t)}")
    new_tables = set(after_counts) - set(before_counts)
    check("conversation_tags created", "conversation_tags" in new_tables, str(sorted(new_tables)))

    print("\nIntegrity:")
    check("PRAGMA integrity_check", conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    check("PRAGMA foreign_key_check empty", conn.execute("PRAGMA foreign_key_check").fetchall() == [])

    print("\nv15 stopped column:")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    check("messages.stopped exists", "stopped" in cols)
    check("messages.superseded_by exists", "superseded_by" in cols)
    still = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE content LIKE '%claudioui:stopped%'"
    ).fetchone()[0]
    check("no marker text left in any content", still == 0, f"{still} row(s) still contain it")
    flagged = conn.execute("SELECT COUNT(*) FROM messages WHERE stopped = 1").fetchone()[0]
    check(f"stopped flag set on the {marker_rows_before} marked row(s)",
          flagged == marker_rows_before, f"flagged {flagged}")

    print("\nContent preserved except for marker removal:")
    changed = []
    for r in conn.execute("SELECT id, content, stopped FROM messages"):
        old = before_content.get(r["id"])
        if old is None:
            changed.append((r["id"], "row is new?!"))
        elif r["content"] != old and not r["stopped"]:
            changed.append((r["id"], "content changed on a NON-stopped row"))
    check("only stopped rows had content rewritten", not changed, str(changed[:3]))

    print("\nv18 FTS index:")
    fts_n = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    msg_n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    check("index row count == messages row count", fts_n == msg_n, f"fts={fts_n} messages={msg_n}")

    # a real search over the user's actual history
    term = None
    for cand in ("Claude", "the", "you"):
        hits = conn.execute(
            "SELECT COUNT(*) FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid "
            "WHERE messages_fts MATCH ?", (cand,)
        ).fetchone()[0]
        if hits:
            term = (cand, hits)
            break
    check("a real search returns real hits", term is not None, f"matched {term}")

    live = conn.execute(
        "SELECT COUNT(*) FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid "
        "WHERE messages_fts MATCH ? AND m.superseded_by IS NULL", (term[0] if term else "x",)
    ).fetchone()[0]
    check("live-only filter runs against the index", live == (term[1] if term else 0),
          f"live={live} of {term[1] if term else 0} (all live, nothing superseded yet)")

    print("\nTrigger sync (insert / update / delete):")
    cid = conn.execute("SELECT id FROM conversations LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, seq, created_at) "
        "VALUES ('dryrun-probe', ?, 'user', 'zzqqx unique probe token', 9999, '2026-01-01')", (cid,)
    )
    found = conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'zzqqx'").fetchone()[0]
    check("INSERT reaches the index", found == 1, f"{found} hit(s)")

    conn.execute("UPDATE messages SET content = 'yywwv replaced token' WHERE id = 'dryrun-probe'")
    old_gone = conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'zzqqx'").fetchone()[0]
    new_here = conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'yywwv'").fetchone()[0]
    check("UPDATE removes the old term", old_gone == 0, f"{old_gone} stale hit(s)")
    check("UPDATE adds the new term", new_here == 1, f"{new_here} hit(s)")

    conn.execute("DELETE FROM messages WHERE id = 'dryrun-probe'")
    after_del = conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'yywwv'").fetchone()[0]
    check("DELETE removes it from the index", after_del == 0, f"{after_del} orphan(s)")
    check("index back in parity after probe cleanup",
          conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == msg_n)

    print("\nIdempotency (re-running must be a no-op):")
    try:
        apply_migrations()
        check("second apply_migrations() does not raise", True)
        check("version unchanged", conn.execute("SELECT version FROM schema_version").fetchone()[0] == target)
    except Exception as e:
        check("second apply_migrations() does not raise", False, f"{type(e).__name__}: {e}")

    print("\nAtomicity (a failing statement must leave the version untouched):")
    probe = Path(tempfile.mkdtemp(prefix="gca-atomic-")) / "atomic.db"
    shutil.copy2(real, probe)
    connection_module.DB_PATH = probe
    import app.db.migrations as mig
    saved = mig.MIGRATIONS
    try:
        mig.MIGRATIONS = saved + [(99, "deliberately broken", [
            "ALTER TABLE messages ADD COLUMN atomic_probe INTEGER",
            "THIS IS NOT VALID SQL",
        ])]
        try:
            apply_migrations()
            check("broken migration raises", False, "it did not raise")
        except Exception:
            check("broken migration raises", True)
        pc = sqlite3.connect(probe)
        ver = pc.execute("SELECT version FROM schema_version").fetchone()[0]
        cols2 = {r[1] for r in pc.execute("PRAGMA table_info(messages)")}
        check("version did NOT advance to 99", ver != 99, f"version is {ver}")
        check("the partial ALTER was rolled back", "atomic_probe" not in cols2,
              "column survived a failed migration")
        pc.close()
    finally:
        mig.MIGRATIONS = saved

    conn.close()
    print()
    print(f"copy left at: {tmp}   (real db untouched, still v{before_ver})")
    if FAIL:
        print(f"\nRESULT: FAIL — {len(FAIL)}")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("\nRESULT: PASS — safe to let the real database migrate on next start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
