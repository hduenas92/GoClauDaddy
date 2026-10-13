/**
 * WP-ST connection states — the single source of truth for the header pill.
 *
 * The state is GLOBAL on purpose: it describes the WebSocket/CLI connection,
 * not the open conversation, so switching conversations must not reset it.
 * Exactly one element renders it: #conn-state inside #chat-header.
 *
 * Frames that move it (see chat_pane.js / composer.js):
 *   - a message actually written to the socket        -> sending
 *   - the first text/thinking/tool_call of a turn     -> streaming
 *   - a conn_state frame                              -> that state
 *   - an error frame (four known codes)               -> that code's state
 *   - any other error frame, or a timeout frame       -> error
 *   - a stopped frame                                 -> ready
 * Nothing else changes it; the app never retries, cancels or times anything
 * out on its own because of a state.
 */

const LABELS = {
  ready: "Ready",
  sending: "Sending…",
  streaming: "Streaming…",
  connected: "Connected",
  retrying: "Retrying",
  key_invalid: "Key invalid",
  key_missing: "API key missing",
  url_missing: "Gateway address missing",
  gateway_unreachable: "Can't reach Claude",
  error: "Something went wrong",
};

// These two labels carry the CLI's own retry progress while it is still
// retrying. Once the turn is over (a plain error frame) they drop the suffix.
const ATTEMPT_STATES = new Set(["retrying", "key_invalid"]);

let _state = "ready";
let _attempt = null;
let _maxRetries = null;

/** The visible label for a state, independent of the DOM. */
export function connStateLabel(state, attempt = null, maxRetries = null) {
  const base = LABELS[state];
  if (!base) return LABELS.error;
  if (ATTEMPT_STATES.has(state) && attempt != null && maxRetries != null) {
    return `${base} (attempt ${attempt} of ${maxRetries})`;
  }
  return base;
}

function _render() {
  if (typeof document === "undefined") return;
  const el = document.getElementById("conn-state");
  if (!el) return;
  el.dataset.state = _state;
  el.textContent = connStateLabel(_state, _attempt, _maxRetries);
}

/**
 * Set the global connection state. An unknown state is ignored rather than
 * blanking the pill.
 *
 * @param {string} state          one of the LABELS keys
 * @param {{attempt?: number, max_retries?: number}} [opts] retry progress
 */
export function setConnState(state, { attempt = null, max_retries = null } = {}) {
  if (!LABELS[state]) return;
  _state = state;
  _attempt = attempt ?? null;
  _maxRetries = max_retries ?? null;
  _render();
}

/** Re-paint the pill from the current global state (called once at boot). */
export function initConnState() {
  _render();
}

export function getConnState() {
  return _state;
}
