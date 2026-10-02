"""D12-5 — with "Prompt for tool approval" OFF, the tool must be DENIED, not approved.

chat_pane.js's approval_needed handler used to call socket.approve() when the
gca_feat_approval flag was off, with a comment saying "auto-approve so the
subprocess isn't left hanging". That ran an arbitrary tool on the user's behalf
without their decision. The handler now calls socket.deny() instead and says so
on the status line; deny() still answers the server, so the subprocess is not
left hanging.

Static source assertion, in the style of the other frontend-reading tests here
(test_frontend_xss_invariants.py, test_ctx_tokens_js.py): the behaviour lives in
a browser websocket handler, and there is no input to drive through it without
standing up the whole app.
"""
import re
from pathlib import Path

CHAT_PANE = (Path(__file__).resolve().parents[2]
             / "frontend" / "static" / "js" / "ui" / "chat_pane.js")

# Exactly the status line the flag-off branch must set.
EXPECTED_STATUS = """statusEl.textContent = "A tool asked for approval and was denied. Turn on 'Prompt for tool approval' in Settings to decide yourself.";"""

HANDLER = re.compile(
    r'socket\.on\("approval_needed",\s*\(ev\)\s*=>\s*\{(?P<body>.*?)\n\s*\}\)',
    re.DOTALL,
)
OFF_BRANCH = re.compile(
    r'if\s*\(\s*storage\.getItem\("gca_feat_approval"\)\s*!==\s*"1"\s*\)'
    r'\s*\{(?P<off>.*?)\n\s*\}',
    re.DOTALL,
)


def _handler_body():
    src = CHAT_PANE.read_text(encoding="utf-8")
    m = HANDLER.search(src)
    assert m, (
        "approval_needed handler not found in chat_pane.js — this test would "
        "check nothing. If it moved or was renamed, move this test with it."
    )
    return m.group("body")


def _off_branch(body):
    m = OFF_BRANCH.search(body)
    assert m, (
        'flag-off branch (gca_feat_approval !== "1") not found in the '
        "approval_needed handler — this test would check nothing."
    )
    return m.group("off")


def test_flag_off_denies_the_tool():
    off = _off_branch(_handler_body())
    assert "socket.deny()" in off, (
        "the flag-off approval branch must call socket.deny(); off-branch was:"
        f"\n{off}"
    )


def test_no_approve_anywhere_in_the_handler():
    body = _handler_body()
    assert "socket.approve(" not in body, (
        "approval_needed still auto-approves somewhere (including comments); a "
        "tool must never run without the user's decision when the prompt is off."
    )


def test_flag_off_sets_the_exact_status_copy():
    off = _off_branch(_handler_body())
    assert EXPECTED_STATUS in off, (
        f"the flag-off branch must set:\n  {EXPECTED_STATUS}\nbut off-branch was:"
        f"\n{off}"
    )


def test_flag_on_still_opens_the_modal():
    assert "_showApprovalModal(ev.tool, ev.action, socket, ev)" in _handler_body(), (
        "the flag-on path must still open the approval modal with the real tool "
        "and action unchanged."
    )
