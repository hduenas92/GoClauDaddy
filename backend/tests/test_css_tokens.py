"""Guard: every colour in base.css must go through a custom property."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "frontend" / "static" / "css" / "base.css"

COLOR = re.compile(
    r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b"
    r"|\b(?:rgb|rgba|hsl|hsla)\(\s*[+-]?(?:\d*\.\d+|\d+\.?\d*)",
    re.IGNORECASE,
)
CUSTOM_PROPERTY = re.compile(r"--[\w-]+\s*:[^;{}]*(?:;|(?=\}))", re.S)


def _blank_keep_newlines(value):
    return "".join("\n" if char == "\n" else " " for char in value)


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", lambda match: _blank_keep_newlines(match.group(0)), text, flags=re.S)


def _without_custom_properties(text):
    return CUSTOM_PROPERTY.sub(lambda match: _blank_keep_newlines(match.group(0)), text)


def test_no_raw_colours_outside_custom_properties():
    text = _without_custom_properties(_strip_comments(CSS.read_text(encoding="utf-8")))
    hits = []
    for match in COLOR.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        hits.append(f"{CSS.relative_to(ROOT)}:{line}")
    assert not hits, "raw colour literal(s) outside custom properties:\n" + "\n".join(hits)
