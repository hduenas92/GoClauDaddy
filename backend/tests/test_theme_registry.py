"""Contract tests for the theme registry and its CSS rule blocks.

The registry lives in ``frontend/index.html``. Its first entry is the default
theme for new users. Every registered id must have a
``[data-theme="<id>"]`` selector block in ``frontend/static/css/base.css``.
"""

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_HTML = _REPO_ROOT / "frontend" / "index.html"
_BASE_CSS = _REPO_ROOT / "frontend" / "static" / "css" / "base.css"

_THEME_ARRAY_RE = re.compile(
    r"window\.GCA_THEMES\s*=\s*\[(?P<body>.*?)\];",
    re.DOTALL,
)
_THEME_ID_RE = re.compile(
    r"\bid\s*:\s*(?P<quote>['\"])(?P<id>.+?)(?P=quote)",
)


def _read_registry_ids() -> list[str]:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    array_match = _THEME_ARRAY_RE.search(html)
    assert array_match, f"Could not find window.GCA_THEMES array in {_INDEX_HTML}"
    return [
        id_match.group("id")
        for id_match in _THEME_ID_RE.finditer(array_match.group("body"))
    ]


def _css_has_theme_block(css: str, theme_id: str) -> bool:
    selector_pattern = (
        r'\[\s*data-theme\s*=\s*["\']'
        + re.escape(theme_id)
        + r'["\']\s*\]\s*\{'
    )
    return re.search(selector_pattern, css) is not None


def test_registry_first_id_is_cyberpunk_console_default() -> None:
    ids = _read_registry_ids()
    assert ids, f"No theme ids found in {_INDEX_HTML}"
    assert ids[0] == "cyberpunk-console", (
        "The first registry entry must be the default theme for new users; "
        f"got {ids!r}"
    )


def test_lit_workbench_is_still_registered() -> None:
    ids = _read_registry_ids()
    assert "lit-workbench" in ids, (
        f"lit-workbench is missing from registry ids in {_INDEX_HTML}: {ids!r}"
    )


def test_every_registered_theme_has_a_css_block() -> None:
    ids = _read_registry_ids()
    css = _BASE_CSS.read_text(encoding="utf-8")
    missing = [theme_id for theme_id in ids if not _css_has_theme_block(css, theme_id)]
    assert not missing, (
        f"Missing [data-theme=\"<id>\"] CSS rule block(s) in {_BASE_CSS}: "
        f"{missing!r}"
    )
