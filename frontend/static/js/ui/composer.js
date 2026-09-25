import { api } from "../api/http.js";
import { getState } from "../state/store.js";
import { setStreaming, loadConversations } from "../state/actions.js";
import { getTemplates } from "../api/template_cache.js";
import { interpolateTemplate, openTemplatePicker } from "./template_picker.js";
import { showErrorToast } from "./modal.js";
import { clearDraft, loadDraft, saveDraft } from "./composer_draft.js";
import { attachmentDownloadUrl, isImageName } from "./attachment_view.js";

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

function _escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _showAssessment({ level, summary, concerns = [] }) {
  return new Promise((resolve) => {
    // 2.4.3: hand focus back to whatever opened this assessment (the composer).
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    document.body.appendChild(overlay);

    const isDanger = level === "high";
    const levelColor = level === "high" ? "var(--rose)" : level === "medium" ? "var(--amber)" : "var(--emerald)";

    const box = document.createElement("div");
    box.className = "modal-box";
    box.innerHTML = `
      <h3 class="modal-title">Task Assessment</h3>
      <div class="assess-level" style="color:${levelColor}">${level.toUpperCase()} RISK</div>
      <p class="assess-summary">${_escHtml(summary)}</p>
      ${concerns.length ? `<ul class="assess-concerns">${concerns.map(c => `<li>${_escHtml(c)}</li>`).join("")}</ul>` : ""}
      <div class="modal-actions">
        <button class="modal-btn modal-cancel">Cancel</button>
        <button class="modal-btn modal-confirm${isDanger ? " danger" : ""}" id="assess-proceed">Send anyway</button>
      </div>
    `;
    overlay.appendChild(box);

    const close = (ok) => {
      overlay.remove();
      if (returnFocus?.isConnected) returnFocus.focus();
      resolve(ok);
    };
    box.querySelector(".modal-cancel").addEventListener("click", () => close(false));
    box.querySelector("#assess-proceed").addEventListener("click", () => close(true));
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(false); });
    box.addEventListener("keydown", (e) => {
      if (e.key === "Escape") close(false);
      if (e.key === "Enter") close(true);
    });
    box.querySelector("#assess-proceed").focus();
  });
}

function _escSuggest(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function mountComposer(root, socket, chatPane) {
  root.innerHTML = `
    <div id="attachment-strip"></div>
    <div id="composer-row">
      <button id="composer-attach" title="Attach file">📎</button>
      <textarea id="composer-input" rows="2" placeholder="Message GoClaudaddy… (drag files, paste image, Enter to send)" aria-label="Message GoClaudaddy"></textarea>
      <button id="composer-send">Send</button>
      <button id="composer-stop" hidden>■ Stop</button>
    </div>
    <div id="composer-footer">
      <span id="char-counter"></span>
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
        <input class="suggest-search" placeholder="Filter templates… (↑↓ navigate, Enter select, Esc close)" aria-label="Filter templates" value="${_escSuggest(query)}">
        <div class="suggest-list">
          ${list.length
          ? list.map((t, i) => `
                <div class="suggest-item${i === activeIdx ? " suggest-active" : ""}" data-idx="${i}">
                  <span class="suggest-title">${_escSuggest(t.title)}</span>
                  <span class="suggest-cat">${_escSuggest(t.category)}</span>
                </div>`).join("")
          : `<div class="suggest-empty">No templates match</div>`}
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
  }

  function updateCharCounter() {
    const len = input.value.length;
    charCounter.textContent = len > 50 ? `${len} chars` : "";
    charCounter.classList.toggle("char-warn", len > 4000);
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
      const dot = /[.!?]$/.test(msg) ? "" : ".";
      showErrorToast(`Couldn't attach "${file.name}": ${msg}${dot}${sizeHint}`);
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
      input.value = "";
      charCounter.textContent = "";
      input.style.height = "";
      clearDraft(getState().activeConversationId);
      return true;
    }

    // Assessment gate (feature flag + heuristic)
    if (text && localStorage.getItem("gca_feat_assess") === "1" && _shouldAssess(text)) {
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
    showErrorToast(`Something went wrong sending that: ${err?.message || err}`);

  sendBtn.addEventListener("click", () => send().catch(_sendUnexpected));
  stopBtn.addEventListener("click", stop);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send().catch(_sendUnexpected);
      return;
    }
    // Ctrl+K — template autosuggest
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
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
