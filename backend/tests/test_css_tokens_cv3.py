"""THEMECV3: guard the token refactor inside the chat-area/composer region.

The region runs from the ``CHAT AREA`` comment line down to (but excluding) the
``COMPOSER — floating dock`` comment line — the floating dock at EOF. Inside it
every mapped bare literal must have been replaced by the matching :root token:
spacing px -> --sp-N, border-radius px/% -> --radius* / --radius-full, and
font-size px -> --fs-N.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "frontend" / "static" / "css" / "base.css"

START_MARKER = "CHAT AREA"
END_MARKER = "COMPOSER — floating dock"

SPACING_PX = {2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 24}
SPACING_PROPS = {"padding", "margin", "gap", "top", "right", "bottom", "left"}
SPACING_PREFIXES = ("padding-", "margin-", "gap-")

_RADIUS_VALUE = re.compile(r"\dpx|%")
_DECLARATION = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+)")
_SPACING_COMPONENT = re.compile(r"^(\d+)px$")


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _region(text):
    """Lines from the ``CHAT AREA`` marker up to the ``COMPOSER`` marker."""
    lines = text.splitlines()
    start = next(
        index for index, line in enumerate(lines)
        if line.strip().startswith(START_MARKER)
    )
    end = next(
        index for index, line in enumerate(lines)
        if index > start and line.strip().startswith(END_MARKER)
    )
    return "\n".join(lines[start:end])


def _is_spacing_property(prop):
    return prop in SPACING_PROPS or prop.startswith(SPACING_PREFIXES)


def _offenders(text):
    """Bare literals still present in the region, grouped by category."""
    body = _strip_comments(_region(text))
    spacing, radius, font_size = [], [], []
    for prop, value in _DECLARATION.findall(body):
        prop = prop.lower()
        value = value.strip()
        if _is_spacing_property(prop):
            for component in value.split():
                match = _SPACING_COMPONENT.match(component)
                if match and int(match.group(1)) in SPACING_PX:
                    spacing.append(f"{prop}: {value}")
                    break
        if prop == "border-radius" and _RADIUS_VALUE.search(value):
            radius.append(f"{prop}: {value}")
        if prop == "font-size" and "px" in value:
            font_size.append(f"{prop}: {value}")
    return {"spacing": spacing, "radius": radius, "font-size": font_size}


def test_no_bare_spacing_px_in_region():
    found = _offenders(CSS.read_text(encoding="utf-8"))["spacing"]
    assert not found, "bare spacing px left in the COMPOSER-region:\n" + "\n".join(found)


def test_no_bare_radius_px_or_percent_in_region():
    found = _offenders(CSS.read_text(encoding="utf-8"))["radius"]
    assert not found, "bare border-radius px/% left in the COMPOSER-region:\n" + "\n".join(found)


def test_no_bare_font_size_px_in_region():
    found = _offenders(CSS.read_text(encoding="utf-8"))["font-size"]
    assert not found, "bare font-size px left in the COMPOSER-region:\n" + "\n".join(found)
