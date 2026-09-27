"""5-2 — the two source-level XSS invariants that a DOM probe cannot assert.

Most of 5-2's XSS work is proved in the browser by tools/xss-check.mjs, which
drives a hostile payload through a real render and asserts on execution. That
is the right instrument when there is an input to drive. These two properties
have no input to drive, so they are pinned here instead — and the distinction
matters, because a source-level assertion is weaker evidence and should only be
used where a behavioural one is impossible.

1. `finishAssistantMessage(status, ...)` — chat_pane.js:480 renders
   `${STATUS_LABEL[status] || status}`, and that `|| status` fallback puts an
   unrecognised status into innerHTML raw. It looked like a live XSS candidate
   for two sessions. It is not, and the reason is not that the sink escapes —
   it does not. It is that `status` is a PARAMETER and all five call sites pass
   a string literal ("done" / "error" / "stopped" / "timeout"), every one of
   which is a key in STATUS_LABEL. The fallback is unreachable with any value a
   user or a server could influence.

   A browser probe cannot demonstrate this. There is nothing to inject: the
   value does not come from the websocket event, it is chosen by the handler.
   I was about to build a routeWebSocket harness to drive a hostile status
   through the socket — elaborate machinery that would have reported PASS for a
   reason having nothing to do with what it claimed to test. The honest
   assertion is the one below: the callers pass literals, and a refactor that
   forwards `ev.status` instead is what would make the sink live.

2. `renderCard` in template_picker.js — this one WAS a live XSS, found by the
   browser probe and fixed in the same commit as this file. The payload fired
   FIVE times from one template, because `t.id` and `t.category` are
   interpolated at five points. `t.category` is typed by the user in the
   template form and stored, so this was stored XSS, not reflected. The probe
   proves the fix today; this pins the shape so a sixth interpolation added
   later cannot quietly arrive unescaped.
"""
import pathlib
import re

import pytest

JS = (pathlib.Path(__file__).resolve().parents[2]
      / "frontend" / "static" / "js")

CALL = re.compile(r'finishAssistantMessage\s*\(\s*([^,)]*)')
STRING_LITERAL = re.compile(r'^\s*["\'][A-Za-z_]+["\']\s*$')


def _call_sites():
    """(file, line, first-argument-source) for every call, excluding the
    definition and comments."""
    out = []
    for p in sorted(JS.rglob("*.js")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "finishAssistantMessage" not in line:
                continue
            s = line.strip()
            if s.startswith("//") or s.startswith("*") or s.startswith("function "):
                continue
            m = CALL.search(line)
            if m:
                out.append((p.name, i, m.group(1)))
    return out


def test_finish_assistant_message_is_only_ever_called_with_a_literal_status():
    sites = _call_sites()

    # NON-EMPTY-SET GUARD, first. "every call passes a literal" is vacuously
    # true of zero calls, and zero calls is also what a rename or a moved file
    # looks like from in here. That failure mode has cost this project a
    # session before, so it fails loudly instead of passing quietly.
    assert sites, (
        "no finishAssistantMessage call sites found — this test would pass "
        "having checked nothing. If the function moved or was renamed, move "
        "this test with it."
    )

    varying = [f"{f}:{i} -> {arg.strip()!r}"
               for f, i, arg in sites if not STRING_LITERAL.match(arg)]
    assert not varying, (
        "finishAssistantMessage is called with a status that is not a string "
        f"literal: {varying}. chat_pane.js renders `STATUS_LABEL[status] || "
        "status` straight into innerHTML, so a caller-varied status makes that "
        "fallback a live XSS sink. Either keep the literals, or escape at the sink."
    )


def test_render_card_escapes_every_template_field_it_interpolates():
    """No bare `${t.<field>}` may survive in renderCard's markup.

    The fix this pins escaped five sites at once — two `t.id` in button
    attributes, one `t.id` on the card, and `t.category` twice (a class
    attribute and text). A regex is the right tool here despite its bluntness:
    the property is literally "this template string contains no unescaped field
    interpolation", which is a statement about the source text.
    """
    src = (JS / "ui" / "template_picker.js").read_text(encoding="utf-8")

    m = re.search(r'function renderCard\(t\)\s*{(.*?)\n  }', src, re.DOTALL)
    assert m, (
        "renderCard not found in template_picker.js — this test would check "
        "nothing. If it moved, move this test."
    )
    body = m.group(1)

    interpolations = re.findall(r'\$\{([^}]*)\}', body)
    assert interpolations, "renderCard interpolates nothing — the match is wrong"

    bare = [
        expr.strip() for expr in interpolations
        # A field read that is not wrapped in an escaper and is not a plain
        # boolean/ternary guard. `t.is_builtin ? ... : ""` and `isCustom ? ...`
        # choose between literals and cannot carry a payload.
        if re.search(r'\bt\.\w+', expr)
        and "escHtml" not in expr
        and "?" not in expr
    ]
    assert not bare, (
        f"renderCard interpolates a template field without escaping it: {bare}. "
        "t.category is typed by the user in the template form and stored, so an "
        "unescaped field here is stored XSS, not reflected."
    )
