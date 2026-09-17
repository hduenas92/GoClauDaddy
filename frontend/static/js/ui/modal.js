/**
 * Cyberpunk-styled modal dialogs — replaces native prompt() / confirm().
 * showModal → resolves with { fieldName: value } or null (cancelled).
 * showConfirm → resolves with true or false.
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
    overlay._reject = () => { overlay.remove(); resolve(false); };

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
