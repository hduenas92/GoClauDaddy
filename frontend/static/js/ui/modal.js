/**
 * Cyberpunk-styled modal dialogs — replaces native prompt() / confirm().
 * showModal → resolves with { fieldName: value } or null (cancelled).
 * showConfirm → resolves with true or false.
 * showErrorToast → the one shared surface for non-boot failures (task 2.4).
 */

function _buildOverlay() {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  document.body.appendChild(overlay);

  // Close on backdrop click
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) overlay._reject();
  });
  return overlay;
}


// Anything a keyboard user can land on. `[tabindex="-1"]` is excluded on purpose:
// script can focus it, Tab cannot reach it, so it is not part of the sequence a
// trap has to contain. Kept identical to the selector tools/focus-check.mjs
// sweeps with, so the instrument and the implementation cannot disagree about
// what "the last control" means.
const FOCUSABLE =
  'button, a[href], input:not([type=hidden]), select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Keep Tab inside `root` while it is open. Returns a release function.
 *
 * Without this, Tab from the last control walks into the page behind the dialog
 * — a keyboard user is then operating controls they cannot see, underneath an
 * overlay they believe has their attention. Measured on this app before the fix:
 * focus escaped to `.cost-toast-close`, a live button behind the modal.
 *
 * Listens on `document` in the CAPTURE phase rather than on `root`, because
 * focus may already be outside `root` by the time Tab is pressed; a listener
 * bound to `root` never sees that keystroke. Capture also runs before the app's
 * own document-level shortcut handler.
 *
 * Visibility is tested with getClientRects(), NOT offsetParent: overlays are
 * position:fixed, and offsetParent is null for fixed elements, which would
 * filter out every control and silently disable the trap.
 */
export function trapFocus(root) {
  const onKey = (e) => {
    if (e.key !== "Tab" || !root.isConnected) return;
    const els = [...root.querySelectorAll(FOCUSABLE)]
      .filter((el) => !el.disabled && el.getClientRects().length > 0);
    if (els.length === 0) return;          // nothing to trap; do not hijack Tab
    const first = els[0];
    const last = els[els.length - 1];
    const active = document.activeElement;
    const inside = root.contains(active);
    if (e.shiftKey) {
      if (!inside || active === first) { e.preventDefault(); last.focus(); }
    } else if (!inside || active === last) {
      e.preventDefault();
      first.focus();
    }
  };
  document.addEventListener("keydown", onKey, true);
  return () => document.removeEventListener("keydown", onKey, true);
}

export function showModal({ title, fields = [], confirmText = "Save", danger = false, initial = {} }) {
  return new Promise((resolve) => {
    const overlay = _buildOverlay();
    overlay._reject = () => { overlay.remove(); resolve(null); };

    const box = document.createElement("div");
    box.className = "modal-box";
    box.innerHTML = `
      <h3 class="modal-title">${escHtml(title)}</h3>
      <div class="modal-fields">
        ${fields.map((f) => `
          <label class="modal-label">
            <span>${escHtml(f.label)}</span>
            ${f.type === "textarea"
              ? `<textarea class="modal-input" name="${escHtml(f.name)}" rows="4" placeholder="${escHtml(f.placeholder || "")}">${escHtml(initial[f.name] || f.value || "")}</textarea>`
              : `<input class="modal-input" type="${f.type || "text"}" name="${escHtml(f.name)}" value="${escHtml(initial[f.name] !== undefined ? initial[f.name] : (f.value || ""))}" placeholder="${escHtml(f.placeholder || "")}">`
            }
          </label>
        `).join("")}
      </div>
      <div class="modal-actions">
        <button class="modal-btn modal-cancel">Cancel</button>
        <button class="modal-btn modal-confirm${danger ? " danger" : ""}">${escHtml(confirmText)}</button>
      </div>
    `;
    overlay.appendChild(box);

    const releaseTrap = trapFocus(overlay);
    const _reject = overlay._reject;
    overlay._reject = () => { releaseTrap(); _reject(); };

    const inputs = box.querySelectorAll(".modal-input");
    if (inputs.length) inputs[0].focus();

    box.querySelector(".modal-cancel").addEventListener("click", () => overlay._reject());

    box.querySelector(".modal-confirm").addEventListener("click", () => {
      const values = {};
      inputs.forEach((el) => { values[el.name] = el.value; });
      overlay.remove();
      resolve(values);
    });

    // Enter submits (when not in textarea)
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") {
        e.preventDefault();
        box.querySelector(".modal-confirm").click();
      }
      if (e.key === "Escape") overlay._reject();
    });
  });
}

export function showConfirm({ message, confirmText = "Delete", danger = true }) {
  return new Promise((resolve) => {
    const overlay = _buildOverlay();
    const releaseTrap = trapFocus(overlay);
    overlay._reject = () => { releaseTrap(); overlay.remove(); resolve(false); };

    const box = document.createElement("div");
    box.className = "modal-box";
    box.innerHTML = `
      <p class="modal-confirm-msg">${escHtml(message)}</p>
      <div class="modal-actions">
        <button class="modal-btn modal-cancel">Cancel</button>
        <button class="modal-btn modal-confirm${danger ? " danger" : ""}">${escHtml(confirmText)}</button>
      </div>
    `;
    overlay.appendChild(box);

    box.querySelector(".modal-cancel").addEventListener("click", () => overlay._reject());
    box.querySelector(".modal-confirm").addEventListener("click", () => {
      overlay.remove();
      resolve(true);
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { overlay.remove(); resolve(true); }
      if (e.key === "Escape") overlay._reject();
    });

    box.querySelector(".modal-confirm").focus();
  });
}

function escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Moved out of chat_pane.js (was module-private there) so every non-boot
// failure in the app — not just chat turns — can show through one surface
// instead of native blocking dialogs, silent catches, or ad-hoc CSS flashes.
// (Worded to avoid the literal string the Phase 2 exit-gate grep sweeps for.)
export function showErrorToast(message) {
  const toast = document.createElement("div");
  toast.className = "error-toast";
  toast.innerHTML = `
    <div class="error-toast-content">
      <span class="error-toast-icon">⚠</span>
      <span>${escHtml(message)}</span>
      <button class="error-toast-close" aria-label="Close">✕</button>
    </div>
  `;
  document.body.appendChild(toast);
  const close = () => { toast.remove(); };
  toast.querySelector(".error-toast-close").addEventListener("click", close);
  setTimeout(close, 8000);
}
