import { render } from "../render/content_registry.js";
import { highlightCodeBlocks } from "../render/markdown.js";
import { setStreaming } from "../state/actions.js";
import { api } from "../api/http.js";
import { getTemplates } from "../api/template_cache.js";
import { interpolateTemplate, CAT_LABELS } from "./template_picker.js";
import { showErrorToast, showDialog } from "./modal.js";
import { attachmentDownloadUrl, isImageName } from "./attachment_view.js";
import * as storage from "../state/storage.js";
import { escapeHtml } from "../render/escape.js";

const STATUS_LABEL = { done: "Done", error: "Error", stopped: "Stopped", timeout: "Timed out" };
const TOOL_LABELS = {
  Read: "Read file",
  Write: "Write file",
  Edit: "Edit file",
  MultiEdit: "Edit files",
  Bash: "Run command",
  Grep: "Search files",
  Glob: "Find files",
  LS: "List files",
  WebFetch: "Fetch web page",
  WebSearch: "Search the web",
  TodoWrite: "Update todo list",
  Task: "Run sub-agent",
};

export function mountChatPane(root, { onRetry, onExport, onComplete } = {}) {
  root.innerHTML = `
    <div id="chat-messages" class="glow-shell"></div>
    <div id="chat-status" class="chat-status" role="status" aria-live="polite"></div>
    <div id="reply-announce" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>
  `;
  const messagesEl = root.querySelector("#chat-messages");
  const statusEl = root.querySelector("#chat-status");
  const replyAnnounceEl = root.querySelector("#reply-announce");

  let currentAssistantEl = null;
  let currentBubbleEl = null;
  let currentThinkingEl = null;
  let currentMetaEl = null;
  let currentStripEl = null;
  let currentText = "";
  let currentThinking = "";
  let lastUserText = "";
  let conversationId = null;
  let resyncMarkerEl = null; // transient "completed while away" line — never persisted
  let msgStartTime = null;
  const _toolStartTimes = new Map(); // tool_use_id → Date.now()
  let _stripStartTime = 0;
  let _approvalModal = null; // live approval modal handle (countdown resync target)

  function setConversationId(id) { conversationId = id; }

  function _clearResyncMarker() {
    resyncMarkerEl?.remove();
    resyncMarkerEl = null;
  }

  function clear() {
    messagesEl.innerHTML = "";
    statusEl.textContent = "";
    resyncMarkerEl = null; // wiped along with messagesEl above
    currentAssistantEl = null;
    currentBubbleEl = null;
    currentThinkingEl = null;
    currentStripEl = null;
    currentText = "";
    currentThinking = "";
    lastUserText = "";
    _toolStartTimes.clear();
    _stripStartTime = 0;
  }

  function fillComposer(text) {
    const input = document.getElementById("composer-input");
    if (input) { input.value = text; input.focus(); }
  }

  function showEmptyState() {
    clear();
    messagesEl.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-glyph">◈</div>
        <h2 class="empty-state-title">GoClaudaddy</h2>
        <p class="empty-state-sub">What would you like to do today?</p>
        <div class="featured-templates" id="featured-templates" hidden>
          <h3 class="featured-heading">Start from a template</h3>
          <div class="featured-grid" id="featured-grid"></div>
          <button class="browse-all-link" id="browse-all-link">Browse all templates →</button>
        </div>
        <div class="quick-chips">
          <button class="quick-chip" data-text="Ask a question: ">Ask a question</button>
          <button class="quick-chip" data-text="Summarize this: ">Summarize something</button>
          <button class="quick-chip" data-text="Debug this error: ">Debug my code</button>
        </div>
      </div>
    `;
    messagesEl.querySelectorAll(".quick-chip").forEach((btn) => {
      btn.addEventListener("click", () => fillComposer(btn.dataset.text));
    });

    // Async load featured templates — fail silently
    getTemplates().then((templates) => {
      const featured = templates.slice(0, 6);
      if (!featured.length) return;
      const grid = messagesEl.querySelector("#featured-grid");
      const section = messagesEl.querySelector("#featured-templates");
      if (!grid || !section) return;
      // Templates are user-creatable through the template picker's CRUD, so
      // title/category/id are user-controlled and MUST be escaped. The category
      // is also concatenated into a class name, so it is additionally restricted
      // to a safe character set rather than only escaped.
      grid.innerHTML = featured.map(t => {
        const cat = String(t.category ?? "").replace(/[^a-zA-Z0-9_-]/g, "");
        return `
        <button class="featured-card" data-id="${escapeHtml(t.id)}" title="${escapeHtml(t.title)}">
          <span class="featured-card-title">${escapeHtml(t.title)}</span>
          <span class="featured-card-cat tp-cat-${cat}">${escapeHtml(CAT_LABELS[t.category] ?? t.category)}</span>
        </button>
      `;
      }).join("");
      grid.querySelectorAll(".featured-card").forEach((btn) => {
        const t = featured.find(x => x.id === btn.dataset.id);
        if (t) btn.addEventListener("click", () => interpolateTemplate(t.body, fillComposer));
      });
      section.hidden = false;

      const browseBtn = messagesEl.querySelector("#browse-all-link");
      if (browseBtn) browseBtn.addEventListener("click", () => {
        window._openTemplatePicker?.({ onSelect: fillComposer });
      });
    }).catch(() => { });
  }

  function fmtTime(d = new Date()) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function fmtWords(tokens) {
    if (!tokens) return "";
    const words = Math.round(tokens * 0.75);
    if (words < 1000) return `~${words}w`;
    return `~${(words / 1000).toFixed(1)}k w`;
  }

  function fmtTok(n) {
    return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
  }

  function tokMeta(inputTok, outputTok) {
    if (!outputTok) return "";
    if (inputTok) return ` · <span class="tok-in">↑${fmtTok(inputTok)}</span> <span class="tok-out">↓${fmtTok(outputTok)}</span>`;
    return ` · ${fmtWords(outputTok)}`;
  }

  function buildMessageEl(role) {
    const div = document.createElement("div");
    div.className = `msg msg-${role} glow`;
    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    div.appendChild(bubble);
    div.appendChild(meta);
    div.appendChild(actions);
    return { div, bubble, meta, actions };
  }

  /** Copy text to the clipboard, with a legacy fallback for contexts where
   *  navigator.clipboard is unavailable or permission is denied (private
   *  mode, older WebViews). Returns true when a copy actually happened. */
  async function _copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        return ok;
      } catch {
        return false;
      }
    }
  }

  function _addCopyButton(containerEl, getText) {
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.title = "Copy";
    btn.textContent = "⎘";
    btn.addEventListener("click", async () => {
      if (!(await _copyToClipboard(getText()))) return;
      btn.textContent = "✓";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = "⎘"; btn.classList.remove("copied"); }, 1500);
    });
    return btn;
  }

  function _makeCodeCopyButton(pre, label) {
    const btn = document.createElement("button");
    btn.className = "code-copy-btn";
    btn.textContent = label;
    btn.title = "Copy code";
    btn.addEventListener("click", async () => {
      const code = pre.querySelector("code");
      if (!(await _copyToClipboard(code ? code.innerText : pre.innerText))) return;
      // P2-V: real DOM state change — "COPIED!" for ~2 s, then back.
      btn.textContent = "COPIED!";
      setTimeout(() => { btn.textContent = label; }, 2000);
    });
    return btn;
  }

  function _addCodeCopyButtons(bubbleEl) {
    bubbleEl.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".code-copy-btn")) return;
      const codeEl = pre.querySelector("code");
      const langMatch = codeEl?.className?.match(/language-([\w-]+)/);
      const lang = langMatch ? langMatch[1].toUpperCase() : null;

      if (!lang) {
        // Non-highlighted pre (tool-call JSON): keep the simple absolute
        // copy button with no code-frame chrome.
        const btn = _makeCodeCopyButton(pre, "Copy");
        pre.style.position = "relative";
        pre.appendChild(btn);
        return;
      }

      // P2-V code block chrome: wrap the pre in a frame, put a tab bar
      // (language pill + status dot + filename + copy) above it and a
      // slim status line (language, line count, verification state)
      // below it. The pre stays the scrollable code body.
      pre.dataset.lang = lang;
      const frame = document.createElement("div");
      frame.className = "code-frame";
      pre.parentNode.insertBefore(frame, pre);
      frame.appendChild(pre);

      const tabbar = document.createElement("div");
      tabbar.className = "code-tabbar";
      const pill = document.createElement("span");
      pill.className = "code-lang-pill";
      pill.textContent = lang;
      const dot = document.createElement("span");
      dot.className = "code-status-dot";
      dot.setAttribute("aria-hidden", "true");
      const file = document.createElement("span");
      file.className = "code-filename";
      // No endpoint exposes a filename for a code fence; show the honest
      // empty state rather than inventing one.
      file.textContent = "—";
      tabbar.append(pill, dot, file);
      tabbar.appendChild(_makeCodeCopyButton(pre, "COPY"));
      frame.insertBefore(tabbar, pre);

      const lineCount = codeEl ? codeEl.innerText.split("\n").length : 0;
      const status = document.createElement("div");
      status.className = "code-statusline";
      const left = document.createElement("span");
      left.textContent = `${lang} · ${lineCount} ${lineCount === 1 ? "LINE" : "LINES"}`;
      const verify = document.createElement("span");
      verify.className = "code-status-verify";
      // No endpoint verifies code blocks; never claim VERIFIED.
      verify.textContent = "UNVERIFIED";
      status.append(left, verify);
      frame.appendChild(status);
    });
  }

  function _buildMessageActions(role, getText, msgEl) {
    const frag = document.createDocumentFragment();
    const copyBtn = _addCopyButton(msgEl, getText);
    frag.appendChild(copyBtn);

    if (role === "user") {
      const retryBtn = document.createElement("button");
      retryBtn.className = "msg-action-btn";
      retryBtn.title = "Reuse this message";
      retryBtn.textContent = "↺";
      retryBtn.addEventListener("click", () => {
        if (onRetry) onRetry(getText());
      });
      frag.appendChild(retryBtn);
    }

    return frag;
  }

  function _stripSummaryHtml(n, totalS) {
    // n comes from array length, totalS is a computed "N.Ns" string — no
    // model-controlled text reaches this markup, so no escaping is needed.
    return `<span class="stage-summary">${n} tool${n !== 1 ? "s" : ""}${totalS ? ` · ${totalS}` : ""}</span>`;
  }

  function renderHistory(messages) {
    if (messages.length === 0) {
      showEmptyState();
      return;
    }
    clear();
    messages.forEach((m) => {
      const { div, bubble, meta, actions } = buildMessageEl(m.role);

      // Parse persisted tool calls once (malformed JSON must not break the
      // rest of this message's render — thinking/text still show below).
      let tcs = [];
      if (m.tool_calls) {
        try {
          const parsed = JSON.parse(m.tool_calls);
          if (Array.isArray(parsed)) tcs = parsed;
        } catch (_) { /* malformed tool_calls — render everything else anyway */ }
      }

      // Stage strip (collapsed, above bubble) — same finished-strip markup
      // the live-streaming path renders, so a reloaded turn looks identical
      // to a freshly streamed one.
      if (tcs.length > 0) {
        const strip = document.createElement("div");
        strip.className = "stage-strip strip-done";
        strip.innerHTML = _stripSummaryHtml(tcs.length, "");
        div.insertBefore(strip, bubble);
      }

      if (m.thinking) {
        const thinkEl = document.createElement("details");
        thinkEl.className = "thinking-block";
        thinkEl.innerHTML = `<summary>Thinking</summary><div class='thinking-content'>${render("thinking", m.thinking)}</div>`;
        bubble.appendChild(thinkEl);
      }
      tcs.forEach((tc) => {
        bubble.appendChild(_buildToolCallEl(tc.name, tc.input || {}, tc.id || "", tc.output, tc.is_error));
      });

      // F2: attachments are persisted against the user message server-side
      // (list_messages_with_attachments) and re-rendered here after a reload.
      // Images get an <img> with alt = filename; everything else a name chip.
      const attachments = Array.isArray(m.attachments) ? m.attachments : [];
      attachments.forEach((att) => {
        if (isImageName(att.original_name)) {
          const img = document.createElement("img");
          img.className = "msg-attachment-thumb";
          img.alt = att.original_name;
          img.src = attachmentDownloadUrl(att.id);
          bubble.appendChild(img);
        } else {
          const chip = document.createElement("span");
          chip.className = "msg-attachment-name";
          chip.textContent = att.original_name;
          bubble.appendChild(chip);
        }
      });

      const textEl = document.createElement("div");
      textEl.className = "text-block";
      textEl.innerHTML = render("text", m.content);
      bubble.appendChild(textEl);

      meta.innerHTML = `${fmtTime(new Date(m.created_at))}${tokMeta(m.input_tokens, m.output_tokens)}`;

      // F4 (2026-09-25 Houston): a cut-off assistant reply is persisted as
      // messages.stopped = 1 (crash checkpoint or /stop). History must say so.
      // The badge is a real text node inside .msg-meta, so screen readers get
      // the word "Incomplete" with no extra ARIA needed.
      if (m.role === "assistant" && m.stopped) {
        const badge = document.createElement("span");
        badge.className = "incomplete-badge";
        badge.textContent = "Incomplete";
        meta.appendChild(badge);
      }

      actions.appendChild(_buildMessageActions(m.role, () => m.content, div));
      messagesEl.appendChild(div);
      highlightCodeBlocks(textEl);
      _addCodeCopyButtons(bubble);
    });
    scrollDown();
  }

  function appendUserMessage(text) {
    _clearResyncMarker();
    lastUserText = text;
    // Clear empty state if showing
    const empty = messagesEl.querySelector(".empty-state");
    if (empty) empty.remove();

    const { div, bubble, meta, actions } = buildMessageEl("user");
    const textEl = document.createElement("div");
    textEl.className = "text-block";
    textEl.innerHTML = render("text", text);
    bubble.appendChild(textEl);
    meta.textContent = fmtTime();
    actions.appendChild(_buildMessageActions("user", () => text, div));
    messagesEl.appendChild(div);
    highlightCodeBlocks(textEl);
    scrollDown();
    // Returned so a caller whose send is REFUSED can remove the bubble it
    // optimistically added. Without this the transcript shows a message that
    // never reached the server and survives a reload looking real.
    return div;
  }

  /**
   * Undo a turn that never actually started.
   *
   * resetForNewTurn() paints the assistant placeholder ("Thinking…") the moment
   * a send is attempted. When the send is REFUSED, that placeholder is worse
   * than the orphaned user bubble: it says Claude is working on a message that
   * never left the browser. Nothing will ever clear it, because no `done` can
   * arrive for a turn the server never heard about.
   */
  function abortCurrentTurn() {
    currentAssistantEl?.remove();
    currentAssistantEl = null;
    currentBubbleEl = null;
    currentThinkingEl = null;
    currentMetaEl = null;
    currentStripEl = null;
    currentText = "";
    currentThinking = "";
    msgStartTime = null;
  }

  function _ensureStageStrip() {
    if (currentStripEl) return currentStripEl;
    currentStripEl = document.createElement("div");
    currentStripEl.className = "stage-strip";
    _stripStartTime = Date.now();
    // Insert before bubble so the strip sits above the text
    currentAssistantEl.insertBefore(currentStripEl, currentBubbleEl);
    return currentStripEl;
  }

  function startAssistantMessage() {
    _clearResyncMarker();
    currentText = "";
    currentThinking = "";
    currentStripEl = null;
    _toolStartTimes.clear();
    _stripStartTime = 0;
    msgStartTime = Date.now();
    const { div, bubble, meta, actions } = buildMessageEl("assistant");
    meta.classList.add("status-badge", "status-processing");
    meta.textContent = "Thinking…";
    // 4.1.3: the streaming indicator is a live status.
    meta.setAttribute("role", "status");

    const thinkEl = document.createElement("details");
    thinkEl.className = "thinking-block";
    thinkEl.hidden = true;
    thinkEl.innerHTML = "<summary>Thinking</summary><div class='thinking-content'></div>";
    bubble.appendChild(thinkEl);

    const textEl = document.createElement("div");
    textEl.className = "text-block";
    bubble.appendChild(textEl);

    currentAssistantEl = div;
    currentBubbleEl = bubble;
    currentThinkingEl = thinkEl;
    currentMetaEl = meta;
    // Actions get populated on finish
    currentAssistantEl._actionsEl = actions;
    messagesEl.appendChild(div);
    scrollDown();
  }

  function appendThinkingText(delta) {
    if (!currentAssistantEl) startAssistantMessage();
    currentThinking += delta;
    currentThinkingEl.hidden = false;
    currentThinkingEl.querySelector(".thinking-content").innerHTML = render("thinking", currentThinking);
    scrollDown();
  }

  function appendAssistantText(delta) {
    if (!currentAssistantEl) startAssistantMessage();
    currentText += delta;
    currentBubbleEl.querySelector(".text-block").innerHTML = render("text", currentText);
    scrollDown();
  }

  // 4.1.3: a finished reply is announced ONCE through a dedicated visually-hidden
  // live region, separate from #chat-status so the per-frame status clearing at
  // the `text` handler cannot erase it. Streaming itself stays silent.
  function announceReply(plainText) {
    const plain = String(plainText ?? "").trim();
    let msg;
    if (!plain) {
      msg = "Claude replied.";
    } else if (plain.length > 300) {
      msg = `Claude replied: ${plain.slice(0, 300)}… reply continues`;
    } else {
      msg = `Claude replied: ${plain}`;
    }
    // Clear, then set on the next tick: a repeated identical reply must still
    // produce a new live-region announcement.
    replyAnnounceEl.textContent = "";
    setTimeout(() => { replyAnnounceEl.textContent = msg; }, 0);
  }

  function _buildToolCallEl(name, inputObj, id, output, isError) {
    const details = document.createElement("details");
    details.className = "tool-call-block";
    if (id) details.dataset.toolId = id;

    const summary = document.createElement("summary");
    summary.className = "tool-call-summary";
    const icon = document.createElement("span");
    icon.className = "tool-call-icon";
    icon.textContent = "⚙";
    const nameSpan = document.createElement("span");
    nameSpan.className = "tool-call-name";
    nameSpan.textContent = TOOL_LABELS[name] ?? "Tool";
    const statusSpan = document.createElement("span");
    statusSpan.className = "tool-call-status";
    if (output !== undefined) {
      statusSpan.textContent = isError ? "Error" : "Done";
      statusSpan.classList.add(isError ? "error" : "done");
    } else {
      statusSpan.textContent = "Running…";
    }
    summary.appendChild(icon);
    summary.appendChild(nameSpan);
    summary.appendChild(statusSpan);

    const pre = document.createElement("pre");
    pre.className = "tool-call-input";
    pre.textContent = JSON.stringify(inputObj, null, 2);

    const resultWrap = document.createElement("div");
    resultWrap.className = "tool-call-result-wrap";
    if (output) {
      const div = document.createElement("div");
      div.className = `tool-call-result${isError ? " tool-call-result-error" : ""}`;
      div.textContent = output.length > 800 ? output.slice(0, 800) + "…" : output;
      resultWrap.appendChild(div);
    }

    details.appendChild(summary);
    details.appendChild(pre);
    details.appendChild(resultWrap);
    return details;
  }

  function appendToolCall(name, inputObj, id) {
    if (!currentAssistantEl) startAssistantMessage();

    // Stage pill
    const strip = _ensureStageStrip();
    if (id) _toolStartTimes.set(id, Date.now());
    const pill = document.createElement("span");
    pill.className = "stage-pill spinning";
    if (id) pill.dataset.pillId = id;
    const iconSpan = document.createElement("span");
    iconSpan.className = "pill-icon";
    iconSpan.textContent = "⟳";
    const nameSpan = document.createElement("span");
    nameSpan.className = "pill-name";
    nameSpan.textContent = TOOL_LABELS[name] ?? "Tool";
    const elapsedSpan = document.createElement("span");
    elapsedSpan.className = "pill-elapsed";
    pill.append(iconSpan, nameSpan, elapsedSpan);
    strip.appendChild(pill);

    // Existing details block inside bubble
    const details = _buildToolCallEl(name, inputObj, id);
    currentBubbleEl.insertBefore(details, currentBubbleEl.querySelector(".text-block"));
    scrollDown();
  }

  function appendToolResult(toolUseId, content, isError) {
    // Update details block
    const block = messagesEl.querySelector(`[data-tool-id="${CSS.escape(toolUseId)}"]`);
    if (block) {
      const st = block.querySelector(".tool-call-status");
      if (st) { st.textContent = isError ? "Error" : "Done"; st.classList.add(isError ? "error" : "done"); }
      const wrap = block.querySelector(".tool-call-result-wrap");
      if (wrap) {
        const div = document.createElement("div");
        div.className = `tool-call-result${isError ? " tool-call-result-error" : ""}`;
        div.textContent = content.length > 800 ? content.slice(0, 800) + "…" : content;
        wrap.appendChild(div);
      }
    }

    // Resolve stage pill
    const pill = currentStripEl?.querySelector(`[data-pill-id="${CSS.escape(toolUseId)}"]`);
    if (pill) {
      const start = _toolStartTimes.get(toolUseId);
      const elapsedS = start ? ((Date.now() - start) / 1000).toFixed(1) : null;
      pill.classList.remove("spinning");
      pill.classList.add(isError ? "pill-error" : "pill-done");
      pill.querySelector(".pill-icon").textContent = isError ? "✗" : "✓";
      if (elapsedS) pill.querySelector(".pill-elapsed").textContent = elapsedS + "s";
    }
    scrollDown();
  }

  function finishAssistantMessage(status, usage) {
    if (!currentMetaEl) {
      setStreaming(false);
      return;
    }

    // Collapse stage strip to summary
    if (currentStripEl) {
      const pillEls = currentStripEl.querySelectorAll(".stage-pill");
      const n = pillEls.length;
      const totalS = _stripStartTime ? ((Date.now() - _stripStartTime) / 1000).toFixed(1) + "s" : "";
      currentStripEl.classList.add("strip-done");
      currentStripEl.innerHTML = _stripSummaryHtml(n, totalS);
      currentStripEl = null;
    }

    highlightCodeBlocks(currentBubbleEl.querySelector(".text-block") || currentBubbleEl);
    _addCodeCopyButtons(currentBubbleEl);
    currentMetaEl.className = `msg-meta status-badge status-${status}`;
    currentMetaEl.innerHTML = `${fmtTime()} · ${STATUS_LABEL[status] || "Unknown"}${tokMeta(usage?.input_tokens, usage?.output_tokens)}`;

    const captured = currentText;
    if (currentAssistantEl._actionsEl) {
      currentAssistantEl._actionsEl.appendChild(
        _buildMessageActions("assistant", () => captured, currentAssistantEl)
      );
    }

    // Add regenerate button only on the latest assistant message
    messagesEl.querySelectorAll(".regen-btn").forEach((b) => b.remove());
    if (status === "done" && currentAssistantEl._actionsEl) {
      const regenBtn = document.createElement("button");
      regenBtn.className = "msg-action-btn regen-btn";
      regenBtn.title = "Regenerate response";
      regenBtn.textContent = "⟳";
      regenBtn.addEventListener("click", async () => {
        if (!conversationId) return;
        const composerEl = document.getElementById("composer");
        if (!(composerEl?.setText && composerEl?.send)) {
          if (onRetry) onRetry(lastUserText);  // fallback: refill only
          return;
        }
        // The row this button sits in is the answer being replaced. Hold the
        // reference now: finishAssistantMessage strips every .regen-btn when the
        // new turn completes, so by then "the last assistant row" is the new one.
        const supersededRow = regenBtn.closest(".msg");
        composerEl.setText(lastUserText);
        let started;
        try {
          // The server supersedes the old answer and re-asks the question it has
          // on record. There is deliberately no DELETE here any more: the delete
          // and the resend are one operation server-side, and nothing is written
          // a second time — that is what used to leave [user][user][assistant].
          started = await composerEl.send({ regenerate: true });
        } catch (err) {
          // Nothing has moved yet, so the old answer is still on screen and the
          // question is sitting in the composer for a manual send. Task 2.5 owns
          // routing this through the shared non-boot failure surface.
          showErrorToast("Couldn't regenerate that reply. Try again, or reload the page.");
          return;
        }
        // Only drop the old answer once the new turn is actually under way.
        if (started) supersededRow?.remove();
      });
      currentAssistantEl._actionsEl.appendChild(regenBtn);
    }

    if (onComplete && status === "done" && msgStartTime) {
      onComplete(usage, Date.now() - msgStartTime);
    }
    msgStartTime = null;
    currentAssistantEl = null;
    currentBubbleEl = null;
    currentThinkingEl = null;
    currentMetaEl = null;
  }

  async function exportConversation() {
    if (!conversationId) return;
    // Markdown is built server-side by conversations_service.export_as_markdown,
    // which has real pytest coverage (fence-length selection, </details>
    // neutralisation, stopped-marker stripping, output truncation). A previous
    // client-side implementation lived here and produced DIFFERENT output from the
    // same /export endpoint, so anyone calling the endpoint directly got the older
    // format. One implementation, in the layer that can be tested.
    const { markdown } = await api.exportConversation(conversationId);
    const blob = new Blob([markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `conversation-${conversationId.slice(0, 8)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function scrollDown() {
    root.scrollTop = root.scrollHeight;
  }

  function _showApprovalModal(tool, action, socket, ev = {}) {
    let dialog = null;
    let timer = null;

    function clearTimer() {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    }

    function close(approved) {
      clearTimer();
      dialog?.close({ restore: false });
      _approvalModal = null;
      if (approved) socket.approve(); else socket.deny();
    }

    function dismiss() {
      clearTimer();
      dialog?.close({ restore: false });
      _approvalModal = null;
    }

    dialog = showDialog({
      title: "Tool Permission Request",
      body: `
        <div class="approval-tool">⚡ <strong>${escapeHtml(tool || "tool")}</strong></div>
        ${action ? `<p class="approval-action">${escapeHtml(action)}</p>` : ""}
        <p class="approval-risk-note">Allow Claude to use this tool?</p>
        <p class="approval-countdown" id="approval-countdown"></p>
        <p class="sr-only" id="approval-announce" aria-live="polite"></p>
      `,
      actions: [
        [
          {
            className: "modal-btn modal-confirm danger",
            id: "approval-deny-btn",
            label: "✕ Deny",
            onClick: () => close(false),
          },
          {
            className: "modal-btn modal-confirm",
            id: "approval-approve-btn",
            label: "✓ Approve",
            onClick: () => close(true),
          },
        ],
        [
          {
            className: "modal-btn",
            id: "approval-need-time-btn",
            label: "Need more time",
            hidden: true,
            onClick: () => socket.extendApproval(),
          },
        ],
      ],
      trap: false,
      closeOnBackdrop: false,
      returnFocus: false,
    });

    // Server owns the deadline. `remaining` arrives on the approval_needed frame
    // and again on every approval_extended reply; the local 1 Hz tick is only a
    // display of that value, never a second source of truth.
    let remaining = Number(ev.remaining ?? ev.timeout ?? 60);
    let announced = false;
    const countdownEl = dialog.box.querySelector("#approval-countdown");
    const announceEl = dialog.box.querySelector("#approval-announce");
    const needTimeBtn = dialog.box.querySelector("#approval-need-time-btn");

    function renderCountdown() {
      const s = Math.max(0, Math.ceil(remaining));
      countdownEl.textContent = `Time remaining: ${s} s`;
      if (remaining <= 20) {
        needTimeBtn.hidden = false;
        if (!announced) {
          // 2.2.1: announce at the 20 s mark ONCE, not a per-second chatter.
          announced = true;
          announceEl.textContent = "20 seconds remaining. Choose Need more time to extend the approval deadline.";
        }
      } else {
        needTimeBtn.hidden = true;
      }
    }
    renderCountdown();

    timer = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        // The server fails closed at its own deadline. Once OUR display of that
        // deadline reaches zero this modal is no longer actionable; remove it
        // without sending anything — the server has already decided.
        clearTimer();
        dialog.close({ restore: false });
        _approvalModal = null;
        return;
      }
      renderCountdown();
    }, 1000);

    _approvalModal = {
      onExtended(ext) {
        const next = Number(ext?.remaining);
        if (!Number.isFinite(next) || next <= 0) return;
        remaining = next;      // re-sync from the server, then re-arm the 20 s announce
        announced = false;
        renderCountdown();
      },
      dismiss,
    };
  }

  // "Turn still running elsewhere" — no further events will ever arrive on
  // this fresh socket for that old turn (the backend keeps streaming to the
  // WS instance that was open when the turn started), so this is a static
  // notice, not a live status.
  function showResyncRunning() {
    statusEl.textContent = "Claude is still responding…";
  }

  // "Turn finished while this client was away" — history was already
  // re-rendered with the completed reply before the socket connected, so
  // just label the last assistant message. Quiet, transient, DB-untouched.
  function showResyncCompleted() {
    _clearResyncMarker();
    const nodes = messagesEl.querySelectorAll(".msg-assistant");
    const lastAssistantEl = nodes[nodes.length - 1];
    if (!lastAssistantEl) return;
    const marker = document.createElement("div");
    marker.className = "msg-meta";
    marker.textContent = "Completed while you were away";
    lastAssistantEl.appendChild(marker);
    resyncMarkerEl = marker;
  }

  let unbindFns = [];
  let lastUsage = null;

  function bindSocket(socket) {
    unbindFns.forEach((fn) => fn());
    lastUsage = null;
    unbindFns = [
      socket.on("thinking", (ev) => {
        statusEl.textContent = "";
        appendThinkingText(ev.thinking || "");
      }),
      socket.on("text", (ev) => {
        statusEl.textContent = "";
        appendAssistantText(ev.text || "");
      }),
      socket.on("usage", (ev) => { lastUsage = ev.usage; }),
      socket.on("result", (ev) => { if (ev.usage) lastUsage = ev.usage; }),
      socket.on("timeout", () => {
        statusEl.textContent = "Response timed out. Send the message again to retry.";
        finishAssistantMessage("timeout", lastUsage);
      }),
      // P2-B E1: a non-fatal notice (non-JSON CLI stdout). It must be visible
      // WITHOUT ending the turn — an `error` frame is terminal here (it calls
      // finishAssistantMessage("error") and restores the composer via the
      // backend's error-then-done pair), so the notice gets its own frame type.
      socket.on("notice", (ev) => {
        const line = String(ev.text || "").trim();
        if (!line) return;
        const notice = document.createElement("div");
        notice.className = "chat-notice";
        // 4.1.3: the notice is a status message (polite), styled as an error.
        notice.setAttribute("role", "status");
        notice.textContent = "GoClaudaddy ignored an unexpected line from the Claude CLI. The reply is unaffected; restart the app if this repeats.";
        messagesEl.appendChild(notice);
        scrollDown();
      }),
      socket.on("error", (ev) => {
        let msg = "Error";
        if (ev.code === "claude_not_found") {
          msg = "Claude CLI isn't installed. Run the setup script and refresh.";
        } else if (ev.code === "auth_failed") {
          msg = "Claude isn't authenticated. Run `claude auth` in a terminal, then refresh.";
        } else if (ev.code === "budget_exceeded") {
          // The one failure a new user cannot diagnose on their own. Before
          // this, an unfunded CaaS key produced "Error: claude exited with
          // code 1" — which reads as a broken app rather than an account that
          // needs a budget assigned. The balance page is where they fix it,
          // and the app already links to it from the sidebar; this just says
          // so at the moment it matters.
          msg = "Your CaaS key has no remaining budget. Open Check Balance in the right sidebar to see your allowance and request an increase.";
        } else {
          msg = ev.error || "Claude reported an error. Try again; if it keeps failing, open the LOG panel for details.";
        }
        statusEl.textContent = msg;
        showErrorToast(msg);
        finishAssistantMessage("error", lastUsage);
      }),
      socket.on("stopped", () => {
        statusEl.textContent = "Stopped.";
        finishAssistantMessage("stopped", lastUsage);
      }),
      socket.on("tool_call", (ev) => appendToolCall(ev.name, ev.input ?? {}, ev.id)),
      socket.on("tool_result", (ev) => appendToolResult(ev.tool_use_id, ev.content || "", ev.is_error)),
      socket.on("done", () => {
        if (currentAssistantEl) {
          announceReply(currentText);
          finishAssistantMessage("done", lastUsage);
        }
        setStreaming(false);
      }),
      // Cleanup on ANY close, including a deliberate conversation switch. No
      // alarming text here: a switch is not a fault, and saying so would train
      // the user to ignore the message that matters.
      socket.on("_close", () => {
        _approvalModal?.dismiss?.();
        if (currentAssistantEl) {
          finishAssistantMessage("error", lastUsage);
          setStreaming(false);
        }
      }),
      // 4-I5. Fires ONLY on an unintended drop (task 2.9), so it cannot flash on
      // every conversation switch the way a `_close`-based indicator would.
      //
      // Deliberately NOT guarded on currentAssistantEl. The old handler only
      // spoke mid-turn, so a socket that died while IDLE rendered nothing at
      // all and the user typed into a dead app, wondering why nothing sent.
      socket.on("_disconnected", () => {
        _approvalModal?.dismiss?.();
        statusEl.textContent = "Connection lost — reconnecting…";
        setStreaming(false);
      }),
      socket.on("_reconnect", () => { statusEl.textContent = ""; }),
      socket.on("_resync_running", () => showResyncRunning()),
      socket.on("_resync_done", () => showResyncCompleted()),
      // 2.2.1: the server owns the deadline. Its reply re-syncs the live
      // countdown in the modal; the modal handle is set by _showApprovalModal.
      socket.on("approval_extended", (ev) => _approvalModal?.onExtended?.(ev)),
      socket.on("approval_needed", (ev) => {
        if (storage.getItem("gca_feat_approval") !== "1") {
          // Feature flag off — deny by default so no tool runs without the user's
          // decision; deny() still answers the subprocess, so nothing is left hanging.
          socket.deny();
          statusEl.textContent = "A tool asked for approval and was denied. Turn on 'Prompt for tool approval' in Settings to decide yourself.";
          return;
        }
        _showApprovalModal(ev.tool, ev.action, socket, ev);
      }),
    ];
  }

  return {
    bindSocket,
    appendUserMessage,
    resetForNewTurn: startAssistantMessage,
    abortCurrentTurn,
    renderHistory,
    clear,
    showEmptyState,
    setConversationId,
    exportConversation,
  };
}
