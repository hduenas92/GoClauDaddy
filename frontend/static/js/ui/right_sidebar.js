import { getState, subscribe } from "../state/store.js";
import * as storage from "../state/storage.js";
import { contextTokens } from "../state/ctx_tokens.js";

function escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** localStorage key for the user's own CaaS allowance. */
export const BUDGET_KEY = "gca_budget_usd";

/**
 * The user's CaaS allowance, or null to fall back to the server's default.
 *
 * Exported so the settings panel writes what this reads — one definition of
 * both the key and what counts as a valid value. When they were separate, a
 * panel that stored "200.00" and a reader that expected a number was a bug
 * waiting on someone typing a currency symbol.
 *
 * Returns null rather than 200 on anything unusable: absent, unparseable, zero
 * or negative. Zero matters — it is a plausible thing to type for "I have no
 * budget", and it would make `cost / budget` Infinity and the bar NaN%.
 * Falling back to the server default keeps the display honest instead.
 *
 * Every access is wrapped: localStorage throws outright in some privacy modes,
 * and a stats refresh must not be what takes the sidebar down.
 */
export function userBudget() {
  try {
    const raw = storage.getItem(BUDGET_KEY);
    if (raw == null || raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

/**
 * P2-U: the right column is now a set of framed panels, each with a coloured
 * title strip and a badge — the look of the mockup's right column, but every
 * value is REAL data from an existing endpoint or existing client state:
 *
 *   SESSION  model, messages, tokens in/out, conversation cost, context %
 *   MONTH    this month's cost vs the user's budget
 *   PROJECT  name + working dir of the conversation's project
 *   SERVER   status + URL from /api/server/info
 *   LOG      the existing /api/server/logs SSE stream (kept so the real
 *            console log surface survives; ui-check seeds its contrast probes
 *            into #rsb-console)
 *
 * The old Metrics/Server rail (met-cost / met-elapsed / met-rate / met-last /
 * met-chat / met-steps / met-tools / met-all + Agents + Teams) is GONE. Its
 * real numbers merged into the panels above; nothing is duplicated.
 */
export function mountRightSidebar(root) {
  root.innerHTML = `
    <details class="rsb-panel glow" id="rsb-panel-session" open>
      <summary>
        <span class="rsb-panel-title">SESSION</span>
        <span class="rsb-panel-badge" id="rsb-session-badge">LIVE</span>
        <span class="rsb-panel-arrow">▶</span>
      </summary>
      <div class="rsb-panel-body">
        <div class="metric-row">
          <span class="metric-label">MODEL</span>
          <span class="metric-value" id="met-model">—</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">MESSAGES</span>
          <span class="metric-value" id="met-messages">—</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">TOKENS</span>
          <span class="metric-value" id="met-tokens">—</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">COST</span>
          <span class="metric-value" id="met-cost" title="Tracks conversations in this app only; total CaaS spend may be higher">—</span>
        </div>
        <div class="meter-row">
          <div class="meter-left">
            <span class="metric-label">CTX</span>
            <span class="meter-ref" id="met-ctx-ref">—</span>
          </div>
          <div class="meter-bar"><div class="budget-bar-fill budget-normal" id="ctx-bar-fill"></div></div>
          <span class="meter-pct" id="met-ctx">—</span>
        </div>
      </div>
    </details>
    <details class="rsb-panel glow" id="rsb-panel-month" open>
      <summary>
        <span class="rsb-panel-title">MONTH</span>
        <span class="rsb-panel-badge" id="rsb-month-badge">EST</span>
        <span class="rsb-panel-arrow">▶</span>
      </summary>
      <div class="rsb-panel-body">
        <div class="metric-row">
          <span class="metric-label">COST</span>
          <span class="metric-value" id="met-month-cost">—</span>
        </div>
        <div class="meter-row">
          <div class="meter-left">
            <span class="metric-label">BUDGET</span>
            <span class="meter-ref" id="met-budget-ref">—</span>
          </div>
          <div class="meter-bar"><div class="budget-bar-fill budget-normal" id="budget-bar-fill"></div></div>
          <span class="meter-pct" id="met-budget-pct">—</span>
        </div>
        <div class="budget-link-row">
          <a href="https://caas.open-webui.godaddy.com/apiKeys" target="_blank" class="budget-link">Check Balance →</a>
        </div>
      </div>
    </details>
    <details class="rsb-panel glow" id="rsb-panel-project" open>
      <summary>
        <span class="rsb-panel-title">PROJECT</span>
        <span class="rsb-panel-badge" id="rsb-project-badge">DIR</span>
        <span class="rsb-panel-arrow">▶</span>
      </summary>
      <div class="rsb-panel-body">
        <div class="metric-row">
          <span class="metric-label">NAME</span>
          <span class="metric-value" id="met-project">—</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">DIR</span>
          <span class="metric-value rsb-project-dir" id="met-project-dir">—</span>
        </div>
      </div>
    </details>
    <details class="rsb-panel glow" id="rsb-panel-server" open>
      <summary>
        <span class="rsb-panel-title">SERVER</span>
        <span class="rsb-panel-badge" id="rsb-server-badge">—</span>
        <span class="rsb-panel-arrow">▶</span>
      </summary>
      <div class="rsb-panel-body" id="rsb-server-body">
        <div class="server-status" role="status"><span class="server-dot"></span><span>Connecting…</span></div>
      </div>
    </details>
    <details class="rsb-panel glow" id="rsb-panel-log">
      <summary>
        <span class="rsb-panel-title">LOG</span>
        <span class="rsb-panel-badge">LIVE</span>
        <span class="rsb-panel-arrow">▶</span>
      </summary>
      <div class="rsb-panel-body">
        <div class="console-log" id="rsb-console">No log lines yet. Server activity will appear here.</div>
      </div>
    </details>
  `;

  // Persist <details> open/closed state per panel.
  root.querySelectorAll("details.rsb-panel").forEach((el) => {
    const key = `gca_rsb_${el.id}`;
    const saved = storage.getItem(key);
    if (saved === "0") el.removeAttribute("open");
    else if (saved === "1") el.setAttribute("open", "");
    el.addEventListener("toggle", () => {
      try { storage.setItem(key, el.open ? "1" : "0"); } catch { /* private mode */ }
    });
  });

  // Populated from /api/config on mount; safe fallback until fetch resolves.
  let _ctxByModel = {};
  let _labelsByModel = {};

  const modelEl   = root.querySelector("#met-model");
  const messagesEl = root.querySelector("#met-messages");
  const tokensEl  = root.querySelector("#met-tokens");
  const costEl    = root.querySelector("#met-cost");
  const ctxEl     = root.querySelector("#met-ctx");
  const ctxRefEl  = root.querySelector("#met-ctx-ref");
  const ctxBarEl  = root.querySelector("#ctx-bar-fill");
  const monthCostEl = root.querySelector("#met-month-cost");
  const budgetPctEl = root.querySelector("#met-budget-pct");
  const budgetRefEl = root.querySelector("#met-budget-ref");
  const budgetBarEl = root.querySelector("#budget-bar-fill");
  const projectEl = root.querySelector("#met-project");
  const projectDirEl = root.querySelector("#met-project-dir");
  const serverBadgeEl = root.querySelector("#rsb-server-badge");

  // P2-B E5: null means "the context window for the current model is unknown".
  // CTX% must then show "—", never a number computed against a guessed default.
  let ctxMax = null;
  let _currentModel = null;
  let _activeConvId = null;
  // One r.ok-checked JSON GET for every sidebar poll (P2-B E3/E4/E5).
  const _getJSON = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(`${url} ${r.status}`); return r.json(); });

  function fmtTok(n) {
    return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  }

  // Decide the unit from the ROUNDED value, not the raw one. Dividing first and
  // rounding after produced "1000.0k" for 999,999 — which is the exact figure you
  // are watching on a 1M-context model just before you run out of room.
  function fmtCompact(n) {
    if (n < 1_000) return String(n);
    const useM = n >= 999_500;                     // anything that rounds to 1M or more
    const v = n / (useM ? 1_000_000 : 1_000);
    const r = Math.round(v * 10) / 10;
    return `${r % 1 === 0 ? r : r.toFixed(1)}${useM ? "M" : "k"}`;
  }

  function updateCtx(inputTokens) {
    if (!inputTokens || !ctxEl) return;
    if (ctxMax == null) {
      // Unknown context window (e.g. /api/config failed): show the honest
      // empty state, never a percentage against a made-up denominator.
      ctxEl.textContent = "—";
      if (ctxRefEl) ctxRefEl.textContent = "—";
      if (ctxBarEl) { ctxBarEl.style.width = "0%"; ctxBarEl.className = "budget-bar-fill budget-normal"; }
      return;
    }
    const pct = Math.min((inputTokens / ctxMax) * 100, 100);
    const currLabel = fmtCompact(inputTokens);
    const maxLabel = fmtCompact(ctxMax);
    // P2-V: the exact percentage sits on its own on the right; the real
    // current/max token counts stay next to the label as the reference
    // point. No fabricated reset timestamp exists in any endpoint.
    ctxEl.textContent = `${Math.round(pct)}%`;
    if (ctxRefEl) ctxRefEl.textContent = `${currLabel} / ${maxLabel}`;
    if (ctxBarEl) {
      ctxBarEl.style.width = `${pct}%`;
      ctxBarEl.classList.remove("budget-normal", "budget-warn", "budget-crit");
      ctxBarEl.classList.add(pct < 60 ? "budget-normal" : pct < 85 ? "budget-warn" : "budget-crit");
    }
  }

  function _refreshCtx() {
    const msgs = getState().messages || [];
    const latestWithCtx = [...msgs].reverse().find(m => contextTokens(m) > 0);
    if (latestWithCtx) updateCtx(contextTokens(latestWithCtx));
  }

  function _renderMessages() {
    const msgs = getState().messages || [];
    if (messagesEl) messagesEl.textContent = msgs.length > 0 ? String(msgs.length) : "—";
  }

  function _renderProject(projectId) {
    const project = getState().projects.find(p => p.id === projectId);
    if (!project) {
      if (projectEl) projectEl.textContent = "—";
      if (projectDirEl) projectDirEl.textContent = "—";
      return;
    }
    if (projectEl) projectEl.textContent = project.name || "—";
    if (projectDirEl) projectDirEl.textContent = project.working_dir || "—";
  }

  // --- Session panel: conversation stats (real endpoint, no invented sums) ---
  function _fetchConvStats() {
    if (!_activeConvId) return;
    _getJSON(`/api/conversations/${_activeConvId}/stats`)
      .then((stats) => {
        const ti = stats.tokens_in;
        const to = stats.tokens_out;
        const cost = stats.cost_usd;
        if (tokensEl) {
          tokensEl.textContent = (ti == null && to == null) ? "—" : `↑${fmtTok(ti ?? 0)} ↓${fmtTok(to ?? 0)}`;
        }
        if (costEl) costEl.textContent = cost == null ? "—" : `$${Number(cost).toFixed(4)}`;
      })
      .catch(() => {
        // P2-B E3: an explicit unavailable state, never undefined/NaN.
        if (tokensEl) tokensEl.textContent = "—";
        if (costEl) costEl.textContent = "—";
      });
  }

  // --- Session panel: context window from the backend config ---
  function _fetchConfig() {
    _getJSON("/api/config")
      .then((cfg) => {
        (cfg.models || []).forEach((m) => {
          _ctxByModel[m.value] = m.context_window;
          _labelsByModel[m.value] = m.label;
        });
        // Re-apply for the model currently on screen so a failed first fetch
        // (CTX showing "—") recovers on the next successful poll.
        if (_currentModel != null) {
          ctxMax = _ctxByModel[_currentModel] ?? null;
          if (modelEl) modelEl.textContent = _labelsByModel[_currentModel] ?? "Model unknown";
          _refreshCtx();
        }
      })
      .catch(() => {});
  }

  // --- Server panel ---
  function _fetchServerInfo() {
    _getJSON("/api/server/info")
      .then((info) => {
        const body = root.querySelector("#rsb-server-body");
        if (serverBadgeEl) serverBadgeEl.textContent = "ONLINE";
        if (!body) return;
        body.innerHTML = `
          <div class="server-status" role="status"><span class="server-dot"></span><span>Online</span></div>
          <div class="server-url">${escHtml(info.url)}</div>
          <button class="server-btn" id="rsb-open-browser">⎋ Open in Browser</button>
        `;
        body.querySelector("#rsb-open-browser")?.addEventListener("click", () => {
          window.open(info.url, "_blank");
        });
      })
      .catch(() => {
        // P2-B E4: an explicit offline state, never the forever-"Connecting…"
        // that a failed first fetch used to leave behind.
        if (serverBadgeEl) serverBadgeEl.textContent = "OFFLINE";
        const body = root.querySelector("#rsb-server-body");
        if (!body) return;
        body.innerHTML = `
          <div class="server-status" role="status"><span class="server-dot offline"></span><span>Offline — server unreachable. Restart GoClaudaddy, then refresh.</span></div>
        `;
      });
  }

  // --- Month panel: all-chat stats ---
  function _fetchStats() {
    _getJSON("/api/server/stats")
      .then((stats) => {
        // Cost + budget bar.
        //
        // The denominator is the USER'S CaaS allowance, which the server has no
        // way to know: BUDGET_USD in routers/server.py is a hardcoded 200, the
        // platform default, and keys are routinely issued with more or with
        // none at all. So a colleague on a larger allowance was being shown a
        // bar against the wrong number and a "Monthly Spend Estimate" that was
        // simply wrong.
        //
        // Stored in localStorage rather than the database because it is a
        // DISPLAY preference and nothing server-side acts on it — the same
        // treatment the theme and the assess feature flag already get. It also
        // avoids a migration to re-add the app-wide config table that v19
        // dropped for having no consumers.
        const cost   = stats.monthly_cost_usd ?? 0;
        const budget = userBudget() ?? stats.budget_usd ?? 200;
        if (monthCostEl) monthCostEl.textContent = `~$${Number(cost).toFixed(2)}`;
        const pct = Math.min((cost / budget) * 100, 100);
        // P2-V: exact percentage on the right, real cost/budget as the
        // reference on the left. No fabricated billing-cycle reset time.
        if (budgetPctEl) budgetPctEl.textContent = `${Math.round(pct)}%`;
        if (budgetRefEl) budgetRefEl.textContent = `~$${Number(cost).toFixed(2)} / $${Number(budget).toFixed(0)}`;
        if (budgetBarEl) {
          budgetBarEl.style.width = `${pct}%`;
          budgetBarEl.classList.remove("budget-normal", "budget-warn", "budget-crit");
          budgetBarEl.classList.add(pct < 60 ? "budget-normal" : pct < 85 ? "budget-warn" : "budget-crit");
        }

        // Daily toast — deduped by date so repeated _fetchStats calls don't re-show it
        const TODAY = new Date().toISOString().slice(0, 10);
        const NOTIF_KEY = "gca_cost_notified";
        if (cost > 0 && storage.getItem(NOTIF_KEY) !== TODAY && stats.monthly_cost_usd != null) {
          showCostToast(cost, budget);
          storage.setItem(NOTIF_KEY, TODAY);
        }
      })
      .catch(() => {
        // P2-B E3: an explicit unavailable state — never "undefined chats"/NaN.
        if (monthCostEl) monthCostEl.textContent = "—";
        if (budgetPctEl) budgetPctEl.textContent = "—";
        if (budgetRefEl) budgetRefEl.textContent = "—";
        if (budgetBarEl) { budgetBarEl.style.width = "0%"; budgetBarEl.className = "budget-bar-fill budget-normal"; }
      });
  }

  // Reset every panel field + both progress bars to their empty state.
  function _resetMetrics() {
    [tokensEl, costEl, ctxEl, ctxRefEl, monthCostEl, budgetPctEl, budgetRefEl, projectEl, projectDirEl].forEach((el) => {
      if (el) el.textContent = "—";
    });
    if (modelEl) modelEl.textContent = "—";
    if (messagesEl) messagesEl.textContent = "—";
    if (ctxBarEl) { ctxBarEl.style.width = "0%"; ctxBarEl.className = "budget-bar-fill budget-normal"; }
    if (budgetBarEl) { budgetBarEl.style.width = "0%"; budgetBarEl.className = "budget-bar-fill budget-normal"; }
  }

  function setModel(model) {
    _currentModel = model;
    ctxMax = _ctxByModel[model] ?? null;
    if (modelEl) modelEl.textContent = _labelsByModel[model] ?? "Model unknown";
    _refreshCtx();
  }

  function setConversation(conversationId) {
    if (conversationId !== _activeConvId) {
      _activeConvId = conversationId;
      _resetMetrics();
      _fetchStats(); // repopulate MONTH immediately rather than leaving it blank
    }
    if (!conversationId) return;
    const conv = getState().conversations.find(c => c.id === conversationId);
    if (conv) {
      if (modelEl) modelEl.textContent = _labelsByModel[conv.model] ?? "Model unknown";
      _renderProject(conv.project_id);
    }
    _renderMessages();
    _fetchConvStats();
    _refreshCtx();
  }

  // Called from main.js when an assistant message finishes — also refreshes stats
  function notifyComplete(usage) {
    _fetchStats();
    _fetchConvStats();
    if (!usage) return;
    const used = contextTokens(usage);
    if (used) updateCtx(used);
  }

  // --- Existing stats-refresh behaviour, wired onto the store's streaming
  // falling edge (the same edge stats-refresh-check.mjs drives). A turn ENDING
  // refreshes stats; a turn STARTING or an unrelated store change does not. ---
  let prevStreaming = getState().streaming;
  subscribe((state) => {
    if (state.streaming && !prevStreaming) {
      // Turn started: nothing has been billed yet, so no fetch.
    } else if (!state.streaming && prevStreaming) {
      // A turn just ended: refresh conversation + server stats now rather than
      // waiting out the 60s poll.
      _fetchStats();
      _fetchConvStats();
    }
    prevStreaming = state.streaming;
    _renderMessages();
    _refreshCtx();
  });

  _fetchConfig();
  _fetchServerInfo();
  _fetchStats();
  // 60s fallback poll in case a turn-end is missed; guarded like the other
  // pollers below. It carries config and server info so their failure states
  // recover on the next successful poll (P2-B).
  setInterval(() => {
    if (root.classList.contains("sb-collapsed")) return;
    _fetchStats();
    _fetchConfig();
    _fetchServerInfo();
    if (_activeConvId) _fetchConvStats();
  }, 60_000);

  // Budget changed in the settings drawer. Without this the new allowance
  // would not show until the next 60s poll or the next finished turn, and a
  // setting that appears to do nothing for a minute reads as a setting that
  // did not save — which is exactly what someone checking their own budget is
  // most likely to conclude.
  window.addEventListener("gca:budget-changed", () => _fetchStats());

  function showCostToast(cost, budget) {
    const toast = document.createElement("div");
    toast.className = "cost-toast";
    // 4.1.3: the spend estimate is a polite status message.
    toast.setAttribute("role", "status");
    toast.innerHTML = `
      <div class="cost-toast-header">
        <span class="cost-toast-title">Monthly Spend Estimate</span>
        <button class="cost-toast-close" aria-label="Close">✕</button>
      </div>
      <div class="cost-toast-body">
        Estimated GoClaudaddy spend this month: ~$${cost.toFixed(2)} (local tracking only) · This is an estimate only.
      </div>
      <a href="https://caas.open-webui.godaddy.com/apiKeys" target="_blank" class="cost-toast-link">Check Balance →</a>
    `;
    document.body.appendChild(toast);

    // Anchor above the composer's actual (live) top edge instead of a fixed
    // pixel guess, so the toast can never sit on top of #composer-send even
    // when the textarea grows. Re-measured via ResizeObserver so it tracks
    // the composer growing/shrinking while the toast is on screen.
    const composerEl = document.getElementById("composer");
    const GAP = 12;
    const reposition = () => {
      if (!composerEl) return;
      const r = composerEl.getBoundingClientRect();
      toast.style.bottom = `${Math.max(20, window.innerHeight - r.top + GAP)}px`;
    };
    reposition();
    const ro = composerEl && window.ResizeObserver ? new ResizeObserver(reposition) : null;
    if (ro) ro.observe(composerEl);
    window.addEventListener("resize", reposition);

    const close = () => {
      toast.remove();
      if (ro) ro.disconnect();
      window.removeEventListener("resize", reposition);
    };
    toast.querySelector(".cost-toast-close").addEventListener("click", close);
    setTimeout(close, 12000);
  }

  // --- Console: SSE log stream ---
  const consoleEl = root.querySelector("#rsb-console");
  if (consoleEl) {
    let autoScroll = true;
    consoleEl.addEventListener("scroll", () => {
      autoScroll = consoleEl.scrollTop + consoleEl.clientHeight >= consoleEl.scrollHeight - 24;
    });

    const clearLogPlaceholder = () => {
      if (consoleEl.textContent === "No log lines yet. Server activity will appear here.") consoleEl.textContent = "";
    };
    const es = new EventSource("/api/server/logs");
    es.onmessage = (ev) => {
      if (!ev.data.trim()) return; // keepalive
      clearLogPlaceholder();
      const line = document.createElement("div");
      line.className = `console-line ${_logLevel(ev.data)}`;
      line.textContent = ev.data;
      consoleEl.appendChild(line);
      if (autoScroll) consoleEl.scrollTop = consoleEl.scrollHeight;
      // Trim to 500 lines
      while (consoleEl.children.length > 500) consoleEl.removeChild(consoleEl.firstChild);
    };
    let esErrorShown = false;
    es.onopen = () => { esErrorShown = false; };
    es.onerror = () => {
      if (esErrorShown) return;
      esErrorShown = true;
      clearLogPlaceholder();
      const line = document.createElement("div");
      line.className = "console-line lvl-warn";
      line.textContent = "Log stream disconnected — reconnecting…";
      consoleEl.appendChild(line);
    };
  }

  return { notifyComplete, setModel, setConversation };
}

function _logLevel(line) {
  if (line.includes("[ERROR]"))   return "lvl-error";
  if (line.includes("[WARNING]") || line.includes("[WARN]")) return "lvl-warn";
  if (line.includes("[DEBUG]"))   return "lvl-debug";
  return "lvl-info";
}
