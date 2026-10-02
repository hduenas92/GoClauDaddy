"""D12 #1 #2 #3 frozen oracle (Opus, 2026-10-01): render/markdown.js through the vendored marked v12.
Red on c9821fa (escape-before-parse): code double-escaped, javascript: links live, no blockquote.
Ships as backend/tests/test_markdown_safety.py."""
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "frontend" / "static" / "js"

CASES = {
    "codespan": "`a < b && c`",
    "fence": "```\nif (a < b && c) {}\n```",
    "quote": "> quoted line",
    "bold": "**bold** and *em*",
    "http": "[ok](https://example.com/a?b=1&c=2)",
    "rel": "[rel](#anchor)",
    "js": "[x](javascript:alert(1))",
    "js_case": "[x](JaVaScRiPt:alert(1))",
    "js_space": "[x]( javascript:alert(1))",
    "js_entity": "[x](javascript&#58;alert(1))",
    "vbs": "[x](vbscript:msgbox(1))",
    "data": "[x](data:text/html,<script>alert(1)</script>)",
    "img_js": "![x](javascript:alert(1))",
    "autolink": "<javascript:alert(1)>",
    "ref": "[x][r]\n\n[r]: javascript:alert(1)",
    "raw_script": "<script>alert(1)</script>",
    "raw_img": "<img src=x onerror=alert(1)>",
    "raw_inline": "hi <b onclick=alert(1)>there</b>",
    "attr_break": '[x](https://a.com" onmouseover="alert(1))',
    "title_amp": '[t](https://a.com "A & B")',
    "title_quote": "[t](https://a.com 'A \"B\"')",
    "img_title_alt": '![al&t](https://a.com/x.png "T&T")',
    "title_attr_break": "[x](https://a.com 'a\" onmouseover=\"alert(1)')",
    "lt_text": "1 < 2 & 3 > 2",
}

SCRIPT = """
import fs from "node:fs"; import vm from "node:vm";
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync(%s, "utf8"));
const { renderMarkdown } = await import(%s);
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
console.log(JSON.stringify(Object.fromEntries(Object.entries(cases).map(([k, v]) => [k, renderMarkdown(v)]))));
"""


@pytest.fixture(scope="module")
def out():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    script = SCRIPT % (json.dumps(str(JS / "vendor" / "marked.min.js")), json.dumps((JS / "render" / "markdown.js").as_uri()))
    r = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(CASES),
                       capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout)


def _code_text(h):
    m = re.search(r"<code[^>]*>(.*?)</code>", h, re.S)
    assert m, h
    return html.unescape(m.group(1))


def test_codespan_shows_source_once(out):
    assert _code_text(out["codespan"]) == "a < b && c", out["codespan"]


def test_fenced_code_shows_source_once(out):
    assert _code_text(out["fence"]).strip() == "if (a < b && c) {}", out["fence"]


def test_blockquote_renders(out):
    assert "<blockquote>" in out["quote"] and "quoted line" in out["quote"], out["quote"]


def test_plain_markdown_still_renders(out):
    assert "<strong>bold</strong>" in out["bold"] and "<em>em</em>" in out["bold"]
    assert html.unescape(out["lt_text"]).count("1 < 2 & 3 > 2") == 1 and "&amp;amp;" not in out["lt_text"]


def test_safe_links_kept(out):
    assert re.search(r'<a href="https://example\.com/a\?b=1&(amp;)?c=2"', out["http"]), out["http"]
    assert '<a href="#anchor"' in out["rel"], out["rel"]


def test_link_title_escaped_once_not_twice(out):
    """marked already escapes the title (escape$1(link.title) in outputLink)."""
    h = out["title_amp"]
    assert 'title="A &amp; B"' in h, h
    assert "&amp;amp;" not in h, h


def test_link_title_quote_escaped_once_not_twice(out):
    h = out["title_quote"]
    assert 'title="A &quot;B&quot;"' in h, h
    assert "&amp;quot;" not in h, h


def test_image_title_and_alt_escaped_once(out):
    h = out["img_title_alt"]
    assert 'title="T&amp;T"' in h, h
    assert 'alt="al&amp;t"' in h, h
    assert "&amp;amp;" not in h and "&amp;quot;" not in h, h


def test_title_attribute_break_stays_inert(out):
    """A quote in the title must not break out of the title attribute."""
    for k in ("title_attr_break", "attr_break"):
        h = out[k]
        # A live handler needs a RAW quote introducing the value; the title's
        # quote is emitted as &quot;, so `onmouseover=` stays inside the value.
        assert not re.search(r'\son\w+\s*=\s*"', h, re.I), (k, h)
        assert "&amp;quot;" not in h, (k, h)
    assert 'title="a&quot; onmouseover=&quot;alert(1)"' in out["title_attr_break"], out["title_attr_break"]


def _urls(h):
    return [re.sub(r"[\s\x00-\x1f]", "", html.unescape(u)).lower()
            for u in re.findall(r'\b(?:href|src)\s*=\s*"([^"]*)"', h)]


@pytest.mark.parametrize("k", ["js", "js_case", "js_space", "js_entity", "vbs", "data", "img_js", "autolink", "ref"])
def test_dangerous_schemes_never_become_urls(out, k):
    bad = [u for u in _urls(out[k]) if u.startswith(("javascript:", "vbscript:", "data:"))]
    assert not bad, (k, out[k])


@pytest.mark.parametrize("k", ["raw_script", "raw_img", "raw_inline", "attr_break", "data"])
def test_raw_html_is_inert_text(out, k):
    h = out[k]
    assert not re.search(r"<(script|img|b)\b", h, re.I), (k, h)
    assert not re.search(r"<[a-z][^>]*\son\w+\s*=", h, re.I), (k, h)


def test_raw_html_still_visible_as_text(out):
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out["raw_script"], out["raw_script"]


def test_no_escape_before_parse():
    src = (JS / "render" / "markdown.js").read_text(encoding="utf-8")
    assert not re.search(r"marked\.parse\(\s*escapeHtml\(", src)
