r"""J6 oracle UI wrapper: the message queue (SPEC.md section C, WP12). Frontend only.

UI half shells out to the J6 Playwright harness
(``plans\swarm\oraclesUI\core_j6_ui.mjs``) against an isolated uvicorn on a
random port with a temp HOME (never :8765). The harness mocks the chat
WebSocket with ``page.routeWebSocket``, so the server never sees a websocket
and no CLI is spawned; HTTP stays the real isolated app.

One parametrized ``test_j6_ui`` per UI id (51 ids in ids_ui.txt). No backend
tests: J6 is frontend only.
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

MJS = r"C:\Users\hduenas\LLMs\Claude\Projects\plans\swarm\oraclesUI\core_j6_ui.mjs"

# Exactly the 51 UI ids, in ids_ui.txt order.
UI_CHECKS = [
    "instrument_selftest",
    "idle_send_unchanged",
    "draft_autosave_unchanged",
    "regenerate_never_queued",
    "enter_while_streaming_queues",
    "queue_order_one_per_done",
    "single_flight",
    "no_early_flush",
    "dispatch_keeps_typed_box",
    "dispatch_renders_bubble",
    "edit_sends_edited",
    "edit_blank_never_sent",
    "remove_never_sent",
    "empty_queue_hides_strip",
    "stop_pauses",
    "paused_ui",
    "resume_dispatches",
    "error_pauses",
    "clear_empties",
    "timeout_pauses",
    "no_auto_resume",
    "approval_holds",
    "no_drain_on_disconnect",
    "per_conversation",
    "reload_survives",
    "corrupt_key_ignored",
    "cap_ten",
    "cap_counts_persisted",
    "delete_clears_key",
    "layout_no_occlusion_1280",
    "layout_no_occlusion_1440",
    "a11y_names",
    "strip_contrast_calm_dark",
    "strip_contrast_calm_light",
    "strip_contrast_dense_dark",
    "strip_contrast_dense_light",
    "strip_contrast_warm_dark",
    "strip_contrast_warm_light",
    "strip_contrast_bold_dark",
    "strip_contrast_bold_light",
    "strip_focus_ring_calm_dark",
    "strip_focus_ring_calm_light",
    "strip_focus_ring_dense_dark",
    "strip_focus_ring_dense_light",
    "strip_focus_ring_warm_dark",
    "strip_focus_ring_warm_light",
    "strip_focus_ring_bold_dark",
    "strip_focus_ring_bold_light",
    "j5_controls_intact",
    "no_new_hosts",
    "no_page_errors",
]
assert len(UI_CHECKS) == 51 and len(set(UI_CHECKS)) == 51, (
    f"UI_CHECKS must be 51 unique ids, got {len(UI_CHECKS)}/{len(set(UI_CHECKS))}"
)


# ===========================================================================
# UI half: isolated server on a random port, temp HOME, never :8765
# ===========================================================================


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    assert shutil.which("node"), "node is required for the J6 UI oracle"
    tmp = tmp_path_factory.mktemp("j6home")
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
def test_j6_ui(ui_run, check):
    lines, tail = ui_run
    assert check in lines, f"check {check} never reported:\n{tail}"
    state, line = lines[check]
    assert state == "PASS", line.encode("ascii", "replace").decode()
