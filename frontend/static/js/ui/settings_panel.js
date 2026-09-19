import { api } from "../api/http.js";
import { showOnboarding } from "./onboarding_tour.js";
import { showErrorToast } from "./modal.js";
// One definition of the key and of what counts as a usable budget. The panel
// writes exactly what the sidebar reads; keeping two copies of a storage key
// in sync by hand is how a setting silently stops taking effect.
import { BUDGET_KEY } from "./right_sidebar.js";

// Keep in sync with PERM_LABELS in main.js
const PERM_LABELS = {
  acceptEdits: "Auto-approve edits",
  auto: "Full auto",
  bypassPermissions: "Bypass all prompts",
  manual: "Manual approval",
  dontAsk: "Never ask",
  plan: "Plan mode",
};

const THINKING_OPTIONS = [
  { value: "", label: "Off" },
  { value: "1024", label: "Quick" },
  { value: "4096", label: "Standard" },
  { value: "10000", label: "Thorough" },
  { value: "32000", label: "Maximum" },
];

const MODEL_DESCRIPTIONS = {
  "claude-opus-5": "Most capable — complex tasks, deep reasoning",
  "claude-sonnet-5": "Balanced — fast and intelligent, best for most work",
  "claude-haiku-4-5-20251001": "Fastest — quick tasks, high throughput",
  "claude-sonnet-4-6": "Smart and efficient — reliable for most tasks",
  "claude-opus-4-5": "Highly capable — advanced reasoning and analysis",
};

/**
 * Mounts settings into a drawer element. Returns { open, close } to control visibility.
 */
// Theme registry — single source of truth is window.GCA_THEMES, set by the
// bootstrap script in index.html (which also applies data-theme before first
// paint). Fall back to the shipping theme alone if it's somehow missing.
const THEME_STORAGE_KEY = "gca_theme";
function getThemes() {
  return Array.isArray(window.GCA_THEMES) && window.GCA_THEMES.length
    ? window.GCA_THEMES
    : [{ id: "lit-workbench", label: "Lit Workbench" }];
}

export async function mountSettingsPanel(drawerEl, conversation, { onChange } = {}) {
  const config = await api.getConfig();
  const themes = getThemes();
  const themeSectionHidden = themes.length > 1 ? "" : "hidden";

  drawerEl.innerHTML = `
    <div class="drawer-header">
      <span class="drawer-title">⚙ Conversation Settings</span>
      <button class="drawer-close" title="Close (Esc)">✕</button>
    </div>
    <div class="drawer-body">
      <div class="drawer-section" id="theme-section" ${themeSectionHidden}>
        <label class="drawer-label">Theme
          <select id="theme-select" class="drawer-select"></select>
        </label>
      </div>
      <div class="drawer-section">
        <label class="drawer-label">Your CaaS budget
          <input type="number" id="budget-input" class="drawer-select" min="1" step="1" placeholder="200">
        </label>
        <p class="drawer-hint">Your monthly allowance in dollars, used for the spend bar. The default is $200; leave blank if that's yours.</p>
      </div>
      <div class="drawer-section">
        <label class="drawer-label">Model
          <select id="model-select" class="drawer-select"></select>
        </label>
        <p id="model-desc" class="drawer-hint"></p>
      </div>
      <div class="drawer-section">
        <label class="drawer-label">Permission mode
          <select id="permission-select" class="drawer-select">
            <option value="">(default)</option>
          </select>
        </label>
      </div>
      <div class="drawer-section">
        <label class="drawer-label">Extended thinking
          <select id="thinking-select" class="drawer-select"></select>
        </label>
        <p class="drawer-hint">Gives Claude more time to reason before answering.</p>
      </div>
      <div class="drawer-section">
        <label class="drawer-label">Max output tokens
          <select id="max-tokens-select" class="drawer-select"></select>
        </label>
        <p class="drawer-hint">Limits Claude's response length. Default lets Claude decide.</p>
      </div>
      <div class="drawer-section">
        <label class="drawer-label">System prompt
          <textarea id="system-prompt-input" class="drawer-textarea" rows="5" placeholder="You are a helpful assistant…"></textarea>
        </label>
        <div class="drawer-hint-row">
          <button id="clear-system-prompt" class="drawer-link-btn">Clear</button>
        </div>
      </div>
      <details class="drawer-section drawer-advanced" id="advanced-settings-section">
        <summary class="drawer-advanced-summary">Advanced</summary>
        <div class="drawer-advanced-body">
          <p class="drawer-hint">Experimental features. Changes take effect on the next conversation.</p>
          <button class="drawer-link-btn" id="replay-onboarding-btn">Replay onboarding tour</button>
          <label class="drawer-toggle-row">
            <input type="checkbox" id="feat-teams" class="drawer-toggle-check">
            <span class="drawer-toggle-label">Enable team sessions</span>
          </label>
          <label class="drawer-toggle-row">
            <input type="checkbox" id="feat-assess" class="drawer-toggle-check">
            <span class="drawer-toggle-label">Evaluate tasks before sending</span>
          </label>
          <label class="drawer-toggle-row">
            <input type="checkbox" id="feat-terminal" class="drawer-toggle-check">
            <span class="drawer-toggle-label">Enable terminal panel <span class="drawer-hint-inline">(Ctrl+\`)</span></span>
          </label>
          <label class="drawer-toggle-row">
            <input type="checkbox" id="feat-approval" class="drawer-toggle-check">
            <span class="drawer-toggle-label">Prompt for tool approval <span class="drawer-badge-exp">experimental</span></span>
          </label>
          <p class="drawer-hint drawer-hint-risk">⚠ Depends on Claude CLI output format and may not work reliably.</p>
        </div>
      </details>
    </div>
  `;

  const themeSelect = drawerEl.querySelector("#theme-select");
  themes.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.label;
    themeSelect.appendChild(opt);
  });
  const activeTheme = document.documentElement.getAttribute("data-theme");
  themeSelect.value = themes.some((t) => t.id === activeTheme) ? activeTheme : themes[0].id;
  themeSelect.addEventListener("change", () => {
    const id = themeSelect.value;
    document.documentElement.setAttribute("data-theme", id);
    try { localStorage.setItem(THEME_STORAGE_KEY, id); } catch { /* private mode — theme still applied for this session */ }
  });

  const modelSelect = drawerEl.querySelector("#model-select");
  const modelDesc = drawerEl.querySelector("#model-desc");
  config.models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.value;
    opt.textContent = m.label;
    modelSelect.appendChild(opt);
  });
  modelSelect.value = conversation.model;
  modelDesc.textContent = MODEL_DESCRIPTIONS[modelSelect.value] || "";

  function _flashError(el, message) {
    el.classList.add("drawer-select-error");
    setTimeout(() => el.classList.remove("drawer-select-error"), 2000);
    showErrorToast(message);
  }

  modelSelect.addEventListener("change", async () => {
    try {
      await api.updateConversationSettings(conversation.id, { model: modelSelect.value });
      modelDesc.textContent = MODEL_DESCRIPTIONS[modelSelect.value] || "";
      onChange?.({ model: modelSelect.value });
    } catch { _flashError(modelSelect, "Couldn't switch models — your change wasn't saved. Try again."); }
  });

  const permissionSelect = drawerEl.querySelector("#permission-select");
  config.permission_modes.forEach((mode) => {
    const opt = document.createElement("option");
    opt.value = mode;
    opt.textContent = PERM_LABELS[mode] ?? mode;
    permissionSelect.appendChild(opt);
  });
  permissionSelect.value = conversation.permission_mode || "";
  permissionSelect.addEventListener("change", async () => {
    try {
      await api.updateConversationSettings(conversation.id, {
        permission_mode: permissionSelect.value || null,
      });
      onChange?.({ permission_mode: permissionSelect.value || null });
    } catch { _flashError(permissionSelect, "Couldn't change the permission mode — your change wasn't saved. Try again."); }
  });

  const thinkingSelect = drawerEl.querySelector("#thinking-select");
  THINKING_OPTIONS.forEach((o) => {
    const opt = document.createElement("option");
    opt.value = o.value;
    opt.textContent = o.label;
    thinkingSelect.appendChild(opt);
  });
  thinkingSelect.value = conversation.thinking_budget ? String(conversation.thinking_budget) : "";
  thinkingSelect.addEventListener("change", async () => {
    const val = thinkingSelect.value;
    try {
      await api.updateConversationSettings(conversation.id,
        val ? { thinking_budget: parseInt(val, 10) } : { clear_thinking_budget: true }
      );
    } catch { _flashError(thinkingSelect, "Couldn't change the thinking budget — your change wasn't saved. Try again."); }
  });

  const MAX_TOKEN_OPTIONS = [
    { value: "", label: "Default" },
    { value: "1024", label: "1,024 — Short" },
    { value: "4096", label: "4,096 — Medium" },
    { value: "8192", label: "8,192 — Long" },
    { value: "16000", label: "16,000 — Very long" },
    { value: "32000", label: "32,000 — Extended" },
  ];
  const maxTokensSelect = drawerEl.querySelector("#max-tokens-select");
  MAX_TOKEN_OPTIONS.forEach((o) => {
    const opt = document.createElement("option");
    opt.value = o.value;
    opt.textContent = o.label;
    maxTokensSelect.appendChild(opt);
  });
  maxTokensSelect.value = conversation.max_tokens ? String(conversation.max_tokens) : "";
  maxTokensSelect.addEventListener("change", async () => {
    const val = maxTokensSelect.value;
    try {
      await api.updateConversationSettings(conversation.id,
        val ? { max_tokens: parseInt(val, 10) } : { clear_max_tokens: true }
      );
    } catch { _flashError(maxTokensSelect, "Couldn't change the response length limit — your change wasn't saved. Try again."); }
  });

  const systemInput = drawerEl.querySelector("#system-prompt-input");
  systemInput.value = conversation.system_prompt || "";
  systemInput.addEventListener("change", async () => {
    const val = systemInput.value.trim();
    try {
      await api.updateConversationSettings(conversation.id,
        val ? { system_prompt: val } : { clear_system_prompt: true }
      );
    } catch { _flashError(systemInput, "Couldn't save the system prompt — try again."); }
  });

  drawerEl.querySelector("#clear-system-prompt").addEventListener("click", async () => {
    systemInput.value = "";
    try {
      await api.updateConversationSettings(conversation.id, { clear_system_prompt: true });
    } catch { _flashError(systemInput, "Couldn't clear the system prompt — try again."); }
  });

  drawerEl.querySelector(".drawer-close").addEventListener("click", () => closeDrawer(drawerEl));

  drawerEl.querySelector("#replay-onboarding-btn")?.addEventListener("click", () => {
    closeDrawer(drawerEl);
    showOnboarding();
  });

  // Budget. App-wide, not per-conversation, which is why it sits beside Theme
  // rather than among the selects that PATCH the conversation.
  //
  // Writes through BUDGET_KEY imported from right_sidebar.js, so the key and
  // the definition of a usable value have exactly one home. Blank clears the
  // override and returns the bar to the server's default rather than storing
  // an empty string for userBudget() to re-interpret.
  const budgetInput = drawerEl.querySelector("#budget-input");
  if (budgetInput) {
    try {
      const stored = localStorage.getItem(BUDGET_KEY);
      if (stored) budgetInput.value = stored;
    } catch { /* private mode: the field just starts empty */ }

    budgetInput.addEventListener("change", () => {
      const raw = budgetInput.value.trim();
      try {
        if (raw === "") {
          localStorage.removeItem(BUDGET_KEY);
        } else {
          const n = Number(raw);
          if (!Number.isFinite(n) || n <= 0) {
            // Same rule userBudget() applies, surfaced instead of silently
            // ignored: a field that accepts a value the reader will discard
            // tells the user their setting took when it did not.
            _flashError(budgetInput, "Enter a dollar amount greater than zero, or leave it blank for the default.");
            return;
          }
          localStorage.setItem(BUDGET_KEY, String(n));
        }
      } catch {
        _flashError(budgetInput, "Couldn't save that — your browser is blocking local storage.");
        return;
      }
      // The sidebar reads this on its next stats refresh; nudge it so the bar
      // updates while the drawer is still open and the change is visible.
      window.dispatchEvent(new CustomEvent("gca:budget-changed"));
    });
  }

  const assessCheck = drawerEl.querySelector("#feat-assess");
  if (assessCheck) {
    assessCheck.checked = localStorage.getItem("gca_feat_assess") === "1";
    assessCheck.addEventListener("change", () => {
      localStorage.setItem("gca_feat_assess", assessCheck.checked ? "1" : "0");
    });
  }

  const terminalCheck = drawerEl.querySelector("#feat-terminal");
  if (terminalCheck) {
    terminalCheck.checked = localStorage.getItem("gca_feat_terminal") === "1";
    terminalCheck.addEventListener("change", () => {
      localStorage.setItem("gca_feat_terminal", terminalCheck.checked ? "1" : "0");
    });
  }

  const teamsCheck = drawerEl.querySelector("#feat-teams");
  if (teamsCheck) {
    teamsCheck.checked = localStorage.getItem("gca_feat_teams") === "1";
    teamsCheck.addEventListener("change", () => {
      localStorage.setItem("gca_feat_teams", teamsCheck.checked ? "1" : "0");
      window.dispatchEvent(new CustomEvent("gca:feature", { detail: { name: "teams", enabled: teamsCheck.checked } }));
    });
  }

  const approvalCheck = drawerEl.querySelector("#feat-approval");
  if (approvalCheck) {
    approvalCheck.checked = localStorage.getItem("gca_feat_approval") === "1";
    approvalCheck.addEventListener("change", () => {
      localStorage.setItem("gca_feat_approval", approvalCheck.checked ? "1" : "0");
    });
  }

  return {
    open: () => openDrawer(drawerEl),
    close: () => closeDrawer(drawerEl),
  };
}

export function openDrawer(el) {
  el.classList.add("drawer-open");
  el.setAttribute("aria-hidden", "false");
}

export function closeDrawer(el) {
  el.classList.remove("drawer-open");
  el.setAttribute("aria-hidden", "true");
}
