"""THEMEWARM: contract tests for the "Warm" theme.

Pins the ``[data-theme="warm"]`` token block in base.css to the exact values
the task specifies, checks its token set against the lit-workbench block, checks
every non-colour token it declares also exists in the top-level ``:root`` block,
and checks the registry in index.html keeps cyberpunk-console first and
registers warm exactly once. It also computes WCAG relative luminance and
asserts every text/accent/status colour clears AA (>= 4.5:1) on all four warm
backgrounds.
"""

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_HTML = _REPO_ROOT / "frontend" / "index.html"
_BASE_CSS = _REPO_ROOT / "frontend" / "static" / "css" / "base.css"

# The 17 (name, value) pairs the task requires, byte-exact.
WARM_TOKENS = {
    "--ground-rgb": "20 17 15",
    "--panel-rgb": "26 22 19",
    "--surface-rgb": "33 28 24",
    "--hover-rgb": "42 36 31",
    "--border-quiet-rgb": "56 48 41",
    "--border-strong-rgb": "82 71 61",
    "--text-primary-rgb": "240 232 222",
    "--text-secondary-rgb": "190 176 160",
    "--text-muted-rgb": "165 151 136",
    "--accent-rgb": "222 150 82",
    "--accent-hi-rgb": "240 180 110",
    "--cta-rgb": "224 122 86",
    "--working-rgb": "230 196 92",
    "--success-rgb": "150 182 100",
    "--warning-rgb": "226 160 80",
    "--error-rgb": "226 104 92",
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
_ROOT_OPEN = re.compile(r"(?m)^:root\s*\{")


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


def _root_block(css: str) -> str:
    """Body of the single top-level (column-0) :root block, braces balanced."""
    matches = list(_ROOT_OPEN.finditer(css))
    assert len(matches) == 1, (
        f"expected exactly one top-level :root block in {_BASE_CSS}, "
        f"found {len(matches)}"
    )
    start = matches[0].end()
    depth = 1
    for index in range(start, len(css)):
        char = css[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return css[start:index]
    raise AssertionError(f"unterminated :root block in {_BASE_CSS}")


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
_WARM_BODIES = _theme_block_bodies(_CSS_TEXT, "warm")
_LIT_BODIES = _theme_block_bodies(_CSS_TEXT, "lit-workbench")


def _warm_token_map() -> dict[str, str]:
    assert len(_WARM_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="warm"] block in {_BASE_CSS}, '
        f"found {len(_WARM_BODIES)}"
    )
    grouped = _group_by_name(_WARM_BODIES[0])
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


def test_warm_block_declared_exactly_once() -> None:
    assert len(_WARM_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="warm"] block in {_BASE_CSS}, '
        f"found {len(_WARM_BODIES)}"
    )


def test_warm_block_has_exactly_the_required_token_values() -> None:
    assert len(_WARM_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="warm"] block in {_BASE_CSS}, '
        f"found {len(_WARM_BODIES)}"
    )
    grouped = _group_by_name(_WARM_BODIES[0])
    assert set(grouped) == set(WARM_TOKENS), (
        f"warm token set mismatch: missing {sorted(set(WARM_TOKENS) - set(grouped))}, "
        f"unexpected {sorted(set(grouped) - set(WARM_TOKENS))}"
    )
    wrong = {
        name: values
        for name, values in grouped.items()
        if values != [WARM_TOKENS[name]]
    }
    assert not wrong, (
        f"warm token(s) must be declared exactly once with the required value; "
        f"got {wrong!r}"
    )


def test_warm_token_set_equals_lit_workbench_token_set() -> None:
    assert len(_LIT_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="lit-workbench"] block in '
        f"{_BASE_CSS}, found {len(_LIT_BODIES)}"
    )
    warm_names = set(_group_by_name(_WARM_BODIES[0])) if _WARM_BODIES else set()
    lit_names = set(_group_by_name(_LIT_BODIES[0]))
    assert warm_names == lit_names, (
        f"warm and lit-workbench must declare the same token set; "
        f"warm-only {sorted(warm_names - lit_names)}, "
        f"lit-only {sorted(lit_names - warm_names)}"
    )


def test_warm_non_colour_tokens_are_declared_in_root() -> None:
    assert len(_WARM_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="warm"] block in {_BASE_CSS}, '
        f"found {len(_WARM_BODIES)}"
    )
    warm_names = set(_group_by_name(_WARM_BODIES[0]))
    non_colour = {name for name in warm_names if not name.endswith("-rgb")}
    root_names = set(_group_by_name(_root_block(_CSS_TEXT)))
    missing = sorted(non_colour - root_names)
    assert not missing, (
        f"non-colour token(s) warm declares but :root does not: {missing!r}"
    )


def test_cyberpunk_console_is_still_first_in_registry() -> None:
    ids = _read_registry_ids()
    assert ids, f"No theme ids found in {_INDEX_HTML}"
    assert ids[0] == "cyberpunk-console", (
        f"cyberpunk-console must stay the first (default) registry entry; got {ids!r}"
    )


def test_warm_is_registered_exactly_once() -> None:
    ids = _read_registry_ids()
    assert ids, f"No theme ids found in {_INDEX_HTML}"
    assert ids.count("warm") == 1, f"warm must be registered exactly once; got {ids!r}"


@pytest.mark.parametrize("foreground", _FOREGROUNDS)
@pytest.mark.parametrize("background", _BACKGROUNDS)
def test_warm_colour_contrast_meets_aa_on_every_background(
    foreground: str, background: str
) -> None:
    tokens = _warm_token_map()
    fg = _rgb_triplet(foreground, tokens[foreground])
    bg = _rgb_triplet(background, tokens[background])
    ratio = _contrast_ratio(fg, bg)
    assert ratio >= 4.5, (
        f"{foreground} {tokens[foreground]} on {background} {tokens[background]} "
        f"is {ratio:.2f}:1, below the WCAG AA 4.5:1 minimum"
    )
