"""Copy-pass amendments (R10c, Houston 2026-10-01): one 404 text for a deleted conversation, the CLI's own
error text logged redacted, server error text shown in chat, one sentence-ending helper, a11y name, risk-level guard."""
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.stream_parser import parse_line

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "frontend" / "static" / "js"
GONE = "This conversation no longer exists. Start a new chat."
GENERIC_CLI = "Claude reported an error. Try again; if it keeps failing, open the LOG panel."
FAKE_SECRET = "sk-" + "ant-" + "x" * 24  # built at runtime: shaped like a key so the sk- redaction rule fires


def src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# --- A53: every conversation 404 says the same thing (uploads keep their own approved text) ---------------
@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/conversations/nope", None),
    ("patch", "/api/conversations/nope/rename", {"name": "x"}),
    ("patch", "/api/conversations/nope/settings", {"model": None}),
    ("post", "/api/conversations/nope/auto-title", None),
    ("get", "/api/conversations/nope/export", None),
    ("delete", "/api/conversations/nope", None),
    ("get", "/api/conversations/nope/stats", None),
])
def test_conversation_404_text(temp_db, method, path, body):
    client = TestClient(app, raise_server_exceptions=False)
    r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 404, (r.status_code, r.text)
    assert r.json()["detail"] == GONE


def test_no_old_404_text_left_and_upload_text_kept():
    backend = ROOT / "backend" / "app"
    hits = [p.name for p in backend.rglob("*.py") if "Conversation not found" in p.read_text(encoding="utf-8")]
    assert hits == []
    assert "start a new chat and attach the file again." in src("backend/app/routers/attachments.py")


# --- F1: the CLI's own error text is logged (redacted); the UI shows server text, generic only as fallback ---
def test_cli_error_result_is_logged_redacted(caplog):
    line = json.dumps({"type": "result", "is_error": True, "result": f"Prompt is too long {FAKE_SECRET}"})
    with caplog.at_level(logging.WARNING):
        out = parse_line(line)
    assert out == [{"type": "error", "error": GENERIC_CLI}]
    warn = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Prompt is too long" in m for m in warn), warn
    assert not any(FAKE_SECRET in m for m in warn) and any("[REDACTED]" in m for m in warn), warn


def test_chat_pane_shows_server_error_text():
    s = src("frontend/static/js/ui/chat_pane.js")
    assert re.search(r'msg\s*=\s*ev\.error\s*\|\|\s*"Claude reported an error\. Try again; if it keeps failing, '
                     r'open the LOG panel for details\."', s)
    assert 'msg = "Claude reported an error.' not in s


# --- F2 + F3a: one sentence-ending helper; attach toast keeps its size hint; no ".." ------------------------
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_end_sentence_helper():
    mod = (JS / "ui" / "sentence.js").as_uri()
    cases = ["Disk full", "Too big.", "Why?", "Stop!", "", "trailing space "]
    script = f"import {{ endSentence }} from {json.dumps(mod)}; console.log(JSON.stringify({json.dumps(cases)}.map(endSentence)));"
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == ["Disk full.", "Too big.", "Why?", "Stop!", "", "trailing space."]


def test_call_sites_use_the_helper():
    composer = src("frontend/static/js/ui/composer.js")
    assert "Allowed types" not in composer
    assert "${endSentence(msg)}${sizeHint}" in composer
    assert "const dot = " not in composer
    main = src("frontend/static/js/main.js")
    assert re.search(r"Couldn't start GoClaudaddy: \$\{_esc\(endSentence\(", main)
    assert "}. Restart the app" not in main
    side = src("frontend/static/js/ui/sidebar_conversations.js")
    assert re.search(r"Search didn't run: \$\{endSentence\(", side)
    assert "}. Try different words" not in side
    search = src("backend/app/routers/search.py")
    assert '"Search needs at least one word or number."' in search
    assert "Try different search terms" not in search


# --- F3b: accessible name = visible label; F3c: risk level never throws ------------------------------------
def test_feat_assess_has_no_conflicting_aria_label():
    s = src("frontend/static/js/ui/settings_panel.js")
    tag = re.search(r'<input[^>]*id="feat-assess"[^>]*>', s)[0]
    assert "aria-label" not in tag


def test_assess_level_is_guarded():
    s = src("frontend/static/js/ui/composer.js")
    assert re.search(r'typeof level === "string"', s)
    assert "${level[0].toUpperCase()" not in s
