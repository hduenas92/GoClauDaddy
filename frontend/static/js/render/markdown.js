/**
 * Real markdown + syntax highlighting via vendored marked.js + highlight.js
 * (loaded as classic <script> globals in index.html — UMD builds, no bundler).
 *
 * marked.js does NOT sanitize HTML by default — a literal `<script>` in the
 * source text passes straight through. Since this renders both the user's own
 * typed text and the model's output, raw `<`/`>`/`&` are escaped BEFORE
 * handing the string to marked, so nothing in the source can inject an
 * element; the HTML marked itself generates from markdown syntax (bold,
 * links, code fences, ...) is unaffected, since none of that syntax needs a
 * literal angle bracket.
 */

let configured = false;

function configureOnce() {
  if (configured) return;
  configured = true;
  window.marked.setOptions({ breaks: true, gfm: true });
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function renderMarkdown(text) {
  if (!text) return "";
  configureOnce();
  return window.marked.parse(escapeHtml(text));
}

/** Call after inserting rendered HTML into the DOM to apply syntax highlighting to any <pre><code> blocks. */
export function highlightCodeBlocks(container) {
  container.querySelectorAll("pre code").forEach((block) => {
    window.hljs.highlightElement(block);
  });
}
