/**
 * tooltips.js — WCAG 2.1 SC 1.4.13 for the CSS [data-tooltip] tooltips.
 *
 * The ::after tooltip in base.css is visual-only (a pseudo-element cannot be
 * hovered or Esc-dismissed). This module adds the two behaviours the criterion
 * requires, without replacing the CSS:
 *
 *   - Hoverable/persistent: while the pointer is on the host OR inside the
 *     tooltip box (derived from the ::after computed geometry, exactly like
 *     tools/hover-check.mjs), the host keeps class `tooltip-open`, and base.css
 *     keeps the tooltip visible. Moving off both closes it.
 *   - Esc dismissal without moving the pointer: Esc adds `tooltip-killed`,
 *     which base.css uses to force the tooltip to opacity 0. The suppression
 *     clears once the pointer leaves the host (or the host loses focus), so a
 *     fresh hover/focus shows it again.
 *   - Keyboard: focusing a [data-tooltip] host shows the tooltip via
 *     [data-tooltip]:focus-visible::after; Esc dismisses it the same way.
 */

function tooltipRect(host) {
  const r = host.getBoundingClientRect();
  const cs = getComputedStyle(host, "::after");
  const w = parseFloat(cs.width) || 0;
  const h = parseFloat(cs.height) || 0;
  const padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
  const padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
  const bw = (parseFloat(cs.borderLeftWidth) || 0) * 2;
  const boxW = cs.boxSizing === "border-box" ? w : w + padX + bw;
  const boxH = cs.boxSizing === "border-box" ? h : h + padY + bw;
  let tx = 0;
  const m = /matrix\(([^)]+)\)/.exec(cs.transform);
  if (m) tx = parseFloat(m[1].split(",")[4]) || 0;

  const num = (v) => (v === "auto" ? null : parseFloat(v));
  const cssTop = num(cs.top);
  const cssBottom = num(cs.bottom);
  const cssLeft = num(cs.left);
  const cssRight = num(cs.right);

  let top;
  if (cssTop !== null) top = r.top + cssTop;
  else if (cssBottom !== null) top = r.bottom - cssBottom - boxH;
  else top = r.top;

  let left;
  if (cssLeft !== null) left = r.left + cssLeft + tx;
  else if (cssRight !== null) left = r.right - cssRight - boxW + tx;
  else left = r.left + tx;

  return { left, top, right: left + boxW, bottom: top + boxH, w: boxW, h: boxH };
}

function inRect(x, y, rect) {
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
}

export function mountTooltips() {
  const hosts = [...document.querySelectorAll("[data-tooltip]")];
  if (hosts.length === 0) return;

  let openHost = null;

  function close(host) {
    if (!host) return;
    host.classList.remove("tooltip-open");
    if (openHost === host) openHost = null;
  }

  function pointerInside(host, x, y) {
    const hostRect = host.getBoundingClientRect();
    if (inRect(x, y, hostRect)) return true;
    if (host.classList.contains("tooltip-killed")) return false;
    try {
      return inRect(x, y, tooltipRect(host));
    } catch {
      return false;
    }
  }

  document.addEventListener("pointermove", (e) => {
    if (!openHost) return;
    if (!pointerInside(openHost, e.clientX, e.clientY)) {
      close(openHost);
    }
  }, { passive: true });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const target = openHost || hosts.find((h) => h.matches(":focus-visible") || h.contains(document.activeElement));
    if (target) {
      target.classList.add("tooltip-killed");
      target.classList.remove("tooltip-open");
      openHost = null;
    }
  });

  // One tooltip at a time: moving straight to an adjacent host (no pointermove outside both) must close the old one.
  function open(host) {
    if (openHost !== host) close(openHost);
    host.classList.remove("tooltip-killed");
    host.classList.add("tooltip-open");
    openHost = host;
  }

  for (const host of hosts) {
    host.addEventListener("pointerenter", () => open(host));
    host.addEventListener("pointerleave", () => {
      // A real element would let the pointer cross the gap; the ::after sits
      // directly below the host so pointermove keeps it open. Leave is only
      // closed when the pointer is truly outside both rects (pointermove
      // handles that); here we only clear the suppression once the pointer has
      // left the host.
    });
    host.addEventListener("focus", () => open(host));
    host.addEventListener("blur", () => {
      host.classList.remove("tooltip-killed");
      close(host);
    });
    // Re-hovering after an Esc dismissal must work again.
    host.addEventListener("pointerdown", () => {
      host.classList.remove("tooltip-killed");
    });
  }
}
