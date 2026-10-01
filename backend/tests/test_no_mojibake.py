"""Guard: no double-encoded UTF-8 (mojibake) in shipped source. 22 comment lines had it until 2026-10-01,
and the same defect once reached the UI (3361d55)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKERS = ("â€", "Ã¢")  # how a UTF-8 dash/quote reads after a cp1252 round-trip


def test_no_mojibake_in_source():
    hits = []
    for base in (ROOT / "backend" / "app", ROOT / "frontend"):
        for p in base.rglob("*"):
            if p.suffix not in {".py", ".sql", ".js", ".css", ".html"} or "vendor" in p.parts:
                continue
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if any(m in line for m in MARKERS):
                    hits.append(f"{p.relative_to(ROOT)}:{n}")
    assert not hits, hits
