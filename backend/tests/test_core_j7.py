r"""J7 oracle UI wrapper: Simple/Advanced mode, panel registry, Settings popup card (SPEC.md section C). Frontend only.

UI half shells out to the J7 Playwright harness
(``plans\swarm\oraclesUI\core_j7_ui.mjs``) against an isolated uvicorn on a
random port with a temp HOME (never :8765). The J7 harness runs no turns, so
no WebSocket is mocked and no CLI is spawned; HTTP stays the real isolated app.

One parametrized ``test_j7_ui`` per UI id (74 ids in ids_ui.txt). No backend
tests: J7 is frontend only.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

# ---------------------------------------------------------------------------
# Frozen constants (SPEC.md section C4)
# ---------------------------------------------------------------------------

MJS = r"C:\Users\hduenas\LLMs\Claude\Projects\plans\swarm\oraclesUI\core_j7_ui.mjs"

# Exactly the 74 UI ids, in ids_ui.txt order.
UI_CHECKS = [
    "instrument_selftest",
    "simple_keeps_everyday",
    "chip_simple_two_options",
    "chip_advanced_six_options",
    "theme_change_immediate",
    "font_size_immediate",
    "card_reflects_active_conv",
    "simple_no_dev_words_menus",
    "mode_default_simple",
    "mode_corrupt_simple",
    "simple_hides_project",
    "simple_hides_server",
    "simple_hides_log",
    "simple_hides_tools",
    "simple_hides_mcp",
    "simple_hides_plugins",
    "simple_hides_skills",
    "simple_hides_slash",
    "simple_hides_agents",
    "simple_this_chat_title",
    "simple_session_no_dev_rows",
    "advanced_shows_all",
    "registry_module_loaded",
    "toggle_in_card",
    "toggle_target_size",
    "toggle_on_reveals",
    "toggle_off_hides",
    "mode_survives_reload",
    "mode_survives_chat_switch",
    "chip_follows_toggle",
    "leave_advanced_coerces",
    "drawer_gone",
    "card_opens_click",
    "card_btn_haspopup",
    "card_focus_inside",
    "card_focus_trap",
    "card_escape_closes",
    "card_close_button",
    "card_ctrl_comma",
    "card_simple_controls",
    "card_simple_hides_dev",
    "card_advanced_controls",
    "card_no_clear_button",
    "card_fits_viewport",
    "prompt_consequence_copy",
    "prompt_no_write_before_save",
    "prompt_save_writes",
    "allowance_no_write_before_save",
    "allowance_save_writes",
    "save_noop_no_write",
    "prompt_close_discards",
    "prompt_reset_default",
    "allowance_reset_default",
    "allowance_invalid_refused",
    "simple_no_dev_words_page",
    "simple_no_dev_words_card",
    "card_contrast_calm_dark",
    "card_contrast_calm_light",
    "card_contrast_dense_dark",
    "card_contrast_dense_light",
    "card_contrast_warm_dark",
    "card_contrast_warm_light",
    "card_contrast_bold_dark",
    "card_contrast_bold_light",
    "card_focus_ring_calm_dark",
    "card_focus_ring_calm_light",
    "card_focus_ring_dense_dark",
    "card_focus_ring_dense_light",
    "card_focus_ring_warm_dark",
    "card_focus_ring_warm_light",
    "card_focus_ring_bold_dark",
    "card_focus_ring_bold_light",
    "no_new_hosts",
    "no_page_errors",
]
assert len(UI_CHECKS) == 74 and len(set(UI_CHECKS)) == 74, (
    f"UI_CHECKS must be 74 unique ids, got {len(UI_CHECKS)}/{len(set(UI_CHECKS))}"
)


# ===========================================================================
# UI half: isolated server on a random port, temp HOME, never :8765
# ===========================================================================


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    assert shutil.which("node"), "node is required for the J7 UI oracle"
    tmp = tmp_path_factory.mktemp("j7home")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert port != 8765, "refusing to run against the live app on :8765"
    env = dict(os.environ, USERPROFILE=str(tmp), HOME=str(tmp))
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
        result = subprocess.run(
            ["node", MJS],
            env=dict(env, GCA_URL=url),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
    lines = {}
    for ln in result.stdout.splitlines():
        m = re.match(r"^(PASS|FAIL) ([\w-]+)", ln)
        if m:
            lines.setdefault(m.group(2), (m.group(1), ln))
    return lines, result.stdout[-3000:] + result.stderr[-1500:]


@pytest.mark.parametrize("check", UI_CHECKS)
def test_j7_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
