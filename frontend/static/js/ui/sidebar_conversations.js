import { getState, subscribe } from "../state/store.js";
import { createConversation, deleteConversation, renameConversation, loadConversations } from "../state/actions.js";
import { showModal, showConfirm, showErrorToast } from "./modal.js";
import { api } from "../api/http.js";

// Long enough that typing a word does not fire a request per keystroke, short
// enough that the Messages group feels like part of the same box. The local
// name filter is NOT debounced Ã¢â‚¬â€ see onSearchInput.
const SEARCH_DEBOUNCE_MS = 250;

export function mountSidebarConversations(root, onSelect, onEmpty) {
  root.innerHTML = `
    <button id="new-conv-btn">Ã¯Â¼â€¹ New Chat</button>
    <div id="conv-search-wrap">
      <input id="conv-search" type="text" placeholder="Search chatsÃ¢â‚¬Â¦" autocomplete="off">
    </div>
    <ul id="conv-list"></ul>
  `;
  const listEl = root.querySelector("#conv-list");
  const searchEl = root.querySelector("#conv-search");

  root.querySelector("#new-conv-btn").addEventListener("click", async () => {
    const conv = await createConversation();
    onSelect(conv.id);
  });

  // --- search state, held in the closure ---------------------------------
  // `hits` is null when no search is in effect and an array once one has
  // returned, so an empty array ("searched, found nothing") stays distinct from
  // "not searching". Keeping it here rather than re-fetching means a store
  // update Ã¢â‚¬â€ subscribe(render) fires on every state change Ã¢â‚¬â€ redraws the hits
  // already in hand instead of dropping them or hitting the network again.
  let hits = null;
  let hitsTruncated = false;
  let searchTimer = null;
  // Monotonic. A response whose id is not the latest is DISCARDED: type `foo`
  // then `foobar` and `foo`'s slower response would otherwise land last and win.
  let latestReqId = 0;

  searchEl.addEventListener("input", onSearchInput);

  // Expose search focus for keyboard shortcut
  root.focusSearch = () => searchEl.focus();

  function onSearchInput() {
    // SYNCHRONOUS, always. The name filter is local and must never be gated on
    // a network round trip Ã¢â‚¬â€ typing stays responsive with the server down.
    render();

    clearTimeout(searchTimer);
    const q = searchEl.value.trim();
    if (!q) {
      // Clearing the box removes the Messages group entirely. Bump the id so a
      // request already in flight cannot repopulate it after the user cleared.
      latestReqId += 1;
      hits = null;
      hitsTruncated = false;
      render();
      return;
    }
    searchTimer = setTimeout(() => runSearch(q), SEARCH_DEBOUNCE_MS);
  }

  async function runSearch(q) {
    const id = ++latestReqId;
    try {
      const data = await api.searchMessages(q);
      if (id !== latestReqId) return;
      hits = data?.results ?? [];
      hitsTruncated = Boolean(data?.truncated);
    } catch (err) {
      if (id !== latestReqId) return;
      // Leave `hits` null so no empty Messages group appears, and leave the
      // Chats group alone Ã¢â‚¬â€ a failed content search must not look like "no
      // conversations match". The server 400s on a q that tokenises to nothing
      // (e.g. "!!!"), which is a normal thing for a user to type.
      hits = null;
      hitsTruncated = false;
      showErrorToast(err?.message || "Search failed.");
    }
    render();
  }

  function render() {
    renderConversations();
    if (hits) renderMessageHits(hits, hitsTruncated);
  }

  function renderConversations() {
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
      const costBadge = c.cost_usd > 0 ? ` Ã‚Â· ~$${c.cost_usd.toFixed(2)}` : "";
      li.innerHTML = `
        <span class="conv-dot conv-dot-${escapeHtml(c.status)}"></span>
        <span class="conv-body" role="button" tabindex="0">
          <span class="conv-name">${escapeHtml(c.name)}${srcBadge}</span>
          <span class="conv-ts">${ago}${costBadge}</span>
        </span>
        <button class="conv-rename" title="Rename">Ã¢Å“Å½</button>
        <button class="conv-delete" title="Delete">Ã¢Å“â€¢</button>
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

  function renderMessageHits(rows, truncated) {
    const header = document.createElement("li");
    header.className = "conv-group-header";
    header.textContent = rows.length ? "Messages" : "No messages match";
    listEl.appendChild(header);

    rows.forEach((r) => {
      const li = document.createElement("li");
      li.className = "conv-item conv-hit";
      li.innerHTML = `
        <span class="conv-body" role="button" tabindex="0">
          <span class="conv-name">${escapeHtml(r.conversation_name || "Untitled")}</span>
          <span class="conv-hit-snippet">${_markSnippet(r.snippet)}</span>
        </span>
      `;
      const body = li.querySelector(".conv-body");
      // Selecting the conversation only. Scrolling to r.message_id is a
      // deliberate follow-up, not half-built here.
      const go = () => onSelect(r.conversation_id);
      body.addEventListener("click", go);
      body.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go();
        }
      });
      listEl.appendChild(li);
    });

    if (truncated) {
      const more = document.createElement("li");
      more.className = "conv-group-note";
      more.textContent = `Showing the first ${rows.length} Ã¢â‚¬â€ refine to see more`;
      listEl.appendChild(more);
    }
  }

  subscribe(render);
  render();
}

/** FTS5 wraps each hit in `**` (see the snippet() call in routers/search.py).
 *
 *  ESCAPE FIRST, DECORATE SECOND, and never the other way round. This is
 *  message text a model wrote, going into innerHTML: escaping after inserting
 *  <mark> would leave the mark tags escaped and any embedded markup live, which
 *  is the exact inversion that turns a highlighter into an XSS hole.
 *  escapeHtml leaves `*` untouched, so the delimiters survive it intact.
 *
 *  Known limitation, stated rather than hidden: message content that itself
 *  contains `**` will produce a spurious highlight, because the backend chose
 *  `**` as its delimiter and the payload gives us no way to tell the two apart.
 */
function _markSnippet(snippet) {
  return escapeHtml(snippet ?? "").replace(/\*\*([\s\S]*?)\*\*/g, "<mark>$1</mark>");
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
