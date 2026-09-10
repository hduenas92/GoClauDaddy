import { getState, subscribe } from "../state/store.js";
import { createConversation, deleteConversation, renameConversation } from "../state/actions.js";

export function mountSidebarConversations(root, onSelect) {
  root.innerHTML = `
    <button id="new-conv-btn">+ New Conversation</button>
    <ul id="conv-list"></ul>
  `;
  const listEl = root.querySelector("#conv-list");

  root.querySelector("#new-conv-btn").addEventListener("click", async () => {
    const conv = await createConversation();
    onSelect(conv.id);
  });

  function render() {
    const { conversations, activeConversationId } = getState();
    listEl.innerHTML = "";
    conversations.forEach((c) => {
      const li = document.createElement("li");
      li.className = "conv-item" + (c.id === activeConversationId ? " active" : "");
      li.innerHTML = `
        <span class="conv-dot conv-dot-${c.status}"></span>
        <span class="conv-name">${escapeHtml(c.name)}</span>
        <button class="conv-rename" title="Rename">✎</button>
        <button class="conv-delete" title="Delete">✕</button>
      `;
      li.querySelector(".conv-name").addEventListener("click", () => onSelect(c.id));
      li.querySelector(".conv-rename").addEventListener("click", async (e) => {
        e.stopPropagation();
        const name = prompt("Rename conversation", c.name);
        if (name && name.trim()) await renameConversation(c.id, name.trim());
      });
      li.querySelector(".conv-delete").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (confirm(`Delete "${c.name}"?`)) await deleteConversation(c.id);
      });
      listEl.appendChild(li);
    });
  }

  subscribe(render);
  render();
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
