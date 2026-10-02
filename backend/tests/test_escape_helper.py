"""D12 #7 frozen oracle (Opus, 2026-10-01): one HTML escaper (render/escape.js) for every module.
Red on c9821fa: 9 local copies, two of which (sidebar_projects, sidebar_conversations) skip `"` while
feeding attribute values. Ships as backend/tests/test_escape_helper.py.
v2 2026-10-02 (R1): LOCAL_DEF requires a function value (v1 flagged `const escOverlay = ...`)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "frontend" / "static" / "js"
HELPER = JS / "render" / "escape.js"
# v2 (2026-10-02, R1): a const/let/var only counts when its value is a function (function expr or arrow);
# v1 flagged any binding starting with "esc", e.g. `const escOverlay = document.querySelector(...)` in main.js.
LOCAL_DEF = re.compile(
    r"function\s+_?esc\w*\s*\("
    r"|(?:const|let|var)\s+_?esc\w*\s*=\s*(?:async\s+)?(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")
OLD_CALL = re.compile(r"\b(_esc|_escHtml|escHtml|_escSuggest)\s*\(")
FORMER = ["main.js", "ui/chat_pane.js", "ui/composer.js", "ui/modal.js", "ui/right_sidebar.js",
          "ui/sidebar_conversations.js", "ui/sidebar_projects.js", "ui/template_picker.js"]


def _modules():
    return [p for p in JS.rglob("*.js") if "vendor" not in p.parts and p != HELPER]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_helper_escapes_text_and_attribute_metacharacters():
    script = (f"import {{ escapeHtml }} from {json.dumps(HELPER.as_uri())};"
              "console.log(JSON.stringify([escapeHtml(`&<>\"'`), escapeHtml(null), escapeHtml(undefined), escapeHtml(5)]));")
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(r.stdout) == ["&amp;&lt;&gt;&quot;&#39;", "", "", "5"]


def test_no_module_defines_its_own_escaper():
    hits = [f"{p.relative_to(JS)}" for p in _modules() if LOCAL_DEF.search(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_no_call_to_a_removed_escaper():
    hits = [f"{p.relative_to(JS)}:{m.group(0)}" for p in _modules()
            for m in OLD_CALL.finditer(p.read_text(encoding="utf-8"))]
    assert hits == []


@pytest.mark.parametrize("rel", FORMER)
def test_former_copies_import_the_helper(rel):
    src = (JS / rel).read_text(encoding="utf-8")
    assert re.search(r'import\s*\{[^}]*\bescapeHtml\b[^}]*\}\s*from\s*"[./]+(render/)?escape\.js"', src), rel
    assert "escapeHtml(" in src, rel


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("rel", FORMER)
def test_module_still_parses(rel):
    subprocess.run(["node", "--check", str(JS / rel)], check=True, capture_output=True)
