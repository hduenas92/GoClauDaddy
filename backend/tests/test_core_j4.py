r"""RESTORATION CORE J4 frozen oracle (theme looks): calm/dense/warm/bold picker, frozen barry-simpson tokens, dense-not-lit-workbench.
Copied into backend/tests/ by oracle_run.py and run from backend/. One Playwright run (plans\swarm\oraclesUI\core_j4_ui.mjs) against THIS
clone's isolated server (random port, temp HOME; never :8765). One pytest test per check id, 83 ids. Registry hygiene only: J4 must not
remove registry entries (S1/S2/S3 are J3's; WP-RT owns retiring the legacy looks).
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

MJS = r"C:\Users\hduenas\LLMs\Claude\Projects\plans\swarm\oraclesUI\core_j4_ui.j7.mjs"
ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "frontend" / "index.html"
LOOKS = ["calm", "dense", "warm", "bold"]
MODES = ["dark", "light"]

UI_CHECKS = (
    ["instrument_selftest"]
    + [f"{k}_{l}_{m}" for l in LOOKS for m in MODES
       for k in ("dashed", "glow", "infinite_anim", "mono_caps", "text_floor", "contrast_thread", "contrast_drawer")]
    + [f"msgs_visible_{l}_{w}" for l in LOOKS for w in (1280, 1440)]
    + [f"{k}_{l}" for l in LOOKS for k in ("body_min_13", "tp_lock")]
    + ["picker_lists_only_looks", "picker_saved_legacy_selected", "picker_saved_barry_selected",
       "barry_tokens_frozen_dark", "barry_tokens_frozen_light",
       "dense_palette_not_lit_workbench", "dense_denser_than_calm", "browse_all_link_dense",
       "palette_distinct_dark", "palette_distinct_light"]
)
assert len(UI_CHECKS) == 83 and len(set(UI_CHECKS)) == 83, f"UI_CHECKS must be 83 unique ids, got {len(UI_CHECKS)}/{len(set(UI_CHECKS))}"


def _registry():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"window\.GCA_THEMES\s*=\s*\[(.*?)\];", html, re.DOTALL)
    assert m, "window.GCA_THEMES not found in index.html"
    return [x.group(2) for x in re.finditer(r"""\bid\s*:\s*(['"])(.+?)\1""", m.group(1))]


def test_registry_unchanged_by_j4():
    assert _registry() == ['calm', 'dense', 'warm', 'bold', 'barry-simpson', 'cyberpunk-console', 'lit-workbench'], \
        f"picker hygiene must not remove registry entries (WP-RT owns that), got {_registry()!r}"


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    assert shutil.which("node"), "node is required for the J4 UI oracle"
    tmp = tmp_path_factory.mktemp("j4home")
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
                           encoding="utf-8", errors="replace", timeout=900)
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
def test_j4_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
