"""FIX1: an empty #chat-status must not draw a box in the cyberpunk theme (it showed as a stray bar above the composer)."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[2] / "frontend" / "static" / "css" / "base.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"missing rule {selector}"
    return m.group(1)


def test_empty_status_line_draws_no_box():
    body = _rule('[data-theme="cyberpunk-console"] .chat-status:empty')
    assert re.search(r"border-color\s*:\s*transparent", body)
    assert re.search(r"background\s*:\s*transparent", body)
    assert re.search(r"box-shadow\s*:\s*none", body)


def test_status_line_keeps_its_height_when_empty():
    # the base rule reserves the height, so text appearing does not shift the composer
    assert re.search(r"min-height\s*:", _rule(".chat-status"))
