/**
 * onboarding_tour.js — 4-step intro overlay shown once on first visit.
 * Exports: showOnboarding(), maybeShowOnboarding()
 */

const STEPS = [
  {
    icon: "◈",
    title: "Welcome to GoClaudaddy",
    body: "Your private Claude interface. Conversations stay on your machine, powered by your GoDaddy account.",
  },
  {
    icon: "⬡",
    title: "Projects",
    body: "Group related conversations together. Each project keeps a working folder and instructions for Claude.",
  },
  {
    icon: "◉",
    title: "Models",
    body: "Choose how capable Claude should be for each conversation. More capable means more thoughtful, but slower.",
  },
  {
    icon: "◎",
    title: "Memory",
    body: "Claude can remember context across conversations automatically. No setup needed.",
  },
];

function _mark() {
  localStorage.setItem("gca_onboarded", "1");
}

export function maybeShowOnboarding() {
  if (localStorage.getItem("gca_onboarded") === "1") return;
  showOnboarding();
}

export function showOnboarding() {
  const overlay = document.createElement("div");
  overlay.className = "ob-overlay";

  let step = 0;

  function render() {
    const s = STEPS[step];
    const isFirst = step === 0;
    const isLast = step === STEPS.length - 1;
    const dots = STEPS.map((_, i) =>
      `<span class="ob-dot${i === step ? " ob-dot-active" : ""}"></span>`
    ).join("");

    overlay.innerHTML = `
      <div class="ob-box" role="dialog" aria-modal="true" aria-label="Getting started tour">
        <button class="ob-skip" type="button">Skip tour</button>
        <div class="ob-icon">${s.icon}</div>
        <h2 class="ob-title">${s.title}</h2>
        <p class="ob-body">${s.body}</p>
        <div class="ob-dots">${dots}</div>
        <div class="ob-actions">
          ${!isFirst ? `<button class="ob-btn ob-btn-ghost" data-action="back">Back</button>` : `<span></span>`}
          ${isLast
            ? `<button class="ob-btn ob-btn-primary" data-action="finish">Get Started</button>`
            : `<button class="ob-btn ob-btn-primary" data-action="next">Next</button>`
          }
        </div>
      </div>
    `;

    overlay.querySelector(".ob-skip").addEventListener("click", dismiss);
    overlay.querySelector("[data-action='back']")?.addEventListener("click", () => { step--; render(); });
    overlay.querySelector("[data-action='next']")?.addEventListener("click", () => { step++; render(); });
    overlay.querySelector("[data-action='finish']")?.addEventListener("click", dismiss);

    // `aria-modal` announces modality to assistive tech; it does NOT move or trap
    // focus. Without this, focus stays wherever it already was — and since
    // main.js runs `boot().then(maybeShowOnboarding)`, the composer has already
    // taken focus during boot. Typing during the tour then went into a textarea
    // hidden behind the overlay. Move focus into the dialog explicitly.
    overlay.querySelector(".ob-btn-primary")?.focus();
  }

  function dismiss() {
    _mark();
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  }

  function onKey(e) {
    if (e.key === "Escape") { dismiss(); return; }
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      if (step < STEPS.length - 1) { step++; render(); }
    }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      if (step > 0) { step--; render(); }
    }
  }

  document.addEventListener("keydown", onKey);
  // Attach BEFORE the first render. render() calls .focus() on the primary
  // button, and focus() is a silent no-op on a detached element — so with the
  // old order (render, then append) focus never moved into the dialog at all.
  document.body.appendChild(overlay);
  render();
}
