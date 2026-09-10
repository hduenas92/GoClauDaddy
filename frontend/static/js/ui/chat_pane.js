import { render } from "../render/content_registry.js";
import { highlightCodeBlocks } from "../render/markdown.js";
import { setStreaming } from "../state/actions.js";

const STATUS_LABEL = { done: "Done", error: "Error", stopped: "Stopped", timeout: "Timed out" };

export function mountChatPane(root) {
  root.innerHTML = `
    <div id="chat-messages"></div>
    <div id="chat-status" class="chat-status"></div>
  `;
  const messagesEl = root.querySelector("#chat-messages");
  const statusEl = root.querySelector("#chat-status");

  let currentAssistantEl = null;
  let currentBubbleEl = null;
  let currentMetaEl = null;
  let currentText = "";

  function clear() {
    messagesEl.innerHTML = "";
    statusEl.textContent = "";
    currentAssistantEl = null;
    currentText = "";
  }

  function fmtTime(d = new Date()) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function buildMessageEl(role) {
    const div = document.createElement("div");
    div.className = `msg msg-${role}`;
    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    div.appendChild(bubble);
    div.appendChild(meta);
    return { div, bubble, meta };
  }

  function renderHistory(messages) {
    clear();
    messages.forEach((m) => {
      const { div, bubble, meta } = buildMessageEl(m.role);
      bubble.innerHTML = render("text", m.content);
      const tokBit = m.output_tokens ? ` · ${m.output_tokens} tok` : "";
      meta.textContent = `${fmtTime(new Date(m.created_at))}${tokBit}`;
      messagesEl.appendChild(div);
      highlightCodeBlocks(bubble);
    });
    scrollDown();
  }

  function appendUserMessage(text) {
    const { div, bubble, meta } = buildMessageEl("user");
    bubble.innerHTML = render("text", text);
    meta.textContent = fmtTime();
    messagesEl.appendChild(div);
    highlightCodeBlocks(bubble);
    scrollDown();
  }

  function startAssistantMessage() {
    currentText = "";
    const { div, bubble, meta } = buildMessageEl("assistant");
    meta.classList.add("status-badge", "status-processing");
    meta.textContent = "Thinking…";
    currentAssistantEl = div;
    currentBubbleEl = bubble;
    currentMetaEl = meta;
    messagesEl.appendChild(div);
    scrollDown();
  }

  function appendAssistantText(delta) {
    if (!currentAssistantEl) startAssistantMessage();
    currentText += delta;
    currentBubbleEl.innerHTML = render("text", currentText);
    scrollDown();
  }

  function finishAssistantMessage(status, usage) {
    if (!currentMetaEl) return;
    highlightCodeBlocks(currentBubbleEl);
    currentMetaEl.className = `msg-meta status-badge status-${status}`;
    const tokBit = usage?.output_tokens ? ` · ${usage.output_tokens} tok` : "";
    currentMetaEl.textContent = `${fmtTime()} · ${STATUS_LABEL[status] || status}${tokBit}`;
    currentAssistantEl = null;
    currentBubbleEl = null;
    currentMetaEl = null;
  }

  function scrollDown() {
    root.scrollTop = root.scrollHeight;
  }

  let unbindFns = [];
  let lastUsage = null;

  function bindSocket(socket) {
    unbindFns.forEach((fn) => fn());
    lastUsage = null;
    unbindFns = [
      socket.on("thinking", () => {
        statusEl.textContent = "Thinking…";
      }),
      socket.on("text", (ev) => {
        statusEl.textContent = "";
        appendAssistantText(ev.text || "");
      }),
      socket.on("usage", (ev) => {
        lastUsage = ev.usage;
      }),
      socket.on("result", (ev) => {
        if (ev.usage) lastUsage = ev.usage;
      }),
      socket.on("timeout", () => {
        statusEl.textContent = "Response timed out.";
        finishAssistantMessage("timeout", lastUsage);
      }),
      socket.on("error", (ev) => {
        statusEl.textContent = `Error: ${ev.error || "unknown error"}`;
        finishAssistantMessage("error", lastUsage);
      }),
      socket.on("stopped", () => {
        statusEl.textContent = "Stopped.";
        finishAssistantMessage("stopped", lastUsage);
      }),
      socket.on("done", () => {
        if (currentAssistantEl) finishAssistantMessage("done", lastUsage);
        setStreaming(false);
      }),
    ];
  }

  return {
    bindSocket,
    appendUserMessage,
    resetForNewTurn: startAssistantMessage,
    renderHistory,
    clear,
  };
}
