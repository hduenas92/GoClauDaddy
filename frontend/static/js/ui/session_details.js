/**
 * Session details panel (P2-S) — a right-aligned <aside> that shows ONLY data
 * the app already has. Every field below cites its source in the report:
 *
 *   model            conversation object from GET /api/conversations/{id}
 *                    (backend/app/routers/conversations.py:87-96)
 *   message count    store.messages (frontend/static/js/state/store.js:8,
 *                    populated by selectConversation in state/actions.js:47-50)
 *   tokens in/out    GET /api/conversations/{id}/stats
 *                    (backend/app/routers/conversation_stats.py:70-79)
 *   conversation $   same endpoint, cost_usd
 *                    (backend/app/routers/conversation_stats.py:52-58,75)
 *   context %        /api/config model context_window
 *                    (backend/app/routers/config.py:9-14) + latest assistant
 *                    input_tokens, the same derivation right_sidebar.js uses
 *                    (frontend/static/js/ui/right_sidebar.js:155-179)
 *   month cost/budget GET /api/server/stats
 *                    (backend/app/routers/server.py:94-103), with the user's
 *                    localStorage budget override the sidebar already reads
 *                    (frontend/static/js/ui/right_sidebar.js:26-35,392)
 *   project + dir    GET /api/projects (backend/app/routers/projects.py:24-26,
 *                    model backend/app/models/project.py:6-12)
 *   connection       the existing #chat-status live region
 *                    (frontend/static/js/ui/chat_pane.js:15, set by the socket
 *                    handlers at chat_pane.js:750-836) — mirrored, not invented
 *
 * Updates ride the EXISTING stats-refresh events, so no new timers/pollers:
 *   - store streaming true->false (the falling edge right_sidebar.js:220-236
 *     already uses) refreshes conversation + server stats
 *   - "gca:budget-changed" (settings_panel.js:328) refreshes server stats
 *   - setConversation() is called by main.js on every conversation switch
 */

import { getState, subscribe } from "../state/store.js";
import { userBudget } from "./right_sidebar.js";

const STORAGE_KEY = "gca_session_panel_open";
const COLLAPSE_BELOW = 1280; // P2-S: below 1280px the panel starts collapsed.

const MODEL_LABELS = {
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-opus-4-5": "Opus 4.5",
  "claude-haiku-4-5-20251001": "Haiku 4.5",
};

function escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmtTok(n) {
  if (n == null || !Number.isFinite(n)) return "—";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function fmtCompact(n) {
  if (n < 1_000) return String(n);
  const useM = n >= 999_500;
  const v = n / (useM ? 1_000_000 : 1_000);
  const r = Math.round(v * 10) / 10;
  return `${r % 1 === 0 ? r : r.toFixed(1)}${useM ? "M" : "k"}`;
}

export function mountSessionDetails(root) {
  root.innerHTML = `
    <div class="sd-header">
      <h2 class="sd-title">Session details</h2>
      <button type="button" id="sd-toggle" class="sd-toggle" aria-expanded="true" aria-controls="sd-body">Hide details</button>
    </div>
    <div class="sd-body" id="sd-body">
      <div class="metric-row">
        <span class="metric-label">MODEL</span>
        <span class="metric-value" id="sd-model">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">MESSAGES</span>
        <span class="metric-value" id="sd-messages">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">TOKENS</span>
        <span class="metric-value" id="sd-tokens">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">COST</span>
        <span class="metric-value" id="sd-cost">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">CTX</span>
        <span class="metric-value" id="sd-ctx">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">MONTH</span>
        <span class="metric-value" id="sd-month">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">PROJECT</span>
        <span class="metric-value" id="sd-project">—</span>
      </div>
      <div class="metric-row" id="sd-project-dir-row">
        <span class="metric-label">WORK DIR</span>
        <span class="metric-value sd-project-dir" id="sd-project-dir">—</span>
      </div>
      <div class="metric-row" id="sd-connection-row">
        <span class="metric-label">CONNECTION</span>
        <span class="metric-value" id="sd-connection">Connected</span>
      </div>
    </div>
  `;

  const bodyEl = root.querySelector("#sd-body");
  const toggleEl = root.querySelector("#sd-toggle");
  const modelEl = root.querySelector("#sd-model");
  const messagesEl = root.querySelector("#sd-messages");
  const tokensEl = root.querySelector("#sd-tokens");
  const costEl = root.querySelector("#sd-cost");
  const ctxEl = root.querySelector("#sd-ctx");
  const monthEl = root.querySelector("#sd-month");
  const projectEl = root.querySelector("#sd-project");
  const projectDirRowEl = root.querySelector("#sd-project-dir-row");
  const projectDirEl = root.querySelector("#sd-project-dir");
  const connectionRowEl = root.querySelector("#sd-connection-row");
  const connectionEl = root.querySelector("#sd-connection");

  // --- collapse state (localStorage wrapped in try/catch) ---
  let collapsed;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "0") collapsed = true;
    else if (saved === "1") collapsed = false;
    else collapsed = window.innerWidth < COLLAPSE_BELOW;
  } catch {
    collapsed = window.innerWidth < COLLAPSE_BELOW;
  }

  function applyCollapsed() {
    toggleEl.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggleEl.textContent = collapsed ? "Show details" : "Hide details";
    bodyEl.hidden = collapsed;
    root.classList.toggle("sd-collapsed", collapsed);
  }
  applyCollapsed();

  toggleEl.addEventListener("click", () => {
    collapsed = !collapsed;
    applyCollapsed();
    try { localStorage.setItem(STORAGE_KEY, collapsed ? "0" : "1"); } catch { /* private mode */ }
  });

  // --- helpers ---
  const _getJSON = (url) =>
    fetch(url).then((r) => {
      if (!r.ok) throw new Error(`${url} ${r.status}`);
      return r.json();
    });

  let _activeConvId = null;
  let _currentModel = null;
  let _ctxByModel = {};

  function _setEmpty(el) {
    if (el) el.textContent = "—";
  }

  function _refreshCtx() {
    const msgs = getState().messages || [];
    const latest = [...msgs].reverse().find((m) => m.input_tokens > 0);
    if (!latest || !ctxEl) return;
    const max = _ctxByModel[_currentModel] ?? null;
    if (max == null) {
      // Unknown context window — show the honest empty state, never a
      // percentage against a made-up denominator (same rule as
      // right_sidebar.js:155-162).
      ctxEl.textContent = "—";
      return;
    }
    const pct = Math.min((latest.input_tokens / max) * 100, 100);
    ctxEl.textContent = `${Math.round(pct)}% · ${fmtCompact(latest.input_tokens)} / ${fmtCompact(max)}`;
  }

  function _renderMessages() {
    const msgs = getState().messages || [];
    if (messagesEl) messagesEl.textContent = msgs.length > 0 ? String(msgs.length) : "—";
  }

  function _renderProject(projectId) {
    const project = getState().projects.find((p) => p.id === projectId);
    if (!project) {
      if (projectEl) projectEl.textContent = "—";
      if (projectDirEl) projectDirEl.textContent = "—";
      return;
    }
    if (projectEl) projectEl.textContent = project.name || "—";
    if (projectDirEl) projectDirEl.textContent = project.working_dir || "—";
  }

  function _fetchConvStats() {
    if (!_activeConvId) return;
    _getJSON(`/api/conversations/${_activeConvId}/stats`)
      .then((stats) => {
        const ti = stats.tokens_in;
        const to = stats.tokens_out;
        const cost = stats.cost_usd;
        if (tokensEl) {
          tokensEl.textContent =
            (ti == null && to == null) ? "—" : `↑${fmtTok(ti)} ↓${fmtTok(to)}`;
        }
        if (costEl) costEl.textContent = cost == null ? "—" : `$${Number(cost).toFixed(2)}`;
      })
      .catch(() => {
        // P2-B style explicit unavailable state, never undefined/NaN.
        _setEmpty(tokensEl);
        _setEmpty(costEl);
      });
  }

  function _fetchServerStats() {
    _getJSON("/api/server/stats")
      .then((stats) => {
        if (!monthEl) return;
        const cost = stats.monthly_cost_usd ?? 0;
        const budget = userBudget() ?? stats.budget_usd ?? 200;
        monthEl.textContent = `~$${Number(cost).toFixed(2)} / $${Number(budget).toFixed(0)}`;
      })
      .catch(() => _setEmpty(monthEl));
  }

  function _fetchConfig() {
    _getJSON("/api/config")
      .then((cfg) => {
        (cfg.models || []).forEach((m) => { _ctxByModel[m.value] = m.context_window; });
        _refreshCtx();
      })
      .catch(() => { /* config unavailable — CTX stays "—" */ });
  }

  // --- conversation change (called from main.js) ---
  function setConversation(conversation) {
    _activeConvId = conversation.id;
    _currentModel = conversation.model;
    if (modelEl) modelEl.textContent = MODEL_LABELS[conversation.model] ?? conversation.model ?? "—";
    _renderMessages();
    _renderProject(conversation.project_id);
    _fetchConvStats();
    _refreshCtx();
  }

  // Model can change from the settings drawer without a conversation switch.
  function setModel(model) {
    _currentModel = model;
    if (modelEl) modelEl.textContent = MODEL_LABELS[model] ?? model ?? "—";
    _refreshCtx();
  }

  // --- connection status mirrors the existing #chat-status live region ---
  const chatStatusEl = document.getElementById("chat-status");
  function syncConnection() {
    if (!connectionEl) return;
    const text = (chatStatusEl?.textContent ?? "").trim();
    connectionEl.textContent = text || "Connected";
    connectionRowEl?.classList.toggle("sd-connection-off", !!text);
  }
  if (chatStatusEl) {
    syncConnection();
    new MutationObserver(syncConnection).observe(chatStatusEl, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  }

  // --- existing stats-refresh events, reused (no new timers) ---
  let prevStreaming = getState().streaming;
  subscribe((state) => {
    _renderMessages();
    _refreshCtx();
    if (state.streaming && !prevStreaming) {
      // turn started: nothing to refresh yet (bills nothing)
    } else if (!state.streaming && prevStreaming) {
      // turn ended: the same falling edge right_sidebar.js uses
      _fetchConvStats();
      _fetchServerStats();
    }
    prevStreaming = state.streaming;
  });

  window.addEventListener("gca:budget-changed", () => _fetchServerStats());

  // Initial load.
  _fetchConfig();
  _fetchServerStats();
  _refreshCtx();
  _renderMessages();

  return { setConversation, setModel };
}
