"""Syntax-check every .js file in the frontend using `node --check`.

Catches template-literal escaping errors, invalid syntax, and bad Unicode
escapes before the app is ever opened in a browser — the kind of issue that
produces a completely blank page with no backend error to grep for.

Requires Node.js on PATH. Skipped automatically if node is not available
so CI environments without Node don't fail on an unrelated gate.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FRONTEND_JS = Path(__file__).resolve().parents[2] / "frontend" / "static" / "js"


def _js_files():
    return sorted(FRONTEND_JS.rglob("*.js"))


@pytest.fixture(scope="module")
def node_bin():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not found on PATH — skipping frontend syntax checks")
    return node


@pytest.mark.parametrize("js_file", _js_files(), ids=lambda p: p.relative_to(FRONTEND_JS).as_posix())
def test_js_syntax(js_file, node_bin):
    """Each JS file must parse without syntax errors under node --check."""
    # node --check requires the file to have a .js / .mjs extension; a temp
    # file works fine here since we only care about parse errors, not imports.
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False, mode="w", encoding="utf-8") as f:
        f.write(js_file.read_text(encoding="utf-8"))
        tmp = f.name

    result = subprocess.run([node_bin, "--check", tmp], capture_output=True, text=True)
    Path(tmp).unlink(missing_ok=True)

    assert result.returncode == 0, (
        f"Syntax error in {js_file.relative_to(FRONTEND_JS)}:\n{result.stderr}"
    )
