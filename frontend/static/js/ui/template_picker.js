/**
 * template_picker.js — Full-panel template browser with CRUD for custom templates.
 *
 * openTemplatePicker({ onSelect }) — opens the overlay.
 * interpolateTemplate(body, onFilled) — variable fill-in flow; calls onFilled(text).
 */

import { trapFocus } from "./modal.js";

import { api } from "../api/http.js";
import { getTemplates, invalidateTemplates } from "../api/template_cache.js";
import { showModal, showConfirm } from "./modal.js";

const CATEGORIES = ["report", "email", "document", "analysis", "code", "custom"];
export const CAT_LABELS = { report: "Reports", email: "Email", document: "Docs", analysis: "Analysis", code: "Code", custom: "Custom" };

/** Extract {{Variable Name}} placeholders from a template body. Returns unique list. */
function extractVars(body) {
  const matches = [...body.matchAll(/\{\{([^}]+)\}\}/g)];
  const seen = new Set();
  return matches.map(m => m[1]).filter(v => { if (seen.has(v)) return false; seen.add(v); return true; });
}

/** Run fill-in modal for a template body; calls onFilled(text) with interpolated result. */
export async function interpolateTemplate(body, onFilled) {
  const vars = extractVars(body);
  if (vars.length === 0) { onFilled(body); return; }
  const result = await showModal({
    title: "Fill in template",
    fields: vars.map(v => ({ name: v, label: v, placeholder: v })),
    confirmText: "Use Template",
  });
  if (!result) return;
  let filled = body;
  for (const [key, val] of Object.entries(result)) {
    filled = filled.replaceAll(`{{${key}}}`, val || "");
  }
  onFilled(filled);
}

export function openTemplatePicker({ onSelect }) {
  let templates = [];
  let activeCategory = "report";
  let searchQuery = "";

  const overlay = document.createElement("div");
  overlay.className = "tp-overlay";
  let _releaseTrap = null;
  // 2.4.3: hand focus back to whatever opened the picker.
  const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  function close() {
    _releaseTrap?.();
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (returnFocus?.isConnected) returnFocus.focus();
  }

  function onKey(e) {
    if (e.key === "Escape") close();
  }
  document.addEventListener("keydown", onKey);
  _releaseTrap = trapFocus(overlay);
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });

  // --- Render ---

  function counts() {
    const map = {};
    CATEGORIES.forEach(c => map[c] = 0);
    templates.forEach(t => {
      const cat = t.is_builtin ? t.category : "custom";
      map[cat] = (map[cat] || 0) + 1;
    });
    return map;
  }

  function filteredTemplates() {
    const q = searchQuery.toLowerCase();
    return templates.filter(t => {
      const inCat = activeCategory === "custom" ? !t.is_builtin : t.category === activeCategory;
      if (!inCat) return false;
      if (q) return t.title.toLowerCase().includes(q) || (t.description || "").toLowerCase().includes(q);
      return true;
    });
  }

  function render() {
    const c = counts();
    const list = filteredTemplates();

    overlay.innerHTML = `
      <div class="tp-box">
        <div class="tp-header">
          <span class="tp-title">Templates</span>
          <input class="tp-search" id="tp-search" type="text" placeholder="Search templates…" aria-label="Search templates" value="${escHtml(searchQuery)}">
          <button class="tp-close" aria-label="Close templates">✕</button>
        </div>
        <div class="tp-body">
          <nav class="tp-cats">
            ${CATEGORIES.map(cat => `
              <button class="tp-cat-btn${cat === activeCategory ? " tp-cat-active" : ""}" data-cat="${cat}">
                <span>${CAT_LABELS[cat]}</span>
                <span class="tp-cat-count">${c[cat] || 0}</span>
              </button>
            `).join("")}
            ${activeCategory === "custom" ? `<button class="tp-new-btn" id="tp-new-btn">＋ New</button>` : ""}
          </nav>
          <div class="tp-grid" id="tp-grid">
            ${list.length === 0 ? renderEmpty() : list.map(t => renderCard(t)).join("")}
          </div>
        </div>
      </div>
    `;

    overlay.querySelector(".tp-close").addEventListener("click", close);
    overlay.querySelector("#tp-search").addEventListener("input", (e) => {
      searchQuery = e.target.value;
      render();
    });
    overlay.querySelectorAll(".tp-cat-btn").forEach(btn => {
      btn.addEventListener("click", () => { activeCategory = btn.dataset.cat; render(); });
    });
    overlay.querySelector("#tp-new-btn")?.addEventListener("click", () => newTemplate());
    overlay.querySelector("#tp-empty-new")?.addEventListener("click", () => newTemplate());
    overlay.querySelectorAll(".tp-card").forEach(card => {
      const use = () => useTemplate(card.dataset.id);
      card.addEventListener("click", use);
      // .tp-card is role="button" tabindex="0" — focusable, and until now inert
      // for anyone not using a mouse. A native <button> fires on Enter AND
      // Space; role="button" is a promise to behave like one. Mirrors the
      // .conv-body handler in sidebar_conversations.js.
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          use();
        }
      });
    });
    overlay.querySelectorAll(".tp-edit-btn").forEach(btn => {
      btn.addEventListener("click", (e) => { e.stopPropagation(); editTemplate(btn.dataset.id); });
    });
    overlay.querySelectorAll(".tp-del-btn").forEach(btn => {
      btn.addEventListener("click", (e) => { e.stopPropagation(); deleteTemplate(btn.dataset.id); });
    });
  }

  function renderEmpty() {
    if (activeCategory === "custom") {
      return `<div class="tp-empty">No custom templates yet.<br><button class="tp-btn-link" id="tp-empty-new">Create your first one</button></div>`;
    }
    return `<div class="tp-empty">No templates match. Clear the search box or pick a different category.</div>`;
  }

  function renderCard(t) {
    const preview = t.body.replace(/\{\{[^}]+\}\}/g, "…").substring(0, 80);
    const isCustom = !t.is_builtin;
    // P2-R R1: NVDA read "Weekly Status Reportreport" because the card's whole
    // text is its accessible name. Name the card explicitly so the title and
    // category are separated; the visible DOM is unchanged.
    return `
      <div class="tp-card" data-id="${escHtml(t.id)}" tabindex="0" role="button" aria-label="${escHtml(t.title)}, ${escHtml(CAT_LABELS[t.category] ?? t.category)} category">
        <div class="tp-card-top">
          <span class="tp-card-title">${escHtml(t.title)}</span>
          <span class="tp-card-badge tp-cat-${escHtml(t.category)}">${escHtml(CAT_LABELS[t.category] ?? t.category)}</span>
          ${t.is_builtin ? `<span class="tp-lock" title="Built-in">🔒</span>` : ""}
        </div>
        ${t.description ? `<p class="tp-card-desc">${escHtml(t.description)}</p>` : ""}
        <p class="tp-card-preview">${escHtml(preview)}…</p>
        ${isCustom ? `
          <div class="tp-card-actions">
            <button class="tp-edit-btn" data-id="${escHtml(t.id)}" title="Edit">✎</button>
            <button class="tp-del-btn" data-id="${escHtml(t.id)}" title="Delete">×</button>
          </div>
        ` : ""}
      </div>
    `;
  }

  async function useTemplate(id) {
    const t = templates.find(x => x.id === id);
    if (!t) return;
    close();
    await interpolateTemplate(t.body, onSelect);
  }

  async function newTemplate() {
    const result = await showModal({
      title: "New Template",
      fields: [
        { name: "title", label: "Title", placeholder: "My Template" },
        { name: "description", label: "Description (optional)", placeholder: "" },
        { name: "body", label: "Prompt body (use {{Variable}} for fields)", placeholder: "Write a…" },
        { name: "category", label: "Category (report/email/document/analysis/code)", placeholder: "code" },
      ],
      confirmText: "Create",
    });
    if (!result || !result.title?.trim() || !result.body?.trim()) return;
    await api.createFlowTemplate({
      title: result.title.trim(),
      description: result.description?.trim() || null,
      body: result.body.trim(),
      category: (result.category?.trim() || "code"),
    });
    invalidateTemplates();
    await reload();
  }

  async function editTemplate(id) {
    const t = templates.find(x => x.id === id);
    if (!t) return;
    const result = await showModal({
      title: "Edit Template",
      fields: [
        { name: "title", label: "Title", placeholder: "My Template" },
        { name: "description", label: "Description", placeholder: "" },
        { name: "body", label: "Prompt body", placeholder: "Write a…" },
        { name: "category", label: "Category", placeholder: "code" },
      ],
      confirmText: "Save",
      initial: { title: t.title, description: t.description || "", body: t.body, category: t.category },
    });
    if (!result) return;
    await api.updateFlowTemplate(id, {
      title: result.title.trim(),
      description: result.description?.trim() || null,
      body: result.body.trim(),
      category: result.category?.trim() || t.category,
    });
    invalidateTemplates();
    await reload();
  }

  async function deleteTemplate(id) {
    const t = templates.find(x => x.id === id);
    if (!t) return;
    const ok = await showConfirm({ message: `Delete "${t.title}"?`, confirmText: "Delete", danger: true });
    if (!ok) return;
    await api.deleteFlowTemplate(id);
    invalidateTemplates();
    await reload();
  }

  async function reload() {
    templates = await getTemplates();
    render();
  }

  // Initial load
  document.body.appendChild(overlay);
  getTemplates()
    .then(ts => { templates = ts; render(); })
    .catch(() => {
      overlay.querySelector(".tp-grid").innerHTML = `<div class="tp-empty">Could not load templates. Check your connection, then reopen the template picker.</div>`;
    });
}

// escHtml must be available in this module — reuse the global defined in the page or define inline
function escHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
