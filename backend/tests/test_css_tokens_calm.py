"""THEMECV1: guard the 28 calm design tokens added to the top-level :root block.

Pure refactor: the tokens are declared but nothing consumes them yet. This test
pins each (name, value), that none of them leaks outside :root, and that the
pre-existing spacing / radius / font-size tokens are untouched.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "frontend" / "static" / "css" / "base.css"

# (name, value) in the order the task specifies.
CALM_TOKENS = [
    ("--sp-2", "2px"),
    ("--sp-3", "3px"),
    ("--sp-4", "4px"),
    ("--sp-5", "5px"),
    ("--sp-6", "6px"),
    ("--sp-7", "7px"),
    ("--sp-8", "8px"),
    ("--sp-10", "10px"),
    ("--sp-12", "12px"),
    ("--sp-14", "14px"),
    ("--sp-16", "16px"),
    ("--sp-24", "24px"),
    ("--radius-2", "2px"),
    ("--radius-3", "3px"),
    ("--radius-6", "6px"),
    ("--radius-10", "10px"),
    ("--radius-12", "12px"),
    ("--radius-20", "20px"),
    ("--radius-full", "50%"),
    ("--fs-10", "10px"),
    ("--fs-11", "11px"),
    ("--fs-12", "12px"),
    ("--fs-13", "13px"),
    ("--fs-14", "14px"),
    ("--fs-16", "16px"),
    ("--fs-22", "22px"),
    ("--fs-36", "36px"),
    ("--fs-48", "48px"),
]

# Pre-existing declarations that must survive the refactor unchanged.
LEGACY_TOKENS = [
    ("--space-1", "4px"),
    ("--space-2", "8px"),
    ("--space-3", "12px"),
    ("--space-4", "16px"),
    ("--space-6", "24px"),
    ("--space-8", "32px"),
    ("--space-12", "48px"),
    ("--radius", "4px"),
    ("--radius-lg", "8px"),
    ("--chat-font-size", "14px"),
]

_DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+);")
_ROOT_OPEN = re.compile(r"(?m)^:root\s*\{")


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _root_block(text: str) -> str:
    """Body of the single top-level (column-0) :root block, braces balanced."""
    matches = list(_ROOT_OPEN.finditer(text))
    assert len(matches) == 1, f"expected exactly one top-level :root block, found {len(matches)}"
    start = matches[0].end()
    depth = 1
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index]
    raise AssertionError("unterminated :root block in base.css")


def _group_by_name(declarations):
    grouped = {}
    for name, value in declarations:
        grouped.setdefault(name, []).append(value)
    return grouped


_CSS_TEXT = _strip_comments(CSS.read_text(encoding="utf-8"))
_ALL_VALUES = _group_by_name(
    (name, value.strip()) for name, value in _DECLARATION.findall(_CSS_TEXT)
)
_ROOT_VALUES = _group_by_name(
    (name, value.strip()) for name, value in _DECLARATION.findall(_root_block(_CSS_TEXT))
)


def test_base_css_has_a_single_top_level_root_block():
    assert len(_ROOT_OPEN.findall(_CSS_TEXT)) == 1


@pytest.mark.parametrize("name,value", CALM_TOKENS)
def test_calm_token_declared_once_in_root_with_exact_value(name, value):
    root_values = _ROOT_VALUES.get(name, [])
    assert root_values == [value], (
        f"{name} must be declared exactly once in the top-level :root block with "
        f"value {value!r}; found {root_values!r}"
    )


_THEME_OPEN = re.compile(r'(?m)^\[data-theme="[^"]+"\]\s*\{')


def _theme_blocks(text: str) -> str:
    """Bodies of the top-level [data-theme="..."] variable blocks: themes may override tokens there (DESIGN.md)."""
    bodies = []
    for m in _THEME_OPEN.finditer(text):
        i, depth = m.end(), 1
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        bodies.append(text[m.end():i - 1])
    return "\n".join(bodies)


_THEME_VALUES = _group_by_name(
    (name, value.strip()) for name, value in _DECLARATION.findall(_theme_blocks(_CSS_TEXT))
)


@pytest.mark.parametrize("name,value", CALM_TOKENS)
def test_calm_token_not_declared_outside_root(name, value):
    """Declared in :root; a theme block may override it; a component rule may not."""
    outside = (len(_ALL_VALUES.get(name, [])) - len(_ROOT_VALUES.get(name, []))
               - len(_THEME_VALUES.get(name, [])))
    assert outside == 0, f"{name} is declared {outside} time(s) in component rules (outside :root and theme blocks)"


@pytest.mark.parametrize("name,value", LEGACY_TOKENS)
def test_legacy_token_present_and_unchanged(name, value):
    root_values = _ROOT_VALUES.get(name, [])
    assert root_values == [value], (
        f"{name} must stay declared once in :root with value {value!r}; "
        f"found {root_values!r}"
    )
