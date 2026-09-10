/**
 * Extension seam: chat_pane.js always renders a message body through this
 * registry instead of switching on block type itself. Adding Artifacts later
 * is `registry.register('artifact', renderArtifactBlock)` + a new panel —
 * no changes needed in chat_pane.js.
 */

import { renderMarkdown } from "./markdown.js";

const renderers = new Map();

export function register(blockType, rendererFn) {
  renderers.set(blockType, rendererFn);
}

export function render(blockType, content) {
  const fn = renderers.get(blockType) || renderers.get("text");
  return fn(content);
}

register("text", renderMarkdown);
register("thinking", (content) => `<span class="thinking">${renderMarkdown(content)}</span>`);
