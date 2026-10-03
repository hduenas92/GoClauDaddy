"""THEMECV9: guard the 5 shadow design tokens and their consumers.

Pure refactor: five repeated box-shadow values moved into :root tokens, and the
11 declarations that held the literals now reference the tokens. This test pins
the token declarations, the per-token consumer count, and that no literal value
survives outside the variable blocks.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "frontend" / "static" / "css" / "base.css"

# (name, value) in the order the task specifies.
SHADOW_TOKENS = [
    ("--shadow-1", "0 0 22px rgba(var(--accent-rgb) / 0.28), 0 0 2px rgba(var(--accent-hi-rgb) / 1)"),
    ("--shadow-2", "0 0 6px rgba(var(--accent-hi-rgb) / 0.25)"),
    ("--shadow-3", "0 0 6px rgba(var(--accent-hi-rgb) / 0.3)"),
    ("--shadow-4", "0 0 16px -2px rgba(var(--accent-hi-rgb) / 0.3)"),
    ("--shadow-5", "0 4px 20px rgba(var(--accent-rgb) / 0.15)"),
]

# Consumers expected per token after the refactor: 3 + 2 + 2 + 2 + 2 = 11.
EXPECTED_CONSUMERS = {
    "--shadow-1": 3,
    "--shadow-2": 2,
    "--shadow-3": 2,
    "--shadow-4": 2,
    "--shadow-5": 2,
}

_DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+);")
_BOX_SHADOW = re.compile(r"box-shadow\s*:\s*([^;{}]+);")
_TOKEN_USE = re.compile(r"var\((--shadow-[1-5])\)")
_ROOT_OPEN = re.compile(r"(?m)^:root\s*\{")
# Only the bare `:root` / `[data-theme="..."]` blocks hold variable declarations;
# themed component rules like `[data-theme="..."] #chat-header {` do not.
_ORIGIN_OPEN = re.compile(r"(?m)^(?::root|\[data-theme\s*=\s*\"[^\"]*\"\])\s*\{")


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _collapse(value):
    return " ".join(value.split())


def _block_span(text, match):
    """(start, end) body span of a brace-balanced block opened at match.end()."""
    start = match.end()
    depth = 1
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, index
    raise AssertionError("unterminated block in base.css")


def _root_block(text):
    matches = list(_ROOT_OPEN.finditer(text))
    assert len(matches) == 1, f"expected exactly one top-level :root block, found {len(matches)}"
    start, end = _block_span(text, matches[0])
    return text[start:end]


def _group_by_name(declarations):
    grouped = {}
    for name, value in declarations:
        grouped.setdefault(name, []).append(_collapse(value))
    return grouped


_TEXT = _strip_comments(CSS.read_text(encoding="utf-8"))
_ALL_VALUES = _group_by_name(_DECLARATION.findall(_TEXT))
_ROOT_VALUES = _group_by_name(_DECLARATION.findall(_root_block(_TEXT)))
_ORIGIN_SPANS = [_block_span(_TEXT, match) for match in _ORIGIN_OPEN.finditer(_TEXT)]


def _box_shadow_values(text):
    return [(_collapse(match.group(1)).rstrip(), match.start()) for match in _BOX_SHADOW.finditer(text)]


def test_base_css_has_a_single_top_level_root_block():
    assert len(_ROOT_OPEN.findall(_TEXT)) == 1


@pytest.mark.parametrize("name,value", SHADOW_TOKENS)
def test_shadow_token_declared_once_in_root_with_exact_value(name, value):
    root_values = _ROOT_VALUES.get(name, [])
    assert root_values == [value], (
        f"{name} must be declared exactly once in the top-level :root block with "
        f"value {value!r}; found {root_values!r}"
    )


@pytest.mark.parametrize("name,value", SHADOW_TOKENS)
def test_shadow_token_not_declared_outside_root(name, value):
    outside = len(_ALL_VALUES.get(name, [])) - len(_ROOT_VALUES.get(name, []))
    assert outside == 0, f"{name} is declared {outside} time(s) outside :root"


def test_exactly_eleven_box_shadow_declarations_use_the_tokens():
    counts = {}
    for value, _ in _box_shadow_values(_TEXT):
        use = _TOKEN_USE.fullmatch(value)
        if use:
            counts[use.group(1)] = counts.get(use.group(1), 0) + 1
    assert counts == EXPECTED_CONSUMERS
    assert sum(counts.values()) == 11


def test_no_box_shadow_literal_outside_variable_blocks():
    literals = {value for _, value in SHADOW_TOKENS}
    hits = []
    for value, start in _box_shadow_values(_TEXT):
        if value not in literals:
            continue
        if any(lo <= start < hi for lo, hi in _ORIGIN_SPANS):
            continue
        line = _TEXT.count("\n", 0, start) + 1
        hits.append(f"base.css:{line}: {value}")
    assert not hits, "box-shadow literal(s) outside the variable blocks:\n" + "\n".join(hits)
