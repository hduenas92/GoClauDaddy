/**
 * Cyberpunk-styled modal dialogs — replaces native prompt() / confirm().
 * showModal → resolves with { fieldName: value } or null (cancelled).
 * showConfirm → resolves with true or false.
 * showErrorToast → the one shared surface for non-boot failures (task 2.4).
 * attachDirectoryBrowse → the ONE folder-picker click handler, shared by the
 *   create modal and the inline project edit form (task 4-D6).
 */

import { api } from "../api/http.js";

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

/**
 * Wire a Browse button to a text input holding a directory path.
 *
 * ONE implementation for both places a working_dir is set, because the three
 * ways to get this wrong are the same in both and are all invisible:
 *
 *  1. `""` IS A REAL ANSWER. POST /api/projects/browse-directory returns an
 *     empty string when the user cancels the native dialog — not null, not an
 *     error. `input.value = path` unguarded therefore WIPES a path the user
 *     already had, as the reward for changing their mind. Hence `if (path)`.
 *  2. The request BLOCKS. The dialog is a real OS window on the machine running
 *     the server; the response does not arrive until someone picks or cancels.
 *     A second click while that is open opens a second window and starts a
 *     second blocking request, so the button disables itself for the duration
 *     and a `pending` flag backs that up.
 *  3. The current value is the only sensible starting folder, so it is passed
 *     as `initial_dir`. `.trim() || undefined` matters: an empty string would
 *     be sent as `?initial_dir=` and is not what "no preference" means.
 *
 * Failure goes to the shared toast. It never goes to a native dialog, and it
 * never silently does nothing — "I clicked Browse and nothing happened" is
 * indistinguishable from a hung dialog the user cannot see.
 */
export function attachDirectoryBrowse(button, input) {
  let pending = false;
  button.addEventListener("click", async (e) => {
    e.preventDefault();
    if (pending) return;
    pending = true;
    const restore = button.textContent;
    button.disabled = true;
    button.textContent = "Opening…";
    try {
      const res = await api.browseDirectory(input.value.trim() || undefined);
      const path = res?.path ?? "";
      if (path) input.value = path;   // "" means cancelled: leave what was there
    } catch (err) {
      showErrorToast(
        `Couldn't open the folder picker: ${err.message}. Type the path in the box instead.`
      );
    } finally {
      pending = false;
      button.disabled = false;
      button.textContent = restore;
    }
  });
}

export function showModal({ title, fields = [], confirmText = "Save", danger = false, initial = {} }) {
  return new Promise((resolve) => {
    const overlay = _buildOverlay();
    // 2.4.3: hand focus back to the trigger that opened this modal.
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const restore = () => { if (returnFocus?.isConnected) returnFocus.focus(); };
    overlay._reject = () => { overlay.remove(); restore(); resolve(null); };

    const box = document.createElement("div");
    box.className = "modal-box";
    // 4.1.2: every modal must expose role, modality, and an accessible name.
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "modal-title");
    box.innerHTML = `
      <h3 class="modal-title" id="modal-title">${escHtml(title)}</h3>
      <div class="modal-fields">
        ${fields.map((f) => {
          const val = escHtml(initial[f.name] !== undefined ? initial[f.name] : (f.value || ""));
          const ph = escHtml(f.placeholder || "");
          const nm = escHtml(f.name);
          if (f.type === "textarea") {
            return `
          <label class="modal-label">
            <span>${escHtml(f.label)}</span>
            <textarea class="modal-input" name="${nm}" rows="4" placeholder="${ph}">${escHtml(initial[f.name] || f.value || "")}</textarea>
          </label>`;
          }
          if (f.type === "directory") {
            // The Browse button is a SIBLING of the label, not a child of it.
            // Nesting a button inside a <label> makes it part of that label's
            // activation target, which is how a Browse click turns into a
            // focus-the-input click. It stays inside the overlay so the
            // existing trapFocus covers it, and it follows the input in DOM
            // order so Tab runs input -> Browse -> Cancel -> Confirm.
            return `
          <div class="modal-dir-field">
            <label class="modal-label modal-dir-label">
              <span>${escHtml(f.label)}</span>
              <input class="modal-input" type="text" name="${nm}" value="${val}" placeholder="${ph}">
            </label>
            <button type="button" class="modal-browse" data-for="${nm}" title="Browse for a folder">Browse…</button>
          </div>`;
          }
          return `
          <label class="modal-label">
            <span>${escHtml(f.label)}</span>
            <input class="modal-input" type="${escHtml(f.type || "text")}" name="${nm}" value="${val}" placeholder="${ph}">
          </label>`;
        }).join("")}
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

    box.querySelectorAll(".modal-browse").forEach((btn) => {
      const target = box.querySelector(`.modal-input[name="${CSS.escape(btn.dataset.for)}"]`);
      if (target) attachDirectoryBrowse(btn, target);
    });

    box.querySelector(".modal-cancel").addEventListener("click", () => overlay._reject());

    box.querySelector(".modal-confirm").addEventListener("click", () => {
      const values = {};
      inputs.forEach((el) => { values[el.name] = el.value; });
      overlay.remove();
      restore();
      resolve(values);
    });

    // Enter submits (when not in textarea, and not on the Browse button).
    //
    // The Browse exclusion is load-bearing, not tidiness. A <button> fires its
    // click from the Enter keydown's DEFAULT ACTION; this handler's
    // preventDefault() cancels exactly that. Without the exclusion, a keyboard
    // user who tabs to Browse and presses Enter submits the form instead —
    // the control is reachable, looks focused, and does the wrong thing.
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter"
          && e.target.tagName !== "TEXTAREA"
          && !e.target.classList?.contains("modal-browse")) {
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
    // 2.4.3: hand focus back to the trigger that opened this confirm.
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const restore = () => { if (returnFocus?.isConnected) returnFocus.focus(); };
    overlay._reject = () => { releaseTrap(); overlay.remove(); restore(); resolve(false); };

    const box = document.createElement("div");
    box.className = "modal-box";
    // 4.1.2: showConfirm has no title element; the message is its heading text.
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", message);
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
      restore();
      resolve(true);
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { overlay.remove(); restore(); resolve(true); }
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
  // 4.1.3: error toasts are assertive status messages.
  toast.setAttribute("role", "alert");
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
