import { getState, subscribe } from "../state/store.js";

function escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function mountRightSidebar(root) {
  root.innerHTML = `
    <details class="sb-section" open>
      <summary><span>METRICS</span><span class="sb-section-arrow">â–¶</span></summary>
      <div class="sb-section-body">
        <div class="metric-row">
          <span class="metric-label">COST</span>
          <span class="metric-value" id="met-cost" title="Tracks conversations in this app only; total CaaS spend may be higher">â€”</span>
        </div>
        <div class="budget-bar-wrap">
          <div class="budget-bar-fill" id="budget-bar-fill"></div>
        </div>
        <div class="budget-link-row">
          <a href="https://caas.open-webui.godaddy.com/apiKeys" target="_blank" class="budget-link">Check Balance â†’</a>
        </div>
        <div class="metric-row">
          <span class="metric-label">CTX</span>
          <span class="metric-value" id="met-ctx">â€”</span>
        </div>
        <div class="budget-bar-wrap">
          <div class="budget-bar-fill budget-normal" id="ctx-bar-fill"></div>
        </div>
        <div class="metric-row">
          <span class="metric-label">ELAPSED</span>
          <span class="metric-value" id="met-elapsed">â€”</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">TOK/S</span>
          <span class="metric-value" id="met-rate">â€”</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">LAST</span>
          <span class="metric-value" id="met-last">â€”</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">THIS CHAT</span>
          <span class="metric-value" id="met-chat">â€”</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">STEPS</span>
          <span class="metric-value" id="met-steps">â€”</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">TOOLS</span>
          <span class="metric-value" id="met-tools">â€”</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">ALL CHATS</span>
          <span class="metric-value" id="met-all">â€”</span>
        </div>
      </div>
    </details>
    <details class="sb-section" open>
      <summary><span>SERVER</span><span class="sb-section-arrow">â–¶</span></summary>
      <div class="sb-section-body" id="rsb-server-body">
        <div class="server-status"><span class="server-dot"></span><span>Connectingâ€¦</span></div>
      </div>
    </details>
    <details class="sb-section">
      <summary><span>CONSOLE</span><span class="sb-section-arrow">â–¶</span></summary>
      <div class="sb-section-body">
        <div class="console-log" id="rsb-console"></div>
      </div>
    </details>
    <details class="sb-section">
      <summary>
        <span class="sb-section-icon">âš¡</span>
        <span>AGENTS</span>
        <span class="sb-section-count" id="rsb-agent-count">0</span>
        <span class="sb-section-arrow">â–¶</span>
      </summary>
      <div class="sb-section-body" id="rsb-agents-body">
        <div class="sb-empty">No agents running</div>
      </div>
    </details>
    <details class="sb-section" id="sb-teams" hidden>
      <summary>
        <span class="sb-section-icon">â¬¡</span>
        <span>TEAMS</span>
        <span class="sb-section-count" id="rsb-team-count">0</span>
        <span class="sb-section-arrow">â–¶</span>
      </summary>
      <div class="sb-section-body" id="rsb-teams-body">
        <div class="sb-empty">No teams</div>
      </div>
    </details>
  `;

  // Persist <details> open/closed state per section
  root.querySelectorAll("details.sb-section").forEach((el) => {
    const key = `gca_sb_${el.id || el.querySelector("summary span")?.textContent?.trim()}`;
    const saved = localStorage.getItem(key);
    if (saved === "0") el.removeAttribute("open");
    else if (saved === "1") el.setAttribute("open", "");
    el.addEventListener("toggle", () => {
      localStorage.setItem(key, el.open ? "1" : "0");
    });
  });

  // Populated from /api/config on mount; safe fallback until fetch resolves.
  let _ctxByModel = {};

  const elEl    = root.querySelector("#met-elapsed");
  const rateEl  = root.querySelector("#met-rate");
  const lastEl  = root.querySelector("#met-last");
  const chatEl  = root.querySelector("#met-chat");
  const allEl   = root.querySelector("#met-all");
  const stepsEl = root.querySelector("#met-steps");
  const toolsEl = root.querySelector("#met-tools");
  const ctxEl   = root.querySelector("#met-ctx");
  const ctxBarEl = root.querySelector("#ctx-bar-fill");
  let ctxMax = 200_000;

  function updateCtx(inputTokens) {
    if (!inputTokens || !ctxEl) return;
    const pct = Math.min((inputTokens / ctxMax) * 100, 100);
    const currLabel = fmtCompact(inputTokens);
    const maxLabel = fmtCompact(ctxMax);
    ctxEl.textContent = `${Math.round(pct)}% Â· ${currLabel} / ${maxLabel}`;
    if (ctxBarEl) {
      ctxBarEl.style.width = `${pct}%`;
      ctxBarEl.classList.remove("budget-normal", "budget-warn", "budget-crit");
      ctxBarEl.classList.add(pct < 60 ? "budget-normal" : pct < 85 ? "budget-warn" : "budget-crit");
    }
  }

  function fmtMs(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }
  function fmtTok(n) {
    return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  }

  // Decide the unit from the ROUNDED value, not the raw one. Dividing first and
  // rounding after produced "1000.0k" for 999,999 â€” which is the exact figure you
  // are watching on a 1M-context model just before you run out of room.
  function fmtCompact(n) {
    if (n < 1_000) return String(n);
    const useM = n >= 999_500;                     // anything that rounds to 1M or more
    const v = n / (useM ? 1_000_000 : 1_000);
    const r = Math.round(v * 10) / 10;
    return `${r % 1 === 0 ? r : r.toFixed(1)}${useM ? "M" : "k"}`;
  }

  // --- Metrics: live elapsed timer ---
  let streamStart = null;
  let elapsedTimer = null;
  let prevStreaming = false;
  // Phase 4 (4-D5). What stood here read `state.lastEventType`, which NOTHING in
  // the frontend or the backend has ever written â€” measured: two references in
  // this file, both reads, zero writes anywhere. So the condition was
  // permanently false and `_fetchStats()` on turn completion never ran once.
  //
  // It was not merely dead. The 60s interval below calls itself a "fallback poll
  // in case a 'done' event is missed" â€” but with this branch inert that fallback
  // was the ONLY mechanism, so COST and ALL CHATS could sit stale for up to a
  // minute after a turn ended while a comment claimed otherwise. Same shape as
  // v15 in 4-P1: a primary path that never ran and a fallback silently carrying
  // the whole load.
  //
  // So it is WIRED rather than deleted, onto the falling edge below, which uses
  // `streaming` â€” a field the store actually has and actually writes.

  subscribe((state) => {
    if (state.streaming && !prevStreaming) {
      streamStart = Date.now();
      if (elEl) elEl.classList.add("streaming");
      clearInterval(elapsedTimer);
      elapsedTimer = setInterval(() => {
        if (elEl && streamStart) elEl.textContent = fmtMs(Date.now() - streamStart);
      }, 500);
    } else if (!state.streaming && prevStreaming) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
      if (elEl) elEl.classList.remove("streaming");
      // A turn just ended: refresh COST / ALL CHATS now rather than waiting out
      // the 60s poll. This is the line the dead lastEventType branch was meant
      // to be.
      _fetchStats();
    }
    prevStreaming = state.streaming;

    // This-chat totals from store messages.
    // Sum (input[i] + output[i]) per turn â€” that's the actual tokens billed for each API call.
    // Never sum input_tokens alone: each turn's input already includes all prior outputs,
    // so summing only inputs double-counts every previous turn's context.
    const msgs = state.messages || [];
    const sessionBilled = msgs.reduce((s, m) => s + (m.input_tokens || 0) + (m.output_tokens || 0), 0);
    const sessionOut    = msgs.reduce((s, m) => s + (m.output_tokens || 0), 0);
    if (chatEl) {
      chatEl.textContent = sessionBilled > 0
        ? `${fmtTok(sessionBilled)} (â†“${fmtTok(sessionOut)} out)`
        : "â€”";
    }

    // Context window: latest message's input_tokens is the actual context size for that turn
    const latestWithCtx = [...msgs].reverse().find(m => m.input_tokens > 0);
    if (latestWithCtx) updateCtx(latestWithCtx.input_tokens);
  });

  // Reset every metric row + both progress bars to their empty state.
  function _resetMetrics() {
    [lastEl, rateEl, elEl, chatEl, stepsEl, toolsEl, ctxEl, allEl].forEach((el) => {
      if (el) el.textContent = "â€”";
    });
    const costEl = root.querySelector("#met-cost");
    if (costEl) costEl.textContent = "â€”";
    if (elEl) elEl.classList.remove("streaming");
    const barEl = root.querySelector("#budget-bar-fill");
    if (barEl) { barEl.style.width = "0%"; barEl.className = "budget-bar-fill budget-normal"; }
    if (ctxBarEl) { ctxBarEl.style.width = "0%"; ctxBarEl.className = "budget-bar-fill budget-normal"; }
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }

  function setModel(model) {
    ctxMax = _ctxByModel[model] ?? 200_000;
    _resetMetrics();
  }

  let _activeConvId = null;

  function setConversation(conversationId) {
    if (conversationId !== _activeConvId) {
      _activeConvId = conversationId;
      _resetMetrics();
      _fetchStats(); // repopulate COST / ALL CHATS immediately rather than leaving them blank
    }
    if (!conversationId) return;
    fetch(`/api/conversations/${conversationId}/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(stats => {
        if (!stats) return;
        if (stepsEl) stepsEl.textContent = stats.step_count > 0 ? String(stats.step_count) : "â€”";
        if (toolsEl) toolsEl.textContent = stats.tool_call_count > 0 ? String(stats.tool_call_count) : "â€”";
      })
      .catch(() => {});
  }

  // Called from main.js when an assistant message finishes â€” also refreshes cost/stats
  function notifyComplete(usage, elapsedMs) {
    _fetchStats();
    if (_activeConvId) setConversation(_activeConvId);
    if (!usage) return;
    const { input_tokens: inp = 0, output_tokens: out = 0 } = usage;
    if (inp) updateCtx(inp);

    if (elEl && elapsedMs != null) {
      elEl.textContent = fmtMs(elapsedMs);
      elEl.classList.remove("streaming");
    }
    if (rateEl && out > 0 && elapsedMs > 0) {
      rateEl.textContent = `${(out / (elapsedMs / 1000)).toFixed(1)} tok/s`;
    }
    if (lastEl) {
      const parts = [];
      if (inp) parts.push(`â†‘${fmtTok(inp)}`);
      if (out) parts.push(`â†“${fmtTok(out)}`);
      if (elapsedMs != null) parts.push(fmtMs(elapsedMs));
      lastEl.textContent = parts.join(" ") || "â€”";
    }
  }

  // --- Model context windows from backend ---
  fetch("/api/config")
    .then((r) => r.json())
    .then((cfg) => {
      (cfg.models || []).forEach((m) => { _ctxByModel[m.value] = m.context_window; });
    })
    .catch(() => {});

  // --- Server info ---
  fetch("/api/server/info")
    .then((r) => r.json())
    .then((info) => {
      const body = root.querySelector("#rsb-server-body");
      if (!body) return;
      body.innerHTML = `
        <div class="server-status"><span class="server-dot"></span><span>Online</span></div>
        <div class="server-url">${escHtml(info.url)}</div>
        <button class="server-btn" id="rsb-open-browser">âŽ‹ Open in Browser</button>
      `;
      body.querySelector("#rsb-open-browser")?.addEventListener("click", () => {
        window.open(info.url, "_blank");
      });
    })
    .catch(() => {});

  // --- All-chat stats ---
  function _fetchStats() {
    fetch("/api/server/stats")
      .then((r) => r.json())
      .then((stats) => {
        if (allEl) {
          const total = stats.total_input + stats.total_output;
          allEl.textContent = total > 0
            ? `${fmtTok(total)} tok / ${stats.chat_count} chats`
            : `${stats.chat_count} chats`;
        }

        // Cost + budget bar
        const costEl = root.querySelector("#met-cost");
        const barEl  = root.querySelector("#budget-bar-fill");
        const cost   = stats.monthly_cost_usd ?? 0;
        const budget = stats.budget_usd ?? 200;
        if (costEl) costEl.textContent = `~$${cost.toFixed(2)} / $${budget.toFixed(0)}`;
        if (barEl) {
          const pct = Math.min((cost / budget) * 100, 100);
          barEl.style.width = `${pct}%`;
          barEl.classList.remove("budget-normal", "budget-warn", "budget-crit");
          barEl.classList.add(pct < 60 ? "budget-normal" : pct < 85 ? "budget-warn" : "budget-crit");
        }

        // Daily toast â€” deduped by date so repeated _fetchStats calls don't re-show it
        const TODAY = new Date().toISOString().slice(0, 10);
        const NOTIF_KEY = "gca_cost_notified";
        if (cost > 0 && localStorage.getItem(NOTIF_KEY) !== TODAY && stats.monthly_cost_usd != null) {
          showCostToast(cost, budget);
          localStorage.setItem(NOTIF_KEY, TODAY);
        }
      })
      .catch(() => {});
  }
  _fetchStats();
  // 60s fallback poll in case a turn-end is missed; guarded like the other
  // pollers below. It is a genuine fallback again as of 4-D5 â€” until then the
  // turn-end refresh it backs up had never fired, so this was carrying the whole
  // job and the word "fallback" was not true.
  setInterval(() => {
    if (root.classList.contains("sb-collapsed")) return;
    _fetchStats();
  }, 60_000);

  function showCostToast(cost, budget) {
    const toast = document.createElement("div");
    toast.className = "cost-toast";
    toast.innerHTML = `
      <div class="cost-toast-header">
        <span class="cost-toast-title">Monthly Spend Estimate</span>
        <button class="cost-toast-close" aria-label="Close">âœ•</button>
      </div>
      <div class="cost-toast-body">
        Estimated GoClaudaddy spend this month: ~$${cost.toFixed(2)} (local tracking only) Â· This is an estimate only.
      </div>
      <a href="https://caas.open-webui.godaddy.com/apiKeys" target="_blank" class="cost-toast-link">Check Balance â†’</a>
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

    const es = new EventSource("/api/server/logs");
    es.onmessage = (ev) => {
      if (!ev.data.trim()) return; // keepalive
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
      const line = document.createElement("div");
      line.className = "console-line lvl-warn";
      line.textContent = "-- log stream disconnected --";
      consoleEl.appendChild(line);
    };
  }

  // --- Active agents polling ---
  const agentsBody = root.querySelector("#rsb-agents-body");
  const agentCount = root.querySelector("#rsb-agent-count");
  const agentsSection = agentsBody?.closest("details.sb-section");

  function pollAgents() {
    if (root.classList.contains("sb-collapsed")) return;
    fetch("/api/agents/status")
      .then(r => r.json())
      .then(agents => {
        if (!agentsBody) return;
        if (agentCount) agentCount.textContent = agents.length;
        if (agentsSection) agentsSection.hidden = agents.length === 0;
        if (agents.length === 0) {
          agentsBody.innerHTML = '<div class="sb-empty">No agents running</div>';
          return;
        }
        agentsBody.innerHTML = agents.map(a => `
          <div class="metric-row">
            <span class="metric-label" title="${escHtml(a.conversation_id)}">${escHtml(a.name.slice(0, 18))}</span>
            <span class="metric-value streaming">${escHtml(String(a.elapsed_s))}s</span>
          </div>
        `).join("");
      })
      .catch(() => {});
  }
  pollAgents();
  setInterval(pollAgents, 2000);

  // --- Team sessions (feature-flagged) ---
  const teamsSection = root.querySelector("#sb-teams");
  const teamsBody = root.querySelector("#rsb-teams-body");
  const teamCount = root.querySelector("#rsb-team-count");

  let _teamsCount = 0;

  function _applyTeamsFlag() {
    if (teamsSection) teamsSection.hidden = localStorage.getItem("gca_feat_teams") !== "1" || _teamsCount === 0;
  }
  _applyTeamsFlag();
  window.addEventListener("gca:feature", (e) => {
    if (e.detail?.name === "teams") _applyTeamsFlag();
  });

  function pollTeams() {
    if (root.classList.contains("sb-collapsed")) return;
    if (!teamsBody || localStorage.getItem("gca_feat_teams") !== "1") return;
    fetch("/api/teams")
      .then(r => r.json())
      .then(teams => {
        if (teamCount) teamCount.textContent = teams.length;
        _teamsCount = teams.length;
        _applyTeamsFlag();
        if (!teamsBody) return;
        if (teams.length === 0) {
          teamsBody.innerHTML = '<div class="sb-empty">No teams</div>';
          return;
        }
        // Slice the raw string BEFORE escaping â€” escaping first then slicing can
        // cut an entity in half (`&amp;` -> `&am`) and emit broken markup.
        teamsBody.innerHTML = teams.map(t => `
          <div class="metric-row">
            <span class="metric-label" title="${escHtml(t.id)}">${escHtml(String(t.name ?? "").slice(0, 16))}</span>
            <span class="metric-value">~$${(t.cost_usd || 0).toFixed(3)} Â· ${Number(t.members?.length ?? 0)} agents</span>
          </div>
        `).join("");
      })
      .catch(() => {});
  }
  pollTeams();
  setInterval(pollTeams, 5000);

  return { notifyComplete, setModel, setConversation };
}

function _logLevel(line) {
  if (line.includes("[ERROR]"))   return "lvl-error";
  if (line.includes("[WARNING]") || line.includes("[WARN]")) return "lvl-warn";
  if (line.includes("[DEBUG]"))   return "lvl-debug";
  return "lvl-info";
}
