"""THEMEDENSE: contract tests for the fourth theme, "Dense".

Pins the ``[data-theme="dense"]`` token block in base.css to the exact values
the task specifies, checks its ``*-rgb`` token set against the lit-workbench
block, checks every non-colour token it overrides is already declared in
``:root``, and checks the registry in index.html registers dense while
cyberpunk-console stays first. It also computes WCAG relative luminance and
asserts every text/accent/status colour clears AA (>= 4.5:1) on all four dense
backgrounds.

Dense deliberately overrides spacing/radius/font-size tokens (a compact scale);
those are not new tokens, they exist in ``:root`` and are only re-valued here.
"""

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_HTML = _REPO_ROOT / "frontend" / "index.html"
_BASE_CSS = _REPO_ROOT / "frontend" / "static" / "css" / "base.css"

# The 50 (name, value) pairs the task requires, byte-exact.
DENSE_TOKENS = {
    "--ground-rgb": "12 12 15",
    "--panel-rgb": "17 17 24",
    "--surface-rgb": "24 24 31",
    "--hover-rgb": "31 31 42",
    "--border-quiet-rgb": "39 39 58",
    "--border-strong-rgb": "60 60 86",
    "--text-primary-rgb": "240 240 248",
    "--text-secondary-rgb": "158 158 184",
    "--text-muted-rgb": "138 138 170",
    "--accent-rgb": "0 163 165",
    "--accent-hi-rgb": "45 212 191",
    "--cta-rgb": "240 125 40",
    "--working-rgb": "255 207 23",
    "--success-rgb": "108 184 40",
    "--warning-rgb": "229 161 63",
    "--error-rgb": "236 104 92",
    "--shadow-rgb": "0 0 0",
    "--space-1": "3px",
    "--space-2": "6px",
    "--space-3": "9px",
    "--space-4": "12px",
    "--space-6": "18px",
    "--space-8": "24px",
    "--space-12": "36px",
    "--sp-2": "2px",
    "--sp-3": "2px",
    "--sp-4": "3px",
    "--sp-5": "4px",
    "--sp-6": "5px",
    "--sp-7": "5px",
    "--sp-8": "6px",
    "--sp-10": "8px",
    "--sp-12": "9px",
    "--sp-14": "10px",
    "--sp-16": "12px",
    "--sp-24": "18px",
    "--radius": "3px",
    "--radius-lg": "6px",
    "--radius-6": "4px",
    "--radius-10": "8px",
    "--radius-12": "9px",
    "--radius-20": "16px",
    "--fs-11": "10px",
    "--fs-12": "11px",
    "--fs-13": "12px",
    "--fs-14": "13px",
    "--fs-16": "14px",
    "--fs-22": "20px",
    "--fs-36": "32px",
    "--fs-48": "42px",
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


def _block_bodies(css: str, selector: str) -> list[str]:
    """Bodies of top-level blocks matching ``selector``, braces balanced."""
    pattern = re.compile(r"(?m)^" + selector + r"\s*\{")
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
                f"unterminated block matching {selector!r} in {_BASE_CSS}"
            )
    return bodies


def _theme_block_bodies(css: str, theme_id: str) -> list[str]:
    selector = (
        r"\[\s*data-theme\s*=\s*[\"']" + re.escape(theme_id) + r"[\"']\s*\]"
    )
    return _block_bodies(css, selector)


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
_DENSE_BODIES = _theme_block_bodies(_CSS_TEXT, "dense")
_LIT_BODIES = _theme_block_bodies(_CSS_TEXT, "lit-workbench")
_ROOT_BODIES = _block_bodies(_CSS_TEXT, r":root")


def _only_dense_body() -> str:
    assert len(_DENSE_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="dense"] block in '
        f"{_BASE_CSS}, found {len(_DENSE_BODIES)}"
    )
    return _DENSE_BODIES[0]


def _only_lit_body() -> str:
    assert len(_LIT_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="lit-workbench"] block in '
        f"{_BASE_CSS}, found {len(_LIT_BODIES)}"
    )
    return _LIT_BODIES[0]


def _root_token_names() -> set[str]:
    assert len(_ROOT_BODIES) == 1, (
        f"expected exactly one top-level :root block in {_BASE_CSS}, "
        f"found {len(_ROOT_BODIES)}"
    )
    return set(_group_by_name(_ROOT_BODIES[0]))


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


def _dense_token_map() -> dict[str, str]:
    grouped = _group_by_name(_only_dense_body())
    return {name: values[0] for name, values in grouped.items()}


def test_dense_block_declared_exactly_once() -> None:
    assert len(_DENSE_BODIES) == 1, (
        f'expected exactly one top-level [data-theme="dense"] block in '
        f"{_BASE_CSS}, found {len(_DENSE_BODIES)}"
    )


def test_dense_block_has_exactly_the_required_token_values() -> None:
    grouped = _group_by_name(_only_dense_body())
    assert set(grouped) == set(DENSE_TOKENS), (
        f"dense token set mismatch: missing "
        f"{sorted(set(DENSE_TOKENS) - set(grouped))}, "
        f"unexpected {sorted(set(grouped) - set(DENSE_TOKENS))}"
    )
    assert len(DENSE_TOKENS) == 50, (
        f"the dense contract is 50 values; test table has {len(DENSE_TOKENS)}"
    )
    wrong = {
        name: values
        for name, values in grouped.items()
        if values != [DENSE_TOKENS[name]]
    }
    assert not wrong, (
        f"dense token(s) must be declared exactly once with the required value; "
        f"got {wrong!r}"
    )


def test_dense_rgb_token_set_equals_lit_workbench_rgb_token_set() -> None:
    dense_names = set(_group_by_name(_only_dense_body()))
    lit_names = set(_group_by_name(_only_lit_body()))
    dense_rgb = {name for name in dense_names if name.endswith("-rgb")}
    lit_rgb = {name for name in lit_names if name.endswith("-rgb")}
    assert dense_rgb == lit_rgb, (
        f"dense and lit-workbench must declare the same *-rgb token set; "
        f"dense-only {sorted(dense_rgb - lit_rgb)}, "
        f"lit-only {sorted(lit_rgb - dense_rgb)}"
    )


def test_dense_non_colour_tokens_are_declared_in_root() -> None:
    dense_names = set(_group_by_name(_only_dense_body()))
    non_colour = {name for name in dense_names if not name.endswith("-rgb")}
    root_names = _root_token_names()
    undeclared = sorted(non_colour - root_names)
    assert not undeclared, (
        f"dense overrides non-colour token(s) that :root never declares: "
        f"{undeclared!r}; add them to :root or drop them from the dense block"
    )


def test_dense_is_registered() -> None:
    ids = _read_registry_ids()
    assert ids, f"No theme ids found in {_INDEX_HTML}"
    assert ids.count("dense") == 1, (
        f"dense must be registered exactly once; got {ids!r}"
    )


def test_cyberpunk_console_is_still_first_in_registry() -> None:
    ids = _read_registry_ids()
    assert ids, f"No theme ids found in {_INDEX_HTML}"
    assert ids[0] == "cyberpunk-console", (
        f"cyberpunk-console must stay the first (default) registry entry; got {ids!r}"
    )


@pytest.mark.parametrize("foreground", _FOREGROUNDS)
@pytest.mark.parametrize("background", _BACKGROUNDS)
def test_dense_colour_contrast_meets_aa_on_every_background(
    foreground: str, background: str
) -> None:
    tokens = _dense_token_map()
    fg = _rgb_triplet(foreground, tokens[foreground])
    bg = _rgb_triplet(background, tokens[background])
    ratio = _contrast_ratio(fg, bg)
    assert ratio >= 4.5, (
        f"{foreground} {tokens[foreground]} on {background} {tokens[background]} "
        f"is {ratio:.2f}:1, below the WCAG AA 4.5:1 minimum"
    )
