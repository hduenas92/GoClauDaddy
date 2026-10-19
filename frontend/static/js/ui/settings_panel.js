import { api } from "../api/http.js";
import { showOnboarding } from "./onboarding_tour.js";
import { showDialog, showErrorToast } from "./modal.js";
// One definition of the key and of what counts as a usable budget. The panel
// writes exactly what the sidebar reads; keeping two copies of a storage key
// in sync by hand is how a setting silently stops taking effect.
import { BUDGET_KEY } from "./right_sidebar.js";
import * as storage from "../state/storage.js";
import { applyPanels, setUiMode, uiMode } from "./panel_registry.js";

/**
 * J7: Settings is a POPUP CARD built on the shared dialog shell (ui/modal.js),
 * the same family as the approval card. The drawer it replaces is gone: one
 * overlay dialect, one focus trap, one close path.
 *
 * Save/Reset is the contract: typing in the prompt or the allowance writes
 * nothing. Save commits both fields in one click; Reset only puts the defaults
 * back into the form. Closing discards unsaved edits.
 *
 * Theme registry — single source of truth is window.GCA_THEMES, set by the
 * bootstrap script in index.html (which also applies data-theme before first
 * paint). Fall back to the shipping theme alone if it's somehow missing.
 */
const THEME_STORAGE_KEY = "gca_theme";
// F3: chat font size. The key is read by the bootstrap script in index.html
// (pre-paint) and written here; the CSS variable it feeds lives in base.css
// (:root --chat-font-size). Values are literal px sizes so the bootstrap can
// whitelist them without parsing.
const FONT_SIZE_STORAGE_KEY = "gca_chat_font_size";
const FONT_SIZE_OPTIONS = [
  { value: "12px", label: "Small (12px)" },
  { value: "13px", label: "Compact (13px)" },
  { value: "14px", label: "Medium (14px)" },
  { value: "16px", label: "Large (16px)" },
];
// A5 consequence copy: the empty-field consequence has to be stated, not just
// the control's name. tests/test_copy_amendments.py pins the feat-assess tag
// below; nothing pins this text, but the oracle compares "empty" and
// "claude code's own prompt" in it.
const PROMPT_HINT =
  "Replaces Claude Code's own prompt for this chat. An empty field uses Claude Code's own prompt.";

function getThemes() {
  return Array.isArray(window.GCA_THEMES) && window.GCA_THEMES.length
    ? window.GCA_THEMES.filter((t) => !t.special && !t.legacy)
    : [{ id: "lit-workbench", label: "Lit Workbench" }];
}

/**
 * createElement helper for the card body. Text always goes in through
 * textContent, so a stored system prompt can never be parsed as markup.
 */
function _el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (value == null) return;
    if (key === "class") node.className = String(value);
    else if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  });
  (Array.isArray(children) ? children : [children]).forEach((child) => {
    if (child == null) return;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return node;
}

// The open card, module-scope so the triggers in main.js (the header button and
// Ctrl+,) can ask whether it is open and close it without holding a reference.
let _activeCard = null;

export function isSettingsCardOpen() {
  return _activeCard != null;
}

export function closeSettingsCard() {
  if (_activeCard) _activeCard.close({ reason: "close" });
}

/**
 * Open the Settings card for `conversation` (the ACTIVE one, read at open time
 * so it always shows that conversation's values).
 */
export function openSettingsCard(conversation, { onChange } = {}) {
  if (_activeCard) return _activeCard;

  const themes = getThemes();
  const convId = conversation?.id;
  let savedPrompt = conversation?.system_prompt || "";
  let savedBudget = storage.getItem(BUDGET_KEY);

  // --- Mode switch (A2). A view preference, not part of Save/Reset: it applies
  // on change like the theme, and it is present in both modes. -----------------
  const modeToggle = _el("input", {
    type: "checkbox",
    id: "ui-mode-toggle",
    class: "drawer-toggle-check",
    role: "switch",
  });
  modeToggle.checked = uiMode() === "advanced";
  const modeRow = _el("label", { class: "settings-toggle-row" }, [
    modeToggle,
    _el("span", { class: "drawer-toggle-label", text: "Show advanced tools" }),
  ]);
  modeToggle.addEventListener("change", () => {
    setUiMode(modeToggle.checked ? "advanced" : "simple");
    // setUiMode already re-applies; running it again here makes the visibility
    // re-run explicit at the one place the mode is changed from the card.
    applyPanels(document);
  });

  // --- This chat --------------------------------------------------------------
  const maxTokensSelect = _el("select", {
    id: "max-tokens-select",
    class: "drawer-select",
    "data-ui-advanced": "",
  });
  const maxTokensSection = _el("div", { class: "drawer-section", "data-ui-advanced": "" }, [
    _el("label", { class: "drawer-label" }, ["Max output tokens", maxTokensSelect]),
    _el("p", { class: "drawer-hint", text: "Limits Claude's response length. Default lets Claude decide." }),
  ]);

  const systemInput = _el("textarea", {
    id: "system-prompt-input",
    class: "drawer-textarea",
    rows: "5",
    placeholder: "You are a helpful assistant…",
    "aria-describedby": "settings-prompt-hint",
  });
  systemInput.value = savedPrompt;
  const promptSection = _el("div", { class: "drawer-section" }, [
    _el("label", { class: "drawer-label" }, ["System prompt", systemInput]),
    _el("p", { class: "drawer-hint", id: "settings-prompt-hint", text: PROMPT_HINT }),
  ]);

  // --- Always-allow rules (unchanged API, unchanged markup contract) ----------
  const rulesList = _el("div", { class: "drawer-section", "data-testid": "always-rules" }, [
    _el("p", {
      class: "drawer-hint",
      "data-testid": "always-rules-empty",
      text: "No always-allow rules yet.",
    }),
  ]);

  // --- App --------------------------------------------------------------------
  const themeSelect = _el("select", { id: "theme-select", class: "drawer-select" });
  themes.forEach((t) => {
    themeSelect.appendChild(_el("option", { value: t.id, text: t.label }));
  });
  const activeTheme = document.documentElement.getAttribute("data-theme");
  themeSelect.value = themes.some((t) => t.id === activeTheme) ? activeTheme : themes[0].id;
  // Applies immediately, exactly as it did in the drawer (unchanged key).
  themeSelect.addEventListener("change", () => {
    const id = themeSelect.value;
    document.documentElement.setAttribute("data-theme", id);
    try { storage.setItem(THEME_STORAGE_KEY, id); } catch { /* private mode — theme still applied for this session */ }
  });
  const themeSection = _el("div", {
    class: "drawer-section",
    id: "theme-section",
    ...(themes.length > 1 ? {} : { hidden: "" }),
  }, [_el("label", { class: "drawer-label" }, ["Theme", themeSelect])]);

  const fontSelect = _el("select", { id: "chat-font-size", class: "drawer-select" });
  FONT_SIZE_OPTIONS.forEach((o) => {
    fontSelect.appendChild(_el("option", { value: o.value, text: o.label }));
  });
  let storedFont = null;
  try { storedFont = storage.getItem(FONT_SIZE_STORAGE_KEY); } catch { /* private mode */ }
  if (!FONT_SIZE_OPTIONS.some((o) => o.value === storedFont)) {
    storedFont = "14px"; // the :root default; bootstrap keeps a valid value valid
  }
  fontSelect.value = storedFont;
  // F3: writes the same --chat-font-size variable the bootstrap writes on load,
  // so the change applies immediately (unchanged behaviour).
  fontSelect.addEventListener("change", () => {
    const size = fontSelect.value;
    document.documentElement.style.setProperty("--chat-font-size", size);
    try { storage.setItem(FONT_SIZE_STORAGE_KEY, size); } catch { /* private mode — still applied for this session */ }
  });
  const fontSection = _el("div", { class: "drawer-section" }, [
    _el("label", { class: "drawer-label" }, ["Chat font size", fontSelect]),
    _el("p", { class: "drawer-hint", text: "Applies to chat messages and the composer." }),
  ]);

  // Budget. App-wide, not per-conversation. Writes through BUDGET_KEY imported
  // from right_sidebar.js, so the key and the definition of a usable value have
  // exactly one home. It now writes on SAVE only (A5): nothing on input/change.
  const budgetInput = _el("input", {
    type: "number",
    id: "budget-input",
    class: "drawer-select",
    min: "1",
    step: "1",
    placeholder: "200",
  });
  if (savedBudget) budgetInput.value = savedBudget;
  const budgetSection = _el("div", { class: "drawer-section" }, [
    _el("label", { class: "drawer-label" }, ["Your monthly allowance", budgetInput]),
    _el("p", {
      class: "drawer-hint",
      text: "Your monthly allowance in dollars, used for the spend bar. The default is $200; leave it blank if that's yours.",
    }),
  ]);

  // Moved out of the Advanced block: Simple keeps the tour.
  const replayBtn = _el("button", {
    class: "drawer-link-btn",
    id: "replay-onboarding-btn",
    type: "button",
    text: "Replay onboarding tour",
  });
  replayBtn.addEventListener("click", () => {
    closeSettingsCard();
    showOnboarding();
  });
  const replaySection = _el("div", { class: "drawer-section" }, [replayBtn]);

  // --- Advanced: developer-only detail, hidden in Simple by the mode rule -----
  const serverValue = _el("span", { id: "adv-server-address", text: "—" });
  const cliValue = _el("span", { id: "adv-cli-version", text: "—" });
  const advanceBody = _el("div", { class: "drawer-advanced-body" }, [
    _el("p", { class: "drawer-hint" }, ["Server address: ", serverValue]),
    _el("p", { class: "drawer-hint" }, ["Claude CLI version: ", cliValue]),
    _el("p", { class: "drawer-hint", text: "Feature toggles apply immediately." }),
  ]);
  // Static markup, no interpolation. The shipped copy test
  // (tests/test_copy_amendments.py::test_feat_assess_has_no_conflicting_aria_label)
  // reads this tag and requires NO aria-label on it: the visible label is the
  // accessible name.
  const featRow = document.createElement("label");
  featRow.className = "drawer-toggle-row";
  featRow.innerHTML = '<input type="checkbox" id="feat-assess" class="drawer-toggle-check"><span class="drawer-toggle-label">Ask a fast model to rate long or risky messages before sending, and warn you if they look dangerous.</span>';
  advanceBody.appendChild(featRow);
  const advancedDetails = _el("details", {
    class: "drawer-section drawer-advanced",
    id: "advanced-settings-section",
    "data-ui-advanced": "",
  }, [
    _el("summary", { class: "drawer-advanced-summary", text: "Advanced" }),
    advanceBody,
  ]);

  const statusEl = _el("p", { class: "drawer-hint", id: "settings-status", role: "status" });

  const body = _el("div", { class: "settings-card-body" }, [
    modeRow,
    _el("h2", { class: "drawer-section-heading", text: "This chat" }),
    maxTokensSection,
    promptSection,
    _el("h2", { class: "drawer-section-heading", text: "Always-allow rules" }),
    rulesList,
    _el("h2", { class: "drawer-section-heading", text: "App" }),
    themeSection,
    fontSection,
    budgetSection,
    replaySection,
    advancedDetails,
    statusEl,
  ]);

  // --- Save / Reset -----------------------------------------------------------
  async function save() {
    statusEl.textContent = "";

    // Allowance first and all-or-nothing: an unusable amount refuses the whole
    // click, so the prompt is never written while the money field is silently
    // discarded. Blank = the server default, which is a real answer.
    const rawBudget = budgetInput.value.trim();
    if (rawBudget !== "") {
      const n = Number(rawBudget);
      if (!Number.isFinite(n) || n <= 0) {
        budgetInput.setAttribute("aria-invalid", "true");
        statusEl.textContent = "Enter an amount greater than zero, or leave it blank for the default.";
        return;
      }
    }
    budgetInput.removeAttribute("aria-invalid");

    const text = systemInput.value.trim();
    if (text !== savedPrompt) {
      try {
        await api.updateConversationSettings(convId,
          text ? { system_prompt: text } : { clear_system_prompt: true }
        );
      } catch {
        statusEl.textContent = "Couldn't save the system prompt — try again.";
        return;
      }
      savedPrompt = text;
      onChange?.({ system_prompt: text || null });
    }

    if (rawBudget !== (savedBudget ?? "")) {
      if (rawBudget === "") {
        storage.removeItem(BUDGET_KEY);
        savedBudget = null;
      } else {
        // As typed: the sidebar reads one key, and blank means "use the default"
        // rather than an empty string for userBudget() to re-interpret.
        storage.setItem(BUDGET_KEY, rawBudget);
        savedBudget = rawBudget;
      }
      // The sidebar reads this on its next stats refresh; nudge it so MONTH
      // updates while the card is still open.
      window.dispatchEvent(new CustomEvent("gca:budget-changed"));
    }

    statusEl.textContent = "Saved";
  }

  function reset() {
    // Defaults INTO THE FORM only. Save is what commits them.
    systemInput.value = "";
    budgetInput.value = "";
    budgetInput.removeAttribute("aria-invalid");
    statusEl.textContent = "";
  }

  const dialog = showDialog({
    title: "Settings",
    body: [body],
    actions: [[
      { id: "settings-save", className: "modal-btn modal-confirm", label: "Save", onClick: save },
      { id: "settings-reset", className: "modal-btn modal-cancel", label: "Reset", onClick: reset },
      {
        id: "settings-close",
        className: "modal-btn modal-cancel",
        label: "Close",
        attrs: { "aria-label": "Close settings" },
        onClick: () => closeSettingsCard(),
      },
    ]],
    trap: true,
    returnFocus: false,
    closeOnBackdrop: false,
    onClose: () => {
      _activeCard = null;
      // A4: after either close, the trigger has focus back.
      document.getElementById("settings-btn")?.focus();
    },
  });
  dialog.box.id = "settings-card";
  _activeCard = dialog;

  // A4: Escape closes. Kept on the box so it composes with the shared trap and
  // does not fire for Escape presses elsewhere on the page.
  dialog.box.addEventListener("keydown", (e) => {
    if (e.key === "Escape") dialog.close({ reason: "escape" });
  });

  // Only the values that SAVE writes are kept off this list. Everything else
  // here still applies on change, exactly as it did in the drawer.
  const MAX_TOKEN_OPTIONS = [
    { value: "", label: "Default" },
    { value: "1024", label: "1,024 — Short" },
    { value: "4096", label: "4,096 — Medium" },
    { value: "8192", label: "8,192 — Long" },
    { value: "16000", label: "16,000 — Very long" },
    { value: "32000", label: "32,000 — Extended" },
  ];
  MAX_TOKEN_OPTIONS.forEach((o) => {
    maxTokensSelect.appendChild(_el("option", { value: o.value, text: o.label }));
  });
  maxTokensSelect.value = conversation?.max_tokens ? String(conversation.max_tokens) : "";
  maxTokensSelect.addEventListener("change", async () => {
    const val = maxTokensSelect.value;
    try {
      await api.updateConversationSettings(convId,
        val ? { max_tokens: parseInt(val, 10) } : { clear_max_tokens: true }
      );
    } catch { _flashError(maxTokensSelect, "Couldn't change the response length limit — your change wasn't saved. Try again."); }
  });

  const assessCheck = dialog.box.querySelector("#feat-assess");
  if (assessCheck) {
    assessCheck.checked = storage.getItem("gca_feat_assess") === "1";
    assessCheck.addEventListener("change", () => {
      storage.setItem("gca_feat_assess", assessCheck.checked ? "1" : "0");
    });
  }

  _refreshAdvancedInfo(dialog.box);
  _refreshAlwaysRules(dialog.box);
  // Focus starts inside the card, or the first Tab is the only way in.
  modeToggle.focus();

  return dialog;
}

function _flashError(el, message) {
  el.classList.add("drawer-select-error");
  setTimeout(() => el.classList.remove("drawer-select-error"), 2000);
  showErrorToast(message);
}

// C5: Advanced is where the server address and the Claude CLI version live now
// (the header and the SERVER panel no longer show them). Both are read-only and
// "—" until their endpoint answers; refreshed on every card open.
function _fillText(el, value) {
  if (el) el.textContent = value || "—";
}

function _refreshAdvancedInfo(cardEl) {
  const serverEl = cardEl.querySelector("#adv-server-address");
  if (serverEl) {
    fetch("/api/server/info")
      .then((r) => { if (!r.ok) throw new Error(`GET /api/server/info ${r.status}`); return r.json(); })
      .then((info) => _fillText(serverEl, info?.url))
      .catch(() => _fillText(serverEl, null));
  }
  const cliEl = cardEl.querySelector("#adv-cli-version");
  if (cliEl) {
    fetch("/api/cli/info")
      .then((r) => { if (!r.ok) throw new Error(`GET /api/cli/info ${r.status}`); return r.json(); })
      .then((info) => _fillText(cliEl, info?.claude_code_version))
      .catch(() => _fillText(cliEl, null));
  }
}

function _renderAlwaysRules(listEl, rules) {
  const emptyEl = listEl.querySelector("[data-testid=always-rules-empty]");
  listEl.querySelectorAll("[data-testid=always-rule]").forEach((el) => el.remove());
  if (!Array.isArray(rules) || rules.length === 0) {
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;
  rules.forEach((rule) => {
    const row = document.createElement("div");
    row.className = "always-rule";
    row.setAttribute("data-testid", "always-rule");

    const pattern = document.createElement("code");
    pattern.className = "always-rule-pattern";
    pattern.setAttribute("data-testid", "always-rule-pattern");
    pattern.textContent = rule.pattern ?? "";

    const cwd = document.createElement("span");
    cwd.className = "always-rule-cwd";
    cwd.setAttribute("data-testid", "always-rule-cwd");
    cwd.textContent = rule.cwd ?? "";

    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "drawer-link-btn always-rule-revoke";
    revoke.setAttribute("data-testid", "always-rule-revoke");
    revoke.textContent = "Revoke";
    revoke.addEventListener("click", async () => {
      try {
        await api.revokePermissionRule(rule.id);
        await _refreshAlwaysRules(listEl.closest("#settings-card"));
      } catch {
        showErrorToast("Couldn't revoke that rule — try again.");
      }
    });

    row.append(pattern, cwd, revoke);
    listEl.appendChild(row);
  });
}

async function _refreshAlwaysRules(cardEl) {
  const listEl = cardEl?.querySelector("[data-testid=always-rules]");
  if (!listEl) return;
  try {
    _renderAlwaysRules(listEl, await api.listPermissionRules());
  } catch {
    // Keep whatever is currently rendered; a failed reload must not blank the list.
  }
}
