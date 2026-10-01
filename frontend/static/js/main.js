import { ChatSocket } from "./api/socket.js";
import { api } from "./api/http.js";
import {
  loadConversations,
  loadProjects,
  selectConversation,
  createConversation,
} from "./state/actions.js";
import { getState, subscribe } from "./state/store.js";

const MODEL_LABELS = {
  "claude-sonnet-4-6": "Sonnet 4.6",
  "claude-opus-4-5": "Opus 4.5",
  "claude-haiku-4-5-20251001": "Haiku 4.5",
};

function fmtTok(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(0)}k` : `${n}`;
}

// Keep in sync with PERM_LABELS in settings_panel.js
const PERM_LABELS = {
  acceptEdits: "Auto-approve edits",
  auto: "Full auto",
  bypassPermissions: "Bypass all prompts",
  manual: "Manual approval",
  dontAsk: "Never ask",
  plan: "Plan mode",
};

function _esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function updateTelemetry(conv) {
  const subtitle = document.getElementById("conv-subtitle");
  if (!subtitle) return;
  const { projects } = getState();
  const model = MODEL_LABELS[conv.model] ?? conv.model ?? "—";
  const permLabel = PERM_LABELS[conv.permission_mode];
  const project = projects.find(p => p.id === conv.project_id);
  const parts = [`<span class="sub-model">${_esc(model)}</span>`];
  if (permLabel) parts.push(_esc(permLabel));
  if (project) parts.push(`<span class="sub-project">${_esc(project.name)}</span>`);
  subtitle.innerHTML = parts.join(' <span class="sub-sep">·</span> ');
}
import { mountChatPane } from "./ui/chat_pane.js";
import { mountComposer } from "./ui/composer.js";
import { mountSidebarConversations } from "./ui/sidebar_conversations.js";
import { mountSidebarProjects } from "./ui/sidebar_projects.js";
import { mountSettingsPanel, openDrawer, closeDrawer } from "./ui/settings_panel.js";
import { mountRightSidebar } from "./ui/right_sidebar.js";
import { maybeShowOnboarding } from "./ui/onboarding_tour.js";
import { openTemplatePicker } from "./ui/template_picker.js";
import { trapFocus } from "./ui/modal.js";
import { mountTooltips } from "./ui/tooltips.js";

let currentSocket = null;
let currentConversation = null;
let sidebarConvsRoot = null;
let settingsDrawerEl = null;
let chatPaneRef = null;
let composerRoot = null;
let rightSidebarRef = null;

function showChatConnectionError(compRoot, chatPane, conversationId) {
  // P2-B E2: the WebSocket could not be opened. Give the user a visible message
  // and a Retry control that re-runs the same switch path — a successful retry
  // mounts the composer with no page reload.
  compRoot.innerHTML = `
    <div id="chat-connection-error" class="chat-connection-error" role="alert">
      <p>The chat connection failed — the realtime channel could not be opened.</p>
      <button id="chat-retry-btn">Retry</button>
    </div>
  `;
  compRoot.querySelector("#chat-retry-btn").addEventListener("click", () => {
    switchToConversation(conversationId, chatPane, compRoot).catch(() => {
      // If the retry itself fails, switchToConversation's own catch has already
      // re-rendered this retry UI; nothing more to do here.
    });
  });
}

async function switchToConversation(id, chatPane, compRoot) {
  if (currentSocket) currentSocket.close();

  const conversation = await selectConversation(id);
  currentConversation = conversation;
  rightSidebarRef?.setModel(conversation.model);
  rightSidebarRef?.setConversation(id);
  chatPane.setConversationId(id);
  const msgs = getState().messages;
  if (msgs.length === 0) {
    chatPane.showEmptyState();
  } else {
    chatPane.renderHistory(msgs);
  }

  updateTelemetry(conversation);
  await mountSettingsPanel(settingsDrawerEl, conversation, {
    onChange: (updates) => {
      if (currentConversation) {
        currentConversation = { ...currentConversation, ...updates };
        updateTelemetry(currentConversation);
      }
    },
  });

  const socket = new ChatSocket(id);
  try {
    await socket.connect();
  } catch (err) {
    // P2-B E2: the WS was refused (or closed before it ever opened). Stop any
    // auto-reconnect the wrapper may have scheduled — the Retry control below
    // owns reconnection now — and show a visible failure instead of silently
    // leaving the composer unmounted.
    socket.close();
    showChatConnectionError(compRoot, chatPane, id);
    return;
  }
  currentSocket = socket;
  chatPane.bindSocket(socket);
  // After bindSocket, never before — checkResync() emits _resync_running /
  // _resync_done, and those listeners are registered by the line above.
  socket.checkResync();

  // Remount composer with fresh auto-title flag
  mountComposer(compRoot, socket, chatPane);
  if (compRoot._resetFirstMessage) compRoot._resetFirstMessage();

  // Update header title
  const titleEl = document.getElementById("conv-title-text");
  if (titleEl) titleEl.textContent = conversation.name;
}

async function boot() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <nav id="sidebar" aria-label="Primary">
      <div id="sidebar-logo">
        <img src="/static/images/goclaudaddy-mark.svg" alt="GoClaudaddy">
        <span>GoClaudaddy</span>
      </div>
      <div id="sidebar-projects"></div>
      <hr class="sidebar-sep">
      <div id="sidebar-conversations"></div>
    </nav>
    <main id="chat-area" tabindex="-1">
      <div id="chat-header">
        <div class="header-title-group">
          <div class="header-title-row">
            <span id="header-dot" class="header-dot header-dot-idle"></span>
            <h1 id="conv-title-text" class="conv-title-text">GoClaudaddy</h1>
          </div>
          <div id="conv-subtitle" class="conv-subtitle"></div>
        </div>
        <div id="header-actions">
          <button id="export-btn" title="Export conversation (Ctrl+E)" class="header-btn" data-tooltip="Export (Ctrl+E)">⬇ Export</button>
          <button id="shortcuts-btn" title="Keyboard shortcuts (?)" class="header-btn" data-tooltip="Shortcuts (?)">⌨</button>
          <button id="settings-btn" title="Settings (Ctrl+,)" class="header-btn" data-tooltip="Settings (Ctrl+,)">⚙</button>
          <button id="sb-toggle-btn" title="Toggle sidebar (Ctrl+B)" class="header-btn" data-tooltip="Sidebar (Ctrl+B)" aria-controls="right-sidebar" aria-expanded="true">◫</button>
        </div>
      </div>
      <div id="settings-drawer" class="settings-drawer" aria-hidden="true" inert></div>
      <div id="chat-scroll"></div>
      <div id="composer"></div>
    </main>

    <aside id="right-sidebar" aria-label="Session, month, project and server panels"></aside>

    <div id="shortcuts-overlay" class="shortcuts-overlay" hidden>
      <div class="shortcuts-box">
        <div class="shortcuts-header">
          <span>Keyboard Shortcuts</span>
          <button class="shortcuts-close" aria-label="Close keyboard shortcuts">✕</button>
        </div>
        <div class="shortcuts-grid">
          <kbd>Ctrl+Shift+N</kbd><span>New conversation</span>
          <kbd>Ctrl+Shift+T</kbd><span>Template picker</span>
          <kbd>Ctrl+K</kbd><span>Template autosuggest</span>
          <kbd>Ctrl+&#96;</kbd><span>Terminal panel</span>
          <kbd>Ctrl+,</kbd><span>Toggle settings</span>
          <kbd>Ctrl+B</kbd><span>Toggle right sidebar</span>
          <kbd>Ctrl+E</kbd><span>Export conversation</span>
          <kbd>/</kbd><span>Focus chat search</span>
          <kbd>?</kbd><span>Show shortcuts</span>
          <kbd>Esc</kbd><span>Close panels / modals</span>
          <kbd>Enter</kbd><span>Send message</span>
          <kbd>Shift+Enter</kbd><span>New line</span>
        </div>
      </div>
    </div>
  `;

  const rightSidebarEl = document.getElementById("right-sidebar");
  const sbToggleBtn = document.getElementById("sb-toggle-btn");
  function setSidebarCollapsed(collapsed, persist = false) {
    rightSidebarEl.classList.toggle("sb-collapsed", collapsed);
    rightSidebarEl.toggleAttribute("inert", collapsed);
    sbToggleBtn.setAttribute("aria-expanded", String(!collapsed));
    if (persist) localStorage.setItem("gca_sb_open", collapsed ? "0" : "1");
  }
  setSidebarCollapsed(window.innerWidth < 1280 || localStorage.getItem("gca_sb_open") !== "1");
  rightSidebarRef = mountRightSidebar(rightSidebarEl);

  const chatScrollEl = document.getElementById("chat-scroll");
  const chatPane = mountChatPane(chatScrollEl, {
    onRetry: (text) => {
      if (composerRoot?.setText) composerRoot.setText(text);
    },
    onComplete: (usage, elapsedMs) => {
      rightSidebarRef?.notifyComplete(usage, elapsedMs);
    },
  });
  chatPaneRef = chatPane;
  composerRoot = document.getElementById("composer");
  settingsDrawerEl = document.getElementById("settings-drawer");
  sidebarConvsRoot = document.getElementById("sidebar-conversations");

  // Expose openTemplatePicker for the empty-state "Browse all" link
  window._openTemplatePicker = openTemplatePicker;

  // Header buttons
  document.getElementById("settings-btn").addEventListener("click", () => {
    if (settingsDrawerEl.classList.contains("drawer-open")) {
      closeDrawer(settingsDrawerEl);
      document.getElementById("settings-btn")?.focus();
    } else openDrawer(settingsDrawerEl);
  });
  sbToggleBtn.addEventListener("click", () => {
    setSidebarCollapsed(!rightSidebarEl.classList.contains("sb-collapsed"), true);
  });
  document.getElementById("export-btn").addEventListener("click", () => {
    chatPane.exportConversation();
  });

  // 1.4.13: [data-tooltip] hosts get Esc dismissal + hover persistence.
  mountTooltips();

  const shortcutsOverlay = document.getElementById("shortcuts-overlay");

  // This overlay lives in the markup and is shown/hidden by toggling `hidden`
  // from FIVE places: the header button, the "?" shortcut, its close button,
  // a backdrop mousedown, and Escape. Rather than install and release the trap
  // at each of those call sites — where the next one added would silently miss
  // it — observe the attribute that actually decides whether it is on screen.
  let _shortcutsTrap = null;
  let _shortcutsReturnFocus = null;
  new MutationObserver(() => {
    if (!shortcutsOverlay.hidden && !_shortcutsTrap) {
      // 2.4.3: remember the trigger so focus can be handed back on close.
      _shortcutsReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      _shortcutsTrap = trapFocus(shortcutsOverlay);
      // Focus must start inside, or the first Tab is the user's only way in.
      shortcutsOverlay.querySelector(".shortcuts-close")?.focus();
    } else if (shortcutsOverlay.hidden && _shortcutsTrap) {
      _shortcutsTrap();
      _shortcutsTrap = null;
      if (_shortcutsReturnFocus?.isConnected) _shortcutsReturnFocus.focus();
      _shortcutsReturnFocus = null;
    }
  }).observe(shortcutsOverlay, { attributes: true, attributeFilter: ["hidden"] });
  document.getElementById("shortcuts-btn").addEventListener("click", () => {
    shortcutsOverlay.hidden = false;
  });
  document.querySelector(".shortcuts-close").addEventListener("click", () => {
    shortcutsOverlay.hidden = true;
  });
  shortcutsOverlay.addEventListener("mousedown", (e) => {
    if (e.target === shortcutsOverlay) shortcutsOverlay.hidden = true;
  });

  subscribe((state) => {
    const dot = document.getElementById("header-dot");
    if (dot) dot.className = `header-dot header-dot-${state.streaming ? "streaming" : "idle"}`;
    if (currentConversation) updateTelemetry(currentConversation);
  });

  let conversations;
  try {
    await loadProjects();
    conversations = await loadConversations();
  } catch {
    app.innerHTML = `<div id="boot-msg">Cannot reach backend. Is GoClaudaddy running?</div>`;
    return;
  }

  mountSidebarConversations(sidebarConvsRoot, async (id) => {
    await switchToConversation(id, chatPane, composerRoot);
    // Update title on conversation switch (may lag behind state update)
    const conv = getState().conversations.find((c) => c.id === id);
    const titleEl = document.getElementById("conv-title-text");
    if (conv && titleEl) titleEl.textContent = conv.name;
  }, () => {
    // No conversations left after delete
    chatPane.showEmptyState();
    if (currentSocket) { currentSocket.close(); currentSocket = null; }
    currentConversation = null;
    const titleEl = document.getElementById("conv-title-text");
    if (titleEl) titleEl.textContent = "GoClaudaddy";
  });

  mountSidebarProjects(document.getElementById("sidebar-projects"), async () => {
    conversations = await loadConversations();
    if (conversations.length > 0) {
      await switchToConversation(conversations[0].id, chatPane, composerRoot);
    } else {
      chatPane.showEmptyState();
      if (currentSocket) currentSocket.close();
    }
  });

  if (conversations.length === 0) {
    const conv = await createConversation();
    await switchToConversation(conv.id, chatPane, composerRoot);
  } else {
    await switchToConversation(conversations[0].id, chatPane, composerRoot);
  }

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    const inInput = document.activeElement?.tagName === "INPUT"
      || document.activeElement?.tagName === "TEXTAREA"
      || document.activeElement?.isContentEditable;

    const mod = e.ctrlKey || e.metaKey;

    // Escape is evaluated FIRST so no guard below can ever block it. Its
    // precedence is unchanged: shortcuts overlay, then drawer, then modal.
    if (e.key === "Escape") {
      if (!shortcutsOverlay.hidden) { shortcutsOverlay.hidden = true; return; }
      if (settingsDrawerEl.classList.contains("drawer-open")) {
        closeDrawer(settingsDrawerEl);
        // 2.4.3: hand focus back to the settings trigger.
        document.getElementById("settings-btn")?.focus();
        return;
      }
      const escOverlay = document.querySelector(".modal-overlay");
      if (escOverlay?._reject) escOverlay._reject();
      return;
    }

    // Is something modal on screen? Shortcuts that OPEN a surface or act
    // destructively must be inert while it is, or they stack a second overlay on
    // the first, or switch conversation mid-rename.
    //
    // This is deliberately NOT a single guard above the branch table. Ctrl+, and
    // ? are TOGGLES: each closes its own surface. A blanket guard would make the
    // drawer and the shortcuts overlay openable and then un-closable by the very
    // key that opened them — two new dead ends in place of one bug.
    const overlayOpen =
      !shortcutsOverlay.hidden ||
      settingsDrawerEl.classList.contains("drawer-open") ||
      !!document.querySelector(".modal-overlay");

    // Ctrl+Shift+N — new conversation
    if (mod && e.shiftKey && e.key === "N") {
      if (overlayOpen) return;
      e.preventDefault();
      createConversation().then((conv) => switchToConversation(conv.id, chatPane, composerRoot));
      return;
    }
    // Ctrl+` — terminal panel
    if (mod && e.key === "`") {
      if (overlayOpen) return;
      e.preventDefault();
      if (localStorage.getItem("gca_feat_terminal") === "1") {
        import("./ui/terminal.js").then(m => m.openTerminal()).catch(() => {});
      }
      return;
    }
    // Ctrl+Shift+T — template picker
    if (mod && e.shiftKey && e.key === "T") {
      if (overlayOpen) return;
      e.preventDefault();
      openTemplatePicker({ onSelect: (text) => { composerRoot.setText?.(text); } });
      return;
    }
    // Ctrl+, — toggle settings
    if (mod && e.key === ",") {
      e.preventDefault();
      if (settingsDrawerEl.classList.contains("drawer-open")) closeDrawer(settingsDrawerEl);
      else openDrawer(settingsDrawerEl);
      return;
    }
    // Ctrl+B — toggle right sidebar
    if (mod && e.key === "b") {
      if (overlayOpen) return;
      e.preventDefault();
      setSidebarCollapsed(!rightSidebarEl.classList.contains("sb-collapsed"), true);
      return;
    }
    // Ctrl+E — export
    if (mod && e.key === "e" && !inInput) {
      if (overlayOpen) return;
      e.preventDefault();
      chatPane.exportConversation();
      return;
    }
    // / — focus search (when not in input)
    if (e.key === "/" && !inInput) {
      e.preventDefault();
      if (sidebarConvsRoot?.focusSearch) sidebarConvsRoot.focusSearch();
      return;
    }
    // ? — shortcuts overlay (when not in input)
    if (e.key === "?" && !inInput) {
      shortcutsOverlay.hidden = !shortcutsOverlay.hidden;
      return;
    }
  });
}

boot().then(() => maybeShowOnboarding()).catch((err) => {
  // P2-B E2: boot() must never end as an unhandled promise rejection. If the
  // app could not even get far enough to render the composer (the WS catch in
  // switchToConversation shows its own retry UI), say so visibly.
  const app = document.getElementById("app");
  if (app && !app.querySelector("#composer") && !app.querySelector("#chat-retry-btn")) {
    app.innerHTML = `<div id="boot-msg">Couldn't start GoClaudaddy: ${_esc(String(err?.message || err))}</div>`;
  }
});
