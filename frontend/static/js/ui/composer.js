import { api } from "../api/http.js";
import { getState, subscribe } from "../state/store.js";
import { setStreaming, loadConversations, setPermissionMode } from "../state/actions.js";
import { getTemplates } from "../api/template_cache.js";
import { interpolateTemplate, openTemplatePicker } from "./template_picker.js";
import { showErrorToast, showDialog } from "./modal.js";
import { clearDraft, loadDraft, saveDraft } from "./composer_draft.js";
import { attachmentDownloadUrl, isImageName } from "./attachment_view.js";
import { endSentence } from "./sentence.js";
import * as storage from "../state/storage.js";
import { escapeHtml } from "../render/escape.js";
import { setConnState } from "./conn_state.js";

const _RISKY = /\b(delete|drop|remove|wipe|destroy|format|truncate|uninstall|overwrite|migrate|deploy|execute|rm\s+-rf)\b/i;

function _shouldAssess(text) {
  if (!text.trim()) return false;
  return text.trim().split(/\s+/).length > 50 || _RISKY.test(text);
}

function _showToast(msg, ms = 4000) {
  const t = document.createElement("div");
  t.className = "gca-toast";
  // 4.1.3: transient notices are status messages.
  t.setAttribute("role", "status");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function _showAssessment({ level, summary, concerns = [] }) {
  const lvl = typeof level === "string" && ["low", "medium", "high"].includes(level) ? level : "unknown";
  return new Promise((resolve) => {
    const isDanger = level === "high";
    const levelColor = level === "high" ? "var(--rose)" : level === "medium" ? "var(--amber)" : "var(--emerald)";
    let dialog = null;

    function settle(ok, reason) {
      resolve(ok);
      dialog?.close({ reason });
    }

    dialog = showDialog({
      title: "Task Assessment",
      body: `
        <div class="assess-level" style="color:${levelColor}">${escapeHtml(lvl[0].toUpperCase() + lvl.slice(1))} risk</div>
        <p class="assess-summary">${escapeHtml(summary)}</p>
        ${concerns.length ? `<ul class="assess-concerns">${concerns.map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ul>` : ""}
      `,
      actions: [[
        {
          className: "modal-btn modal-cancel",
          label: "Cancel",
          onClick: () => settle(false, "cancel"),
        },
        {
          className: `modal-btn modal-confirm${isDanger ? " danger" : ""}`,
          id: "assess-proceed",
          label: "Send anyway",
          onClick: () => settle(true, "proceed"),
        },
      ]],
      trap: false,
      closeOnBackdrop: true,
      returnFocus: true,
      onClose: (reason) => {
        if (reason === "backdrop" || reason === "escape") resolve(false);
      },
    });

    dialog.box.addEventListener("keydown", (e) => {
      if (e.key === "Escape") settle(false, "escape");
      if (e.key === "Enter") settle(true, "proceed");
    });
    dialog.box.querySelector("#assess-proceed").focus();
  });
}

// --- J5: model/effort + permission controls ---------------------------------
// Effort labels and levels are fixed by SPEC section B. "" is Default and
// sends no --effort flag; xhigh is accepted by the API but never offered here.
const EFFORT_LABELS = { "": "Default", low: "Quick", medium: "Balanced", high: "Thorough", max: "Maximum" };
const SIMPLE_PERMISSIONS = ["manual", "acceptEdits"];
// /api/config has no option for auto/dontAsk, so their labels are local (YC).
// They must match main.js's PERM_LABELS fallback word for word: the header and
// the chip are the same picker (J1 rule, D2 chip_header_words_*).
const PERM_FALLBACK_LABELS = {
  manual: "Ask before acting",
  acceptEdits: "Auto-approve edits",
  plan: "Plan only",
  bypassPermissions: "Bypass all prompts",
  auto: "Full auto",
  dontAsk: "Never ask",
};
// The labels main.js paints in the header (its own PERM_LABELS), kept so a
// Simple coercion can find and replace the old text without re-rendering the
// whole subtitle.
const HEADER_PERM_LABELS = {
  manual: "Ask before acting",
  acceptEdits: "Auto-approve edits",
  plan: "Plan only",
  bypassPermissions: "Bypass all prompts",
  auto: "Full auto",
  dontAsk: "Never ask",
};

// At most one composer menu is open at a time, app-wide. Module scope so a
// remount can clean up the previous mount's document listener.
let _activeMenu = null;
let _menuOutside = null;
// The permission-effective live region subscribes to the store; module scope so
// a remount drops the previous mount's listener instead of stacking one per
// conversation switch (same pattern as the removed drawer's _unsubscribeSettings).
let _unsubscribeEffective = null;

function _closeMenu({ focus = false } = {}) {
  if (!_activeMenu) return;
  const { button, el } = _activeMenu;
  _activeMenu = null;
  if (_menuOutside) {
    document.removeEventListener("mousedown", _menuOutside, true);
    _menuOutside = null;
  }
  el.remove();
  button.setAttribute("aria-expanded", "false");
  if (focus) button.focus();
}

function _openMenu(button, items, onPick) {
  const wasSame = _activeMenu?.button === button;
  _closeMenu();
  if (wasSame) return; // re-click (or ArrowDown) on the open button closes it
  const root = button.closest("#composer");
  if (!root) return;

  const el = document.createElement("div");
  el.className = "composer-menu";
  el.setAttribute("role", "menu");
  items.forEach((item) => {
    const itemEl = document.createElement("button");
    itemEl.type = "button";
    itemEl.setAttribute("role", "menuitemradio");
    itemEl.dataset.kind = item.kind;
    itemEl.dataset.value = item.value;
    itemEl.setAttribute("aria-checked", item.checked ? "true" : "false");
    itemEl.tabIndex = -1;
    itemEl.textContent = item.label;
    itemEl.addEventListener("click", () => {
      _closeMenu({ focus: true });
      onPick(item.kind, item.value);
    });
    el.appendChild(itemEl);
  });
  root.appendChild(el);

  // Sit above the dock, inside the viewport: clamp to the composer box (which
  // is itself inside the viewport) instead of the button's own left edge.
  const rootRect = root.getBoundingClientRect();
  const btnRect = button.getBoundingClientRect();
  const maxLeft = Math.max(0, root.clientWidth - el.offsetWidth);
  el.style.left = `${Math.max(0, Math.min(btnRect.left - rootRect.left, maxLeft))}px`;

  button.setAttribute("aria-expanded", "true");
  _activeMenu = { button, el };

  const checked = el.querySelector('[aria-checked="true"]') || el.querySelector('[role="menuitemradio"]');
  checked?.focus();

  el.addEventListener("keydown", (e) => {
    const opts = Array.from(el.querySelectorAll('[role="menuitemradio"]'));
    const idx = opts.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      opts[Math.min(idx + 1, opts.length - 1)]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      opts[Math.max(idx - 1, 0)]?.focus();
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      document.activeElement?.click?.();
    } else if (e.key === "Escape") {
      e.preventDefault();
      _closeMenu({ focus: true });
    }
  });

  const outside = (e) => {
    if (!_activeMenu) return;
    if (_activeMenu.el.contains(e.target) || e.target === _activeMenu.button) return;
    _closeMenu();
  };
  _menuOutside = outside;
  document.addEventListener("mousedown", outside, true);
}

export function mountComposer(root, socket, chatPane, opts = {}) {
  // A previous mount may have left a menu open (and its document mousedown
  // listener attached). Drop both before this mount rebuilds the composer.
  _closeMenu();
  root.innerHTML = `
    <div id="attachment-strip"></div>
    <div id="composer-row">
      <button id="composer-attach" type="button" title="Attach file" aria-label="Attach file">📎</button>
      <textarea id="composer-input" rows="2" placeholder="Message GoClaudaddy… (drag files, paste image, Enter to send)" aria-label="Message GoClaudaddy"></textarea>
      <button id="composer-model-effort" type="button" aria-haspopup="menu" aria-expanded="false">Model</button>
      <button id="permission-chip" type="button" aria-haspopup="menu" aria-expanded="false">Ask before acting</button>
      <button id="composer-send" aria-disabled="true" aria-describedby="composer-empty-hint">Send</button>
      <span id="composer-empty-hint" class="sr-only">Type a message to send.</span>
      <button id="composer-stop" hidden>■ Stop</button>
    </div>
    <div id="composer-footer">
      <span id="char-counter"></span>
      <span id="permission-effective" class="composer-effective" aria-live="polite"></span>
      <span id="composer-hint">Enter to send · Shift+Enter for newline</span>
    </div>
    <input type="file" id="composer-file-input" multiple hidden aria-label="Attach file">
  `;
  const input = root.querySelector("#composer-input");
  const sendBtn = root.querySelector("#composer-send");
  const stopBtn = root.querySelector("#composer-stop");
  const strip = root.querySelector("#attachment-strip");
  const fileInput = root.querySelector("#composer-file-input");
  const attachBtn = root.querySelector("#composer-attach");
  const charCounter = root.querySelector("#char-counter");

  // --- J5: model/effort + permission controls -------------------------------
  const modelBtn = root.querySelector("#composer-model-effort");
  const permBtn = root.querySelector("#permission-chip");
  let config = { models: [], permission_modes: [], permission_mode_options: [] };

  const _activeConv = () =>
    (getState().conversations || []).find((c) => c.id === getState().activeConversationId) || null;
  const conv = _activeConv();
  let currentModel = conv?.model || "";
  let currentEffort = conv?.effort || "";
  let currentPermission = conv?.permission_mode || "manual";

  const _uiAdvanced = () => storage.getItem("gca_ui_mode") === "advanced";
  const _modelLabel = (id) => (config.models || []).find((m) => m.value === id)?.label || id || "Model";
  const _permLabel = (mode) =>
    (config.permission_mode_options || []).find((o) => o.value === mode)?.label
    || PERM_FALLBACK_LABELS[mode] || mode;
  const _rememberConv = (patch) => {
    const c = _activeConv();
    if (c) Object.assign(c, patch);
  };

  function _setHeaderModel(label) {
    const el = document.querySelector("#conv-subtitle .sub-model");
    if (el) el.textContent = label;
  }

  // main.js owns the header; after a Simple coercion we only swap the old
  // permission text node so no "Bypass" label survives in Simple (Ruling 11).
  function _replaceHeaderText(oldText, newText) {
    const sub = document.querySelector("#conv-subtitle");
    if (!sub || !oldText || oldText === newText) return;
    const walker = document.createTreeWalker(sub, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.nodeValue && node.nodeValue.includes(oldText)) {
        node.nodeValue = node.nodeValue.replace(oldText, newText);
      }
    }
  }

  function _renderModelEffort() {
    const label = _modelLabel(currentModel);
    modelBtn.textContent = currentEffort
      ? `${label} · ${EFFORT_LABELS[currentEffort] || currentEffort}`
      : label;
    _setHeaderModel(label);
  }

  function _renderPermission() {
    permBtn.textContent = _permLabel(currentPermission);
  }

  // J5/D2: the CLI-reported effective mode moved out of the drawer with the
  // select. Same three strings the drawer rendered (empty -> "takes effect on
  // your next message"; ok -> "Claude is using <label>."; mismatch -> warning).
  const permissionEffective = root.querySelector("#permission-effective");
  function _renderPermissionEffective() {
    if (!permissionEffective) return;
    const info = getState().permissionMode;
    // Only a mismatch is announced: an ok echo is spoken by nobody (J2 allows one announcement per ordinary turn).
    permissionEffective.setAttribute("aria-live", info?.requested && !info.ok ? "polite" : "off");
    if (!info?.requested) {
      permissionEffective.textContent = "takes effect on your next message";
      return;
    }
    const requestedLabel = _permLabel(info.requested);
    if (info.ok) {
      permissionEffective.textContent = `Claude is using ${requestedLabel}.`;
    } else {
      permissionEffective.textContent = `Warning: Claude is using ${info.actual ?? "unknown"} instead of ${requestedLabel}.`;
    }
  }
  if (_unsubscribeEffective) _unsubscribeEffective();
  _unsubscribeEffective = subscribe(_renderPermissionEffective);
  _renderPermissionEffective();

  const _effortValues = () =>
    _uiAdvanced() ? ["", "low", "medium", "high", "max"] : ["", "low", "medium", "high"];
  const _permissionModes = () => {
    const all = (config.permission_modes || []).length ? config.permission_modes : SIMPLE_PERMISSIONS;
    return _uiAdvanced() ? all : SIMPLE_PERMISSIONS;
  };

  function _menuItems(button) {
    if (button === permBtn) {
      return _permissionModes().map((mode) => ({
        kind: "permission", value: mode, label: _permLabel(mode), checked: mode === currentPermission,
      }));
    }
    return [
      ...(config.models || []).map((m) => ({
        kind: "model", value: m.value, label: m.label, checked: m.value === currentModel,
      })),
      ..._effortValues().map((v) => ({
        kind: "effort", value: v, label: EFFORT_LABELS[v], checked: v === currentEffort,
      })),
    ];
  }

  async function _pick(kind, value) {
    const convId = getState().activeConversationId;
    if (!convId) return;
    try {
      if (kind === "permission") {
        await api.updateConversationSettings(convId, { permission_mode: value });
        currentPermission = value;
        _rememberConv({ permission_mode: value });
        _renderPermission();
        setPermissionMode(null);
        opts.onChange?.({ permission_mode: value });
      } else if (kind === "model") {
        await api.updateConversationSettings(convId, { model: value });
        currentModel = value;
        _rememberConv({ model: value });
        _renderModelEffort();
        opts.onChange?.({ model: value });
      } else {
        await api.updateConversationSettings(
          convId, value === "" ? { clear_effort: true } : { effort: value }
        );
        currentEffort = value;
        _rememberConv({ effort: value || null });
        _renderModelEffort();
        opts.onChange?.({ effort: value || null });
      }
    } catch {
      showErrorToast("Couldn't save that choice — try again.");
    }
  }

  const _toggleMenu = (button) => _openMenu(button, _menuItems(button), _pick);
  modelBtn.addEventListener("click", () => _toggleMenu(modelBtn));
  permBtn.addEventListener("click", () => _toggleMenu(permBtn));
  modelBtn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); _toggleMenu(modelBtn); }
  });
  permBtn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); _toggleMenu(permBtn); }
  });

  // Simple coercion (Ruling 11): a stored bypass/plan/auto/dontAsk never shows
  // in Simple. The chip reads "Ask before acting" immediately and the PATCH
  // lands before any turn; Advanced never coerces.
  if (!_uiAdvanced() && conv?.permission_mode && !SIMPLE_PERMISSIONS.includes(conv.permission_mode)) {
    currentPermission = "manual";
    const coercedFrom = conv.permission_mode;
    api.updateConversationSettings(conv.id, { permission_mode: "manual" })
      .then(() => {
        _rememberConv({ permission_mode: "manual" });
        _replaceHeaderText(HEADER_PERM_LABELS[coercedFrom] || coercedFrom, _permLabel("manual"));
      })
      .catch(() => { /* chip already reads Ask before acting; the next turn re-coerces */ });
  }

  modelBtn.textContent = currentModel || "Model";
  _renderPermission();
  api.getConfig()
    .then((cfg) => {
      if (cfg && typeof cfg === "object") config = cfg;
      if (!currentModel) currentModel = config.default_model || config.models?.[0]?.value || "";
      _renderModelEffort();
      _renderPermission();
    })
    .catch(() => { _renderModelEffort(); _renderPermission(); });

  const pending = []; // {id, name}
  let isFirstMessage = true;
  const _history = []; // sent messages, oldest→newest
  let _histIdx = -1;   // -1 = live (not browsing)

  // --- Autosuggest ---
  let suggestEl = null;

  function closeSuggest() {
    if (suggestEl) { suggestEl.remove(); suggestEl = null; }
    document.removeEventListener("mousedown", _onSuggestOutside);
  }

  function _onSuggestOutside(e) {
    if (suggestEl && !suggestEl.contains(e.target) && e.target !== input) closeSuggest();
  }

  async function openSuggest() {
    closeSuggest();
    let templates;
    try { templates = await getTemplates(); } catch { templates = []; }
    if (!templates.length) return;

    suggestEl = document.createElement("div");
    suggestEl.className = "suggest-dropdown";

    let query = "";
    let activeIdx = 0;

    function _filtered() {
      const q = query.toLowerCase();
      return (q
        ? templates.filter(t => t.title.toLowerCase().includes(q) || t.category.includes(q))
        : templates
      ).slice(0, 8);
    }

    function _renderSuggest() {
      const list = _filtered();
      suggestEl.innerHTML = `
        <input class="suggest-search" placeholder="Filter templates… (↑↓ navigate, Enter select, Esc close)" aria-label="Filter templates" value="${escapeHtml(query)}">
        <div class="suggest-list">
          ${list.length
          ? list.map((t, i) => `
                <div class="suggest-item${i === activeIdx ? " suggest-active" : ""}" data-idx="${i}">
                  <span class="suggest-title">${escapeHtml(t.title)}</span>
                  <span class="suggest-cat">${escapeHtml(t.category)}</span>
                </div>`).join("")
          : `<div class="suggest-empty">No templates match — clear the filter or press Esc.</div>`}
        </div>
      `;

      const searchEl = suggestEl.querySelector(".suggest-search");
      searchEl.focus();
      searchEl.setSelectionRange(query.length, query.length);

      searchEl.addEventListener("input", (e) => { query = e.target.value; activeIdx = 0; _renderSuggest(); });
      searchEl.addEventListener("keydown", (e) => {
        const items = _filtered();
        if (e.key === "ArrowDown") { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, items.length - 1); _renderSuggest(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); _renderSuggest(); }
        else if (e.key === "Enter") { e.preventDefault(); if (items[activeIdx]) _selectSuggest(items[activeIdx]); }
        else if (e.key === "Escape") { e.preventDefault(); closeSuggest(); input.focus(); }
      });

      suggestEl.querySelectorAll(".suggest-item").forEach(el => {
        el.addEventListener("mousedown", (e) => {
          e.preventDefault();
          const t = _filtered()[parseInt(el.dataset.idx, 10)];
          if (t) _selectSuggest(t);
        });
      });
    }

    function _selectSuggest(t) {
      closeSuggest();
      interpolateTemplate(t.body, (text) => root.setText(text));
    }

    document.addEventListener("mousedown", _onSuggestOutside);
    root.appendChild(suggestEl);
    _renderSuggest();
  }

  function renderStrip() {
    strip.innerHTML = "";
    pending.forEach((att) => {
      const chip = document.createElement("span");
      chip.className = "attachment-chip";
      const nameEl = document.createElement("span");
      nameEl.className = "attachment-chip-name";
      nameEl.textContent = att.name;
      if (isImageName(att.name)) {
        // F2: thumbnail for image attachments. alt = filename, per the brief.
        // src is the download endpoint — file bytes are never inlined.
        const img = document.createElement("img");
        img.className = "attachment-thumb";
        img.alt = att.name;
        img.src = attachmentDownloadUrl(att.id);
        img.addEventListener("error", () => {
          // A missing/deleted file must degrade to the filename chip, not a
          // broken <img> that hides what was attached.
          img.remove();
          chip.prepend(nameEl);
        });
        chip.appendChild(img);
      } else {
        chip.appendChild(nameEl);
      }
      const rm = document.createElement("button");
      rm.textContent = "✕";
      rm.addEventListener("click", () => {
        pending.splice(pending.indexOf(att), 1);
        renderStrip();
        api.deleteAttachment(att.id).catch(() => { });
      });
      chip.appendChild(rm);
      strip.appendChild(chip);
    });
    _syncSendDisabled();
  }

  function updateCharCounter() {
    const len = input.value.length;
    charCounter.textContent = len > 50 ? `${len} chars` : "";
    charCounter.classList.toggle("char-warn", len > 4000);
  }

  // C4: Send is "disabled" to assistive tech whenever there is nothing to send.
  // aria-disabled (not `disabled`) keeps the button focusable and Enter working;
  // send()'s own guard still refuses an empty send.
  function _syncSendDisabled() {
    const empty = input.value.trim() === "" && pending.length === 0;
    sendBtn.setAttribute("aria-disabled", empty ? "true" : "false");
  }

  // F1: restore the per-conversation draft this composer was remounted for.
  // mountComposer runs on every conversation switch (main.js), so loading here
  // covers both first load and switching — and it is also what fixes the plan
  // ~1521 bug where text typed right after New Chat was destroyed by the
  // remount: the keystrokes were already saved under the NEW conversation id,
  // so the fresh composer reads them straight back.
  const initialDraft = loadDraft(getState().activeConversationId);
  if (initialDraft) {
    input.value = initialDraft;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
    updateCharCounter();
  }
  _syncSendDisabled();

  async function uploadFile(file) {
    const conversationId = getState().activeConversationId;
    try {
      const att = await api.uploadAttachment(conversationId, file);
      pending.push({ id: att.id, name: att.original_name });
      renderStrip();
    } catch (e) {
      const msg = e?.message || "upload failed";
      // P2-B E6: "use a smaller file" only makes sense when the failure is
      // actually about size. A disk-full error must say the disk is full and
      // stop there.
      const sizeHint = /\b(size|larger?|limit|exceed|MB|GB)\b/i.test(msg)
        ? " Try again, or use a smaller file."
        : "";
      showErrorToast(`Couldn't attach "${file.name}": ${endSentence(msg)}${sizeHint}`);
    }
  }

  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    Array.from(fileInput.files || []).forEach(uploadFile);
    fileInput.value = "";
  });
  input.addEventListener("dragover", (e) => e.preventDefault());
  input.addEventListener("drop", (e) => {
    e.preventDefault();
    Array.from(e.dataTransfer.files || []).forEach(uploadFile);
  });
  input.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length) {
      e.preventDefault();
      files.forEach(uploadFile);
    }
  });
  input.addEventListener("input", () => {
    updateCharCounter();
    _syncSendDisabled();
    // F1: autosave the draft for the conversation this composer belongs to.
    // Synchronous on purpose — a debounce would lose the last keystrokes when
    // the user clicks another conversation immediately after typing.
    saveDraft(getState().activeConversationId, input.value);
  });

  // Template button in composer footer (added here, not in main.js boot, so footer exists)
  const composerFooter = root.querySelector("#composer-footer");
  if (composerFooter && !composerFooter.querySelector("#template-btn")) {
    const tplBtn = document.createElement("button");
    tplBtn.id = "template-btn";
    tplBtn.className = "composer-footer-btn";
    tplBtn.title = "Templates (Ctrl+Shift+T)";
    tplBtn.textContent = "⚡ Templates";
    tplBtn.addEventListener("click", () => {
      openTemplatePicker({ onSelect: (text) => { root.setText?.(text); } });
    });
    composerFooter.prepend(tplBtn);
  }

  // Allow external code to set text (retry / quick chips) and send
  root.setText = (text) => {
    input.value = text;
    input.focus();
    // Dispatch rather than calling updateCharCounter() directly: the input
    // listener is the one place that keeps the counter AND the F1 autosaved
    // draft in sync, so prefill from quick chips / templates / retry persists
    // like typed text instead of silently vanishing on the next remount.
    input.dispatchEvent(new Event("input"));
    // Auto-resize
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  };

  root.send = send;

  /* Put the caret in the composer so a new or switched-to conversation is
     immediately typeable. Without this the user has to click the box first,
     which was the original complaint: "when starting a new conversation having
     to enter ask a question in the chatbox is bad."

     mountComposer runs on every conversation switch (main.js), so this covers
     both first load and switching — matching how Slack, Discord and Claude.ai
     all behave.

     Guarded, because stealing focus is rude in three cases:
       - an open <dialog> (onboarding tour, modals, approval prompt) owns focus
       - the template-picker overlay is open
       - the user is already typing somewhere else, e.g. the conversation search
     rAF defers until after this mount's layout, so focus() cannot cause a
     scroll jump mid-render. */
  requestAnimationFrame(() => {
    // Selector verified against the real markup: the onboarding tour is a plain
    // div.ob-overlay containing [role="dialog"], NOT a native <dialog>. An
    // earlier version of this guard looked for ".onboarding-overlay", a class
    // that exists nowhere in the codebase, so it silently never matched.
    if (document.querySelector('dialog[open], [role="dialog"], .ob-overlay, .tp-overlay')) return;
    const ae = document.activeElement;
    if (ae && ae !== document.body && ae !== input &&
      (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA" || ae.isContentEditable)) return;
    input.focus({ preventScroll: true });
  });

  async function send({ regenerate = false } = {}) {
    const text = input.value.trim();
    if ((!text && pending.length === 0) || getState().streaming) return false;

    if (regenerate) {
      // Re-answer the question already in the transcript. It must NOT be appended
      // to it a second time — doing exactly that produced [user][user][assistant] —
      // and the server re-reads the stored question, so this text is not the
      // authoritative copy. N10's mislabelled catch is gone with it: there is no
      // delete round trip left to blame a send failure on (task 2.5 owns the
      // remaining error surfacing).
      chatPane.resetForNewTurn();
      setStreaming(true);
      sendBtn.hidden = true;
      stopBtn.hidden = false;
      try {
        socket.send(text, { regenerate: true });
      } catch (err) {
        // No optimistic bubble on this path — regenerate never appends one —
        // but the stuck-streaming state is identical.
        _undoFailedSend(err, null, text);
        return false;
      }
      setConnState("sending");
      input.value = "";
      charCounter.textContent = "";
      input.style.height = "";
      clearDraft(getState().activeConversationId);
      _syncSendDisabled();
      return true;
    }

    // Assessment gate (feature flag + heuristic)
    if (text && storage.getItem("gca_feat_assess") === "1" && _shouldAssess(text)) {
      const convId = getState().activeConversationId;
      let assessment;
      try {
        assessment = await Promise.race([
          api.assessMessage(convId, text),
          new Promise((res) => setTimeout(() => res({ timed_out: true }), 9000)),
        ]);
      } catch {
        assessment = { timed_out: true };
      }

      if (assessment.timed_out) {
        _showToast("Couldn't evaluate — proceeding");
      } else if (assessment.level !== "low") {
        const proceed = await _showAssessment(assessment);
        if (!proceed) return false;
      }
    }

    if (text) { _history.push(text); _histIdx = -1; }
    const optimisticBubble = chatPane.appendUserMessage(text || "(attachment)");
    chatPane.resetForNewTurn();
    setStreaming(true);
    sendBtn.hidden = true;
    stopBtn.hidden = false;
    try {
      socket.send(text, { attachment_ids: pending.map((a) => a.id) });
    } catch (err) {
      _undoFailedSend(err, optimisticBubble, text);
      return false;
    }
    setConnState("sending");
    input.value = "";
    charCounter.textContent = "";
    input.style.height = "";
    clearDraft(getState().activeConversationId);
    pending.length = 0;
    renderStrip();
    return true;
  }

  /**
   * Undo an optimistic send. N2: the UI flips to streaming BEFORE socket.send(),
   * so a refusal used to leave a bubble for a message that never left, plus
   * `streaming` stuck true — which makes send() early-return FOREVER at the top
   * of this function — and a live Stop button. The user's only escape was a
   * reload.
   *
   * @param {Error} err       what socket.send() refused with
   * @param {Element} [bubble] the optimistic bubble to remove, if one was added
   * @param {string} [text]    the message, restored to the input so it is not lost
   */
  function _undoFailedSend(err, bubble, text) {
    bubble?.remove();
    // Also drop the assistant placeholder. resetForNewTurn() paints "Thinking…"
    // before the send is attempted, and no `done` can ever arrive to clear it
    // for a message the server never received.
    chatPane.abortCurrentTurn?.();
    setStreaming(false);
    sendBtn.hidden = false;
    stopBtn.hidden = true;
    if (text) {
      input.value = text;
      input.dispatchEvent(new Event("input"));
    }
    showErrorToast(
      `${err?.message || "Message not sent."} Your message is back in the box — try again.`
    );
  }

  function stop() {
    // 2.9 made stop() report refusal instead of throwing. A refusal means the
    // socket is already gone, so the turn cannot still be running however the
    // UI looks — restore the composer rather than leaving a Stop button that
    // does nothing.
    if (socket.stop() === false) {
      setStreaming(false);
      sendBtn.hidden = false;
      stopBtn.hidden = true;
    }
  }

  // N1. `done` below is the ONLY event that restores this composer, and a socket
  // that dies mid-turn means no `done` can ever arrive — the turn is over while
  // `streaming` stays true, send() early-returns forever, and the user's only
  // recovery is a page reload.
  //
  // Deliberately narrow, per the brief: Stop and `error` do NOT strand the
  // composer. chat_socket.py:59-65 always follows `stopped` with `done`, and
  // `error` is followed by `done` at :231-232. `done` is the shared reset and it
  // works. This fixes only the path where `done` cannot arrive at all.
  //
  // Idempotent, so it neither fights the `done` path nor leaves stale state on
  // the freshly mounted composer when main.js:65 closes the old socket during a
  // deliberate conversation switch.
  socket.on("_close", () => {
    sendBtn.hidden = false;
    stopBtn.hidden = true;
    setStreaming(false);
  });

  socket.on("done", async () => {
    sendBtn.hidden = false;
    stopBtn.hidden = true;
    // Auto-title on first completed turn
    if (isFirstMessage) {
      isFirstMessage = false;
      const convId = getState().activeConversationId;
      if (convId) {
        try {
          await api.autoTitleConversation(convId);
          await loadConversations();
        } catch { }
      }
    }
  });

  // send() now handles a REFUSED send itself (_undoFailedSend). These outer
  // catches therefore no longer hide that case — but a bare `catch(() => {})`
  // would still swallow anything unexpected, which is the same silence 2.4
  // removed from the rest of the app. Report instead.
  const _sendUnexpected = (err) =>
    showErrorToast("Something went wrong sending that. Your message is back in the box — try again.");

  sendBtn.addEventListener("click", () => send().catch(_sendUnexpected));
  stopBtn.addEventListener("click", stop);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send().catch(_sendUnexpected);
      return;
    }
    // Ctrl+K — template autosuggest
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openSuggest();
      return;
    }
    // ↑ — recall previous sent message (only when cursor is at start / input is empty)
    if (e.key === "ArrowUp" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      const atStart = input.selectionStart === 0 && input.selectionEnd === 0;
      if ((input.value === "" || atStart) && _history.length > 0) {
        e.preventDefault();
        if (_histIdx === -1) _histIdx = _history.length - 1;
        else if (_histIdx > 0) _histIdx--;
        input.value = _history[_histIdx];
        input.setSelectionRange(0, 0);
        updateCharCounter();
        return;
      }
    }
    // ↓ — go forward in history (or back to live)
    if (e.key === "ArrowDown" && !e.shiftKey && !e.ctrlKey && !e.metaKey && _histIdx !== -1) {
      e.preventDefault();
      if (_histIdx < _history.length - 1) {
        _histIdx++;
        input.value = _history[_histIdx];
      } else {
        _histIdx = -1;
        input.value = "";
      }
      updateCharCounter();
      return;
    }
    // Any other key while browsing history resets index
    if (_histIdx !== -1 && e.key.length === 1) _histIdx = -1;
    // Auto-grow
    setTimeout(() => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 200) + "px";
    }, 0);
  });

  // Reset first-message flag when this composer is remounted for a new conversation
  root._resetFirstMessage = () => { isFirstMessage = true; };
}
