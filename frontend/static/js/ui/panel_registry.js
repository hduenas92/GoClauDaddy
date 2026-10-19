/**
 * J7: the Simple/Advanced view preference and the ONE list of right-rail panels.
 *
 * `gca_ui_mode` is the single key (J5 pin). Only the exact string "advanced"
 * means Advanced; anything else — absent, "", "ADVANCED", "1" — means Simple.
 * It is read and written through state/storage.js like every other UI pref.
 *
 * The mode lives on <html> as data-ui-mode="simple" | "advanced" and the
 * hiding is ONE CSS rule (base.css, SETTINGS DRAWER region):
 *
 *   html[data-ui-mode="simple"] [data-ui-advanced] { display: none !important; }
 *
 * A CSS rule rather than the `hidden` attribute on purpose: panels and controls
 * here already carry author `display` rules, and those beat `hidden`, so the
 * attribute alone would leave the Advanced-only controls on screen.
 */
import * as storage from "../state/storage.js";

/** localStorage key for the Simple/Advanced view preference. */
export const UI_MODE_KEY = "gca_ui_mode";

/** The right-rail panels in today's order. `simple: true` = also shown in Simple. */
export const PANELS = [
  { id: "rsb-panel-session", simple: true },
  { id: "rsb-panel-month", simple: true },
  { id: "rsb-panel-project", simple: false },
  { id: "rsb-panel-server", simple: false },
  { id: "rsb-panel-log", simple: false },
  { id: "rsb-panel-tools", simple: false },
  { id: "rsb-panel-mcp", simple: false },
  { id: "rsb-panel-plugins", simple: false },
  { id: "rsb-panel-skills", simple: false },
  { id: "rsb-panel-slash", simple: false },
  { id: "rsb-panel-agents", simple: false },
];

/** The stored mode: exactly "advanced" is Advanced, everything else is Simple. */
export function uiMode() {
  return storage.getItem(UI_MODE_KEY) === "advanced" ? "advanced" : "simple";
}

/**
 * Mark every Advanced-only panel with data-ui-advanced and title the SESSION
 * panel for the current mode ("THIS CHAT" in Simple, "SESSION" in Advanced).
 *
 * `root` is `document` at boot and on every toggle; any node with querySelector
 * works, so the function can also be run on a detached fragment.
 */
export function applyPanels(root) {
  const scope = root && typeof root.querySelector === "function" ? root : document;
  const advanced = uiMode() === "advanced";
  PANELS.forEach(({ id, simple }) => {
    const panel = scope.querySelector(`#${id}`);
    if (!panel) return;
    if (simple) panel.removeAttribute("data-ui-advanced");
    else panel.setAttribute("data-ui-advanced", "");
  });
  const title = scope.querySelector("#rsb-panel-session .rsb-panel-title");
  if (title) title.textContent = advanced ? "SESSION" : "THIS CHAT";
}

/**
 * Persist `mode`, apply it to <html> and the panels at once, and tell the rest
 * of the app (the composer's Simple coercion listens for the event). No reload.
 */
export function setUiMode(mode) {
  const next = mode === "advanced" ? "advanced" : "simple";
  storage.setItem(UI_MODE_KEY, next);
  document.documentElement.dataset.uiMode = next;
  applyPanels(document);
  window.dispatchEvent(new CustomEvent("gca:ui-mode", { detail: { mode: next } }));
}
