/**
 * Real markdown + syntax highlighting via vendored marked.js + highlight.js
 * (loaded as classic <script> globals in index.html — UMD builds, no bundler).
 *
 * marked.js does NOT sanitize HTML, and v12's default link/image renderers
 * accept any scheme (`cleanUrl` only wraps encodeURI). So we hand marked the
 * RAW source — code spans/fences keep their characters exactly once and `>`
 * still starts a blockquote — and take over three renderer hooks:
 *   - html(): escape raw HTML from the source, block and inline, so nothing in
 *     the source becomes an element or an on*= handler;
 *   - link()/image(): keep only http/https/mailto or scheme-less URLs, decided
 *     after HTML-entity decoding and stripping whitespace/control characters.
 *
 * The vendored marked is v12.0.2, whose renderer methods take POSITIONAL
 * arguments: html(rawHtml, block), link(href, title, text),
 * image(href, title, text). (v13+ passes one token object instead.)
 */

import { escapeHtml } from "./escape.js";

let configured = false;

const SAFE_SCHEMES = new Set(["http", "https", "mailto"]);
const NAMED_ENTITIES = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  colon: ":",
  tab: "\t",
  newline: "\n",
  sol: "/",
};

/** Decode the HTML character references a scheme could hide behind. */
function decodeEntities(value) {
  return String(value).replace(
    /&(?:#([0-9]+);?|#[xX]([0-9a-fA-F]+);?|([a-zA-Z][a-zA-Z0-9]*);)/g,
    (match, dec, hex, named) => {
      if (dec !== undefined) {
        const codePoint = Number(dec);
        return codePoint <= 0x10ffff ? String.fromCodePoint(codePoint) : match;
      }
      if (hex !== undefined) {
        const codePoint = parseInt(hex, 16);
        return codePoint <= 0x10ffff ? String.fromCodePoint(codePoint) : match;
      }
      const replacement = NAMED_ENTITIES[named.toLowerCase()];
      return replacement === undefined ? match : replacement;
    },
  );
}

/**
 * Return the URL to emit, or null when its scheme is not safe. The decision is
 * made on the entity-decoded, whitespace/control-stripped, lowercased form, so
 * `JaVaScRiPt:`, ` javascript:`, `javascript&#58;` and `java\tscript:` are all
 * recognised. The value emitted keeps its original case.
 */
function safeUrl(raw) {
  if (raw === null || raw === undefined) return null;
  const decoded = decodeEntities(String(raw));
  const normalized = decoded.replace(/[\s\u0000-\u001f]+/g, "").toLowerCase();
  const scheme = /^([a-z][a-z0-9+.-]*):/.exec(normalized);
  if (scheme === null) return decoded;
  return SAFE_SCHEMES.has(scheme[1]) ? decoded : null;
}

function configureOnce() {
  if (configured) return;
  configured = true;
  window.marked.setOptions({ breaks: true, gfm: true });
  window.marked.use({
    renderer: {
      html(rawHtml) {
        return escapeHtml(rawHtml);
      },

      link(href, title, text) {
        const url = safeUrl(href);
        // `text` is already rendered and escaped by marked; when the URL is
        // unsafe, drop the anchor and keep only the link text.
        if (url === null) return text;
        let out = '<a href="' + escapeHtml(url) + '"';
        // marked escapes the title once already (escape$1(link.title) in
        // outputLink); escaping it again double-encodes entities.
        if (title) out += ' title="' + title + '"';
        return out + ">" + text + "</a>";
      },

      image(href, title, text) {
        const url = safeUrl(href);
        // marked escapes the alt text and title once already (escape$1(text),
        // escape$1(image.title) in outputLink); escaping again double-encodes.
        const alt = text === null || text === undefined ? "" : text;
        if (url === null) return alt;
        let out = '<img src="' + escapeHtml(url) + '" alt="' + alt + '"';
        if (title) out += ' title="' + title + '"';
        return out + ">";
      },
    },
  });
}

export function renderMarkdown(text) {
  if (!text) return "";
  configureOnce();
  return window.marked.parse(String(text));
}

/** Call after inserting rendered HTML into the DOM to apply syntax highlighting to any <pre><code> blocks. */
export function highlightCodeBlocks(container) {
  container.querySelectorAll("pre code").forEach((block) => {
    window.hljs.highlightElement(block);
  });
}
