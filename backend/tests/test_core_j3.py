r"""RESTORATION CORE J3 frozen oracle (Opus, 2026-10-14): theme/motion contract. Copied into backend/tests/ by oracle_run.py and run from backend/.
Static half (pure Python on frontend/index.html + static/css/base.css) + one Playwright run (plans\swarm\oraclesUI\core_j3_ui.mjs) against THIS
clone's isolated server (random port, temp HOME; never :8765). One pytest test per check id. Contract: header of core_j3_ui.mjs plus
  S1 registry   first entry = calm; calm, dense, warm, bold registered; exactly 5 non-legacy entries (4 looks + 1 special); the two legacy ids stay
                registered until WP-RT (R-J3-1: J3 only flips the default).
  S2 structural a rule keyed on a non-legacy look's [data-theme="id"] may carry only custom properties (token sets only; no structural CSS per look).
                Legacy looks are exempt until WP-RT retires them.
  S3 blocks     every non-legacy look has at least one [data-theme="id"] rule.
  S4 external   index.html and base.css reference no absolute http(s) host (links, scripts, css url(), @import).
  S5 fonts      base.css (or a linked stylesheet) has >=1 @font-face and every url() in it resolves to an existing file under frontend/ .
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

MJS = str(Path(__file__).resolve().parents[2] / "tools" / "core-j3-ui-check.mjs")
ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "frontend" / "index.html"
CSS = ROOT / "frontend" / "static" / "css" / "base.css"
LEGACY = {"cyberpunk-console", "lit-workbench"}
LOOKS = ["calm", "dense", "warm", "bold"]
MODES = ["dark", "light"]

UI_CHECKS = (
    ["instrument_selftest"]
    + [f"tokens_{l}_{m}" for l in LOOKS for m in MODES]
    + [f"mode_{l}_{m}" for l in LOOKS for m in MODES]
    + ["default_is_calm", "unknown_theme_falls_back", "mode_system_light", "mode_system_dark", "mode_default_is_system"]
    + [f"contrast_{k}_{l}" for l in LOOKS for k in ("tpcat", "hint", "text")]
    + [f"legacy_contrast_{i}" for i in sorted(LEGACY)]
    + ["overflow_code_pre", "overflow_code_frame", "overflow_tool_input", "overflow_tool_result", "overflow_page"]
    + [f"reduced_motion_{l}" for l in LOOKS]
    + ["fonts_local", "no_external_requests"]
)


def _registry():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"window\.GCA_THEMES\s*=\s*\[(.*?)\];", html, re.DOTALL)
    assert m, "window.GCA_THEMES not found in index.html"
    return [x.group(2) for x in re.finditer(r"""\bid\s*:\s*(['"])(.+?)\1""", m.group(1))]


def _rules(css):
    """Yield (selector, body) for every style rule at any @media/@supports depth."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    out, i, n = [], 0, len(css)

    def block(start):  # returns index after the matching }
        depth, j = 1, start
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        return j

    def walk(a, b):
        i = a
        while i < b:
            k = css.find("{", i, b)
            if k < 0:
                return
            head = css[i:k].strip()
            e = block(k + 1)
            body = css[k + 1:e - 1]
            if head.startswith("@media") or head.startswith("@supports") or head.startswith("@layer"):
                walk(k + 1, e - 1)
            elif not head.startswith("@"):
                out.append((head, body))
            i = e

    walk(0, n)
    return out


def test_s1_registry_shape():
    ids = _registry()
    non_legacy = [i for i in ids if i not in LEGACY]
    assert ids and ids[0] == "calm", f"first registry entry must be calm, got {ids!r}"
    assert len(set(ids)) == len(ids), f"duplicate registry ids {ids!r}"
    assert all(l in ids for l in LOOKS), f"missing looks {[l for l in LOOKS if l not in ids]}"
    assert len(non_legacy) == 5, f"need 4 looks + 1 special = 5 non-legacy entries, got {non_legacy!r}"
    assert LEGACY <= set(ids), f"legacy looks stay registered until WP-RT (R-J3-1); registry {ids!r}"


_PART = re.compile(r"""^(?::root|html)?(?:\[data-(?:theme|mode)\s*=\s*["'][^"']*["']\])+$""")


def test_s2_no_structural_css_per_look():
    ids = [i for i in _registry() if i not in LEGACY]
    assert ids, "no registry ids"
    bad = []
    for sel, body in _rules(CSS.read_text(encoding="utf-8")):
        parts = [p.strip() for p in sel.split(",")]
        for i in ids:
            if not any(re.search(r"""\[data-theme\s*=\s*["']""" + re.escape(i) + r"""["']\]""", p) for p in parts):
                continue
            if not all(_PART.match(re.sub(r"\s+", "", p)) for p in parts):
                bad.append(f"selector `{sel[:70]}`")
                break
            decls = [d.strip() for d in body.split(";") if d.strip()]
            if any(not d.startswith("--") for d in decls):
                bad.append(f"non-token declaration in `{sel[:60]}`")
                break
    assert not bad, f"per-look rules must be token-only: {bad[:5]}"


def test_s3_every_look_has_a_block():
    ids = [i for i in _registry() if i not in LEGACY]
    css = CSS.read_text(encoding="utf-8")
    missing = [i for i in ids if not re.search(r"""\[data-theme\s*=\s*["']""" + re.escape(i) + r"""["']\]""", css)]
    assert not missing, f"no [data-theme] rule for {missing}"


def test_s4_no_external_references_static():
    html = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    refs = re.findall(r"""(?:href|src)\s*=\s*["'](https?://[^"']+)""", html)
    refs += re.findall(r"""url\(\s*["']?(https?://[^)"']+)""", css)
    refs += re.findall(r"""@import\s+(?:url\()?["']?(https?://[^)"';]+)""", css)
    refs += re.findall(r"""<link[^>]+rel\s*=\s*["']preconnect["'][^>]*href\s*=\s*["'](https?://[^"']+)""", html)
    assert not refs, f"absolute external references: {sorted(set(refs))[:6]}"


def test_s5_fonts_vendored():
    css = CSS.read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{(.*?)\}", css, flags=re.DOTALL)
    assert faces, "no @font-face in base.css: fonts are not vendored"
    missing = []
    for f in faces:
        for u in re.findall(r"""url\(\s*["']?([^)"']+)""", f):
            if u.startswith("data:"):
                continue
            p = ROOT / "frontend" / u.lstrip("/") if u.startswith("/") else CSS.parent / u
            if not p.resolve().exists():
                missing.append(u)
    assert not missing, f"@font-face urls that do not resolve to a file: {missing[:5]}"


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    if not shutil.which("node") or not (Path(__file__).resolve().parents[2] / "node_modules" / "playwright").is_dir():
        pytest.skip("node or node_modules/playwright missing: run npm install in the app root")
    tmp = tmp_path_factory.mktemp("j3home")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert port != 8765
    env = dict(os.environ, USERPROFILE=str(tmp), HOME=str(tmp))
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=os.getcwd(), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = "http://127.0.0.1:%d" % port
    try:
        for _ in range(60):
            try:
                if urllib.request.urlopen(url + "/", timeout=1).status == 200:
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("clone server did not come up on " + url)
        r = subprocess.run(["node", MJS], env=dict(env, GCA_URL=url), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
    lines = {}
    for ln in r.stdout.splitlines():
        m = re.match(r"^(PASS|FAIL) ([\w-]+)", ln)
        if m:
            lines.setdefault(m.group(2), (m.group(1), ln))
    return lines, r.stdout[-3000:] + r.stderr[-1500:]


@pytest.mark.parametrize("check", UI_CHECKS)
def test_j3_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
