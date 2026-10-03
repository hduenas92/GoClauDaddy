"""THEMECALM: contract tests for the third theme, "Calm".

Pins the ``[data-theme="calm"]`` token block in base.css to the exact values
the task specifies, checks its token set against the lit-workbench block, and
checks the registry in index.html keeps cyberpunk-console first and appends
calm last. It also computes WCAG relative luminance and asserts every
text/accent/status colour clears AA (>= 4.5:1) on all four calm backgrounds.
"""

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_HTML = _REPO_ROOT / "frontend" / "index.html"
_BASE_CSS = _REPO_ROOT / "frontend" / "static" / "css" / "base.css"

# The 17 (name, value) pairs the task requires, byte-exact.
CALM_TOKENS = {
    "--ground-rgb": "16 18 20",
    "--panel-rgb": "21 23 26",
    "--surface-rgb": "27 30 33",
    "--hover-rgb": "34 38 42",
    "--border-quiet-rgb": "44 49 54",
    "--border-strong-rgb": "66 73 80",
    "--text-primary-rgb": "226 230 233",
    "--text-secondary-rgb": "168 177 184",
    "--text-muted-rgb": "146 156 164",
    "--accent-rgb": "94 168 154",
    "--accent-hi-rgb": "132 194 180",
    "--cta-rgb": "218 145 98",
    "--working-rgb": "224 196 112",
    "--success-rgb": "132 182 124",
    "--warning-rgb": "218 172 108",
    "--error-rgb": "222 120 110",
    "--shadow-rgb": "0 0 0",
}

# Colours that must clear 4.5:1 on each background below.
_FOREGROUNDS = (
    "--text-primary-rgb",
    "--text-secondary-rgb",
    "--text-muted-rgb",
    "--accent-rgb",
    "--accent-hi-rgb",
    "--cta-rgb",
    "--working-rgb",
    "--success-rgb",
    "--warning-rgb",
    "--error-rgb",
)
_BACKGROUNDS = (
    "--ground-rgb",
    "--panel-rgb",
    "--surface-rgb",
    "--hover-rgb",
)

_DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+);")
_THEME_ARRAY_RE = re.compile(
    r"window\.GCA_THEMES\s*=\s*\[(?P<body>.*?)\];",
    re.DOTALL,
)
_THEME_ID_RE = re.compile(
    r"\bid\s*:\s*(?P<quote>['\"])(?P<id>.+?)(?P=quote)",
)


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _theme_block_bodies(css: str, theme_id: str) -> list[str]:
    """Bodies of top-level ``[data-theme="<id>"] { ... }`` blocks, braces balanced."""
    pattern = re.compile(
        r'(?m)^\[\s*data-theme\s*=\s*["\']'
        + re.escape(theme_id)
        + r'["\']\s*\]\s*\{'
    )
    bodies: list[str] = []
    for match in pattern.finditer(css):
        start = match.end()
        depth = 1
        for index in range(start, len(css)):
            char = css[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(css[start:index])
                    break
        else:
            raise AssertionError(
                f'unterminated [data-theme="{theme_id}"] block in {_BASE_CSS}'
            )
    return bodies


def _group_by_name(body: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for name, value in _DECLARATION.findall(body):
        grouped.setdefault(name, []).append(value.strip())
    return grouped


def _read_registry_ids() -> list[str]:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    array_match = _THEME_ARRAY_RE.search(html)
    assert array_match, f"Could not find window.GCA_THEMES array in {_INDEX_HTML}"
    return [
        id_match.group("id")
        for id_match in _THEME_ID_RE.finditer(array_match.group("body"))
    ]


_CSS_TEXT = _strip_comments(_BASE_CSS.read_text(encoding="utf-8"))
_CALM_BODIES = _theme_block_bodies(_CSS_TEXT, "calm")
_LIT_BODIES = _theme_block_bodies(_CSS_TEXT, "lit-workbench")


def _calm_token_map() -> dict[str, str]:
    assert len(_CALM_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="calm"] block in {_BASE_CSS}, '
        f"found {len(_CALM_BODIES)}"
    )
    grouped = _group_by_name(_CALM_BODIES[0])
    return {name: values[0] for name, values in grouped.items()}


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        if srgb <= 0.04045:
            return srgb / 12.92
        return ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(
    foreground: tuple[int, int, int], background: tuple[int, int, int]
) -> float:
    lum_fg = _relative_luminance(foreground)
    lum_bg = _relative_luminance(background)
    lighter, darker = max(lum_fg, lum_bg), min(lum_fg, lum_bg)
    return (lighter + 0.05) / (darker + 0.05)


def _rgb_triplet(token: str, value: str) -> tuple[int, int, int]:
    parts = value.split()
    assert len(parts) == 3, f"{token} must be an 'r g b' triplet, got {value!r}"
    channels = tuple(int(part) for part in parts)
    assert all(0 <= channel <= 255 for channel in channels), (
        f"{token} channels must be 0-255, got {value!r}"
    )
    return channels  # type: ignore[return-value]


def test_calm_block_declared_exactly_once() -> None:
    assert len(_CALM_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="calm"] block in {_BASE_CSS}, '
        f"found {len(_CALM_BODIES)}"
    )


def test_calm_block_has_exactly_the_required_token_values() -> None:
    assert len(_CALM_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="calm"] block in {_BASE_CSS}, '
        f"found {len(_CALM_BODIES)}"
    )
    grouped = _group_by_name(_CALM_BODIES[0])
    assert set(grouped) == set(CALM_TOKENS), (
        f"calm token set mismatch: missing {sorted(set(CALM_TOKENS) - set(grouped))}, "
        f"unexpected {sorted(set(grouped) - set(CALM_TOKENS))}"
    )
    wrong = {
        name: values
        for name, values in grouped.items()
        if values != [CALM_TOKENS[name]]
    }
    assert not wrong, (
        f"calm token(s) must be declared exactly once with the required value; "
        f"got {wrong!r}"
    )


def test_calm_token_set_equals_lit_workbench_token_set() -> None:
    assert len(_LIT_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="lit-workbench"] block in '
        f"{_BASE_CSS}, found {len(_LIT_BODIES)}"
    )
    calm_names = set(_group_by_name(_CALM_BODIES[0])) if _CALM_BODIES else set()
    lit_names = set(_group_by_name(_LIT_BODIES[0]))
    assert calm_names == lit_names, (
        f"calm and lit-workbench must declare the same token set; "
        f"calm-only {sorted(calm_names - lit_names)}, "
        f"lit-only {sorted(lit_names - calm_names)}"
    )


def test_cyberpunk_console_is_still_first_in_registry() -> None:
    ids = _read_registry_ids()
    assert ids, f"No theme ids found in {_INDEX_HTML}"
    assert ids[0] == "cyberpunk-console", (
        f"cyberpunk-console must stay the first (default) registry entry; got {ids!r}"
    )


def test_calm_is_registered_exactly_once() -> None:
    ids = _read_registry_ids()
    assert ids, f"No theme ids found in {_INDEX_HTML}"
    assert ids.count("calm") == 1, f"calm must be registered exactly once; got {ids!r}"
    assert ids[0] == "cyberpunk-console", f"cyberpunk-console must stay the default (first); got {ids!r}"


@pytest.mark.parametrize("foreground", _FOREGROUNDS)
@pytest.mark.parametrize("background", _BACKGROUNDS)
def test_calm_colour_contrast_meets_aa_on_every_background(
    foreground: str, background: str
) -> None:
    tokens = _calm_token_map()
    fg = _rgb_triplet(foreground, tokens[foreground])
    bg = _rgb_triplet(background, tokens[background])
    ratio = _contrast_ratio(fg, bg)
    assert ratio >= 4.5, (
        f"{foreground} {tokens[foreground]} on {background} {tokens[background]} "
        f"is {ratio:.2f}:1, below the WCAG AA 4.5:1 minimum"
    )
