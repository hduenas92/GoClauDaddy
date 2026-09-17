import { getState, subscribe } from "../state/store.js";
import { createConversation, deleteConversation, renameConversation, loadConversations } from "../state/actions.js";
import { showModal, showConfirm } from "./modal.js";

export function mountSidebarConversations(root, onSelect, onEmpty) {
  root.innerHTML = `
    <button id="new-conv-btn">＋ New Chat</button>
    <div id="conv-search-wrap">
      <input id="conv-search" type="text" placeholder="Search chats…" autocomplete="off">
    </div>
    <ul id="conv-list"></ul>
  `;
  const listEl = root.querySelector("#conv-list");
  const searchEl = root.querySelector("#conv-search");

  root.querySelector("#new-conv-btn").addEventListener("click", async () => {
    const conv = await createConversation();
    onSelect(conv.id);
  });

  searchEl.addEventListener("input", render);

  // Expose search focus for keyboard shortcut
  root.focusSearch = () => searchEl.focus();

  function render() {
    const { conversations, activeConversationId } = getState();
    const q = searchEl.value.trim().toLowerCase();
    const filtered = q ? conversations.filter((c) => c.name.toLowerCase().includes(q)) : conversations;
    listEl.innerHTML = "";
    filtered.forEach((c) => {
      const li = document.createElement("li");
      li.className = "conv-item" + (c.id === activeConversationId ? " active" : "");
      const ago = _relTime(c.updated_at);
      const srcBadge = c.source && c.source !== "web"
        ? `<span class="conv-source-badge conv-source-${escapeHtml(c.source)}">${escapeHtml(c.source)}</span>`
        : "";
      const costBadge = c.cost_usd > 0 ? ` · ~$${c.cost_usd.toFixed(2)}` : "";
      li.innerHTML = `
        <span class="conv-dot conv-dot-${c.status}"></span>
        <span class="conv-body" role="button" tabindex="0">
          <span class="conv-name">${escapeHtml(c.name)}${srcBadge}</span>
          <span class="conv-ts">${ago}${costBadge}</span>
        </span>
        <button class="conv-rename" title="Rename (F2)">✎</button>
        <button class="conv-delete" title="Delete">✕</button>
      `;
      const convBody = li.querySelector(".conv-body");
      const selectConv = () => onSelect(c.id);
      convBody.addEventListener("click", selectConv);
      convBody.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectConv();
        }
      });
      li.querySelector(".conv-rename").addEventListener("click", async (e) => {
        e.stopPropagation();
        const result = await showModal({
          title: "Rename conversation",
          fields: [{ name: "name", label: "Name", value: c.name }],
          confirmText: "Rename",
        });
        if (result?.name?.trim()) await renameConversation(c.id, result.name.trim());
      });
      li.querySelector(".conv-delete").addEventListener("click", async (e) => {
        e.stopPropagation();
        const ok = await showConfirm({ message: `Delete "${c.name}"? This can't be undone.` });
        if (ok) {
          const remaining = await deleteConversation(c.id);
          if (remaining.length > 0) onSelect(remaining[0].id);
          else onEmpty?.();
        }
      });
      listEl.appendChild(li);
    });
  }

  subscribe(render);
  render();
}

function _relTime(isoStr) {
  if (!isoStr) return "";
  const diff = Date.now() - new Date(isoStr).getTime();
  if (diff < 60_000) return "now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h`;
  return `${Math.floor(diff / 86_400_000)}d`;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
