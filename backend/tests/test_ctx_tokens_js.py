"""CTX% counts cache reads/writes, not just fresh input (QA unknown #1, 2026-10-01): runs the real
frontend/static/js/state/ctx_tokens.js under Node, and pins that right_sidebar.js uses it on both paths."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "frontend" / "static" / "js" / "state" / "ctx_tokens.js"

CASES = [
    ({"input_tokens": 100, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 900}, 51000),  # live
    ({"input_tokens": 100, "cache_read_tokens": 50000, "cache_creation_tokens": 900}, 51000),  # stored message
    ({"input_tokens": 7}, 7),
    ({}, 0),
    (None, 0),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_context_tokens_sums_cache():
    script = (f"import {{ contextTokens }} from {json.dumps(MODULE.as_uri())};"
              f"console.log(JSON.stringify({json.dumps([c for c, _ in CASES])}.map(contextTokens)));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [want for _, want in CASES]


def test_right_sidebar_uses_it_on_both_paths():
    src = (ROOT / "frontend" / "static" / "js" / "ui" / "right_sidebar.js").read_text(encoding="utf-8")
    assert "updateCtx(latestWithCtx.input_tokens)" not in src and "updateCtx(inp)" not in src
    assert src.count("contextTokens(") >= 3  # history find + history value + live usage
