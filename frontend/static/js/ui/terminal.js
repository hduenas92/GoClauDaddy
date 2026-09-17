/**
 * Embedded terminal panel — lazy-loads xterm.js from cdnjs on first open.
 *
 * xterm.js UMD: https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.js
 * xterm CSS:    https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.css
 *
 * To vendor offline: download both files to frontend/static/js/vendor/ and
 * frontend/static/css/vendor/ respectively, then update the paths below.
 */

const XTERM_JS  = "https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.js";
const XTERM_CSS = "https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.css";

const _THEME = {
  background: "#09090d", foreground: "#f8fafc",
  cursor: "#2dd4bf", cursorAccent: "#09090d",
  selectionBackground: "rgba(45,212,191,0.25)",
  black: "#09090d",   brightBlack: "#1a192a",
  white: "#f8fafc",   brightWhite: "#ffffff",
  red: "#f43f5e",     brightRed: "#fb7185",
  green: "#34d399",   brightGreen: "#6ee7b7",
  yellow: "#fbbf24",  brightYellow: "#fde68a",
  blue: "#38bdf8",    brightBlue: "#7dd3fc",
  magenta: "#a78bfa", brightMagenta: "#c4b5fd",
  cyan: "#2dd4bf",    brightCyan: "#5eead4",
};

let _xtermLoaded = false;

async function _loadXterm() {
  if (_xtermLoaded || window.Terminal) { _xtermLoaded = true; return; }
  await new Promise((resolve, reject) => {
    if (!document.querySelector(`link[href="${XTERM_CSS}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = XTERM_CSS;
      document.head.appendChild(link);
    }
    const s = document.createElement("script");
    s.src = XTERM_JS;
    s.onload = resolve;
    s.onerror = () => reject(new Error("Could not load xterm.js — check network"));
    document.head.appendChild(s);
  });
  _xtermLoaded = true;
}

export async function openTerminal() {
  try {
    await _loadXterm();
  } catch (err) {
    alert(`Terminal error: ${err.message}`);
    return;
  }

  // Create session
  let sessionId;
  try {
    const res = await fetch("/api/terminal", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    ({ id: sessionId } = await res.json());
  } catch (err) {
    alert(`Could not start terminal: ${err.message}`);
    return;
  }

  // Dialog
  const dialog = document.createElement("dialog");
  dialog.className = "term-dialog";
  dialog.innerHTML = `
    <div class="term-header">
      <span class="term-icon">⬛</span>
      <span class="term-title">Terminal</span>
      <span class="term-hint">Ctrl+\` to toggle · Esc to close</span>
      <button class="term-close" aria-label="Close">✕</button>
    </div>
    <div class="term-body" id="term-body"></div>
  `;
  document.body.appendChild(dialog);
  dialog.showModal();

  // xterm
  const term = new window.Terminal({
    theme: _THEME,
    // Mirrors --font-shell in base.css. Fira Code dropped there for never
    // resolving; xterm needs a literal string, so it cannot read the token.
    fontFamily: "'JetBrains Mono', 'Cascadia Code', monospace",
    fontSize: 13,
    lineHeight: 1.2,
    cursorBlink: true,
    cursorStyle: "block",
    scrollback: 1000,
    cols: 80,
    rows: 24,
  });
  term.open(dialog.querySelector("#term-body"));
  term.focus();

  // WebSocket
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/terminal/${sessionId}`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    term.write("\x1b[1;36m◈ GoClaudaddy Terminal\x1b[0m  \x1b[2m(type commands, Enter to run)\x1b[0m\r\n\r\n");
  };

  ws.onmessage = (e) => {
    term.write(e.data instanceof ArrayBuffer ? new Uint8Array(e.data) : e.data);
  };

  ws.onclose = () => {
    term.write("\r\n\x1b[1;31m[disconnected]\x1b[0m\r\n");
  };

  ws.onerror = () => {
    term.write("\r\n\x1b[1;31m[WebSocket error]\x1b[0m\r\n");
  };

  // Input: echo locally + forward to server
  term.onData((data) => {
    // Local echo so characters appear as you type (pipe mode doesn't echo)
    term.write(data);
    if (ws.readyState === WebSocket.OPEN) ws.send(data);
  });

  function closeTerminal() {
    ws.close();
    term.dispose();
    dialog.remove();
  }

  dialog.querySelector(".term-close").addEventListener("click", closeTerminal);
  // dialog cancel event fires on Esc — prevent default (which would close dialog
  // without our cleanup) and run our cleanup instead
  dialog.addEventListener("cancel", (e) => { e.preventDefault(); closeTerminal(); });
}
