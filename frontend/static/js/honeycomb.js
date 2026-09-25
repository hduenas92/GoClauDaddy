(function () {
  const canvas = document.getElementById('honeycombCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let width = 0, height = 0, dpr = 1;
  const hexRadius = 28;
  const hexWidth = Math.sqrt(3) * hexRadius;
  const hexHeight = 2 * hexRadius;
  const vertDist = hexHeight * 0.75;
  const horizDist = hexWidth;

  const mouse = { x: -1000, y: -1000, targetX: -1000, targetY: -1000, isHovering: false, radius: 140 };

  /* ---- theme colors come from the CSS token contract, not literals ----
     The grid used to hardcode rgba(45,212,191) and rgba(20,184,166), which meant
     a theme switch changed the whole app except this canvas. Read the channel
     triplets instead and re-read them when the theme attribute changes. */
  let accentRgb = '0 163 165';
  let accentHiRgb = '45 212 191';

  function readThemeColors() {
    const cs = getComputedStyle(document.documentElement);
    const base = cs.getPropertyValue('--accent-rgb').trim();
    const hi = cs.getPropertyValue('--accent-hi-rgb').trim();
    if (base) accentRgb = base;
    if (hi) accentHiRgb = hi;
  }
  readThemeColors();

  // A theme switch flips data-theme on <html>; pick up the new tokens.
  new MutationObserver(readThemeColors).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme', 'class'],
  });

  /* ---- reduced motion ----
     prefers-reduced-motion means no animation loop at all: draw the grid once,
     statically, and stop. Users who ask for less motion get a still background
     rather than a slower one, and idle CPU drops to zero. */
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let reduceMotion = motionQuery.matches;

  /* ---- 2.2.2 settle: animate on load / while Claude streams / on user
     activity, then settle to a STILL frame after 5 s of no activity. When
     settled, the rAF loop is CANCELLED (0 callbacks), not merely throttled;
     activity restarts it and the 5 s timer starts again. ---- */
  const SETTLE_MS = 5000;
  let active = false;          // is the rAF loop supposed to be running?
  let rafId = null;
  let settleTimer = null;
  let time = 0;                // wave phase — must precede resize()/noteActivity()
  let lastFrame = 0;           // frame clock, reset when the loop restarts

  function resize() {
    dpr = window.devicePixelRatio || 1;
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    if (reduceMotion) { drawGrid(); return; }
    // A resize is user activity, and a settled canvas must repaint immediately
    // instead of waiting for the next frame that would never come.
    noteActivity();
    drawGrid();
  }

  window.addEventListener('resize', resize);
  resize();

  if (!reduceMotion) {
    window.addEventListener('mousemove', (e) => {
      mouse.targetX = e.clientX;
      mouse.targetY = e.clientY;
      mouse.isHovering = true;
      noteActivity();   // pointer movement is user activity (2.2.2 settle timer)
    });
    // Clear hover state on every path out of the document, not just mouseleave.
    // mouseleave alone does not fire when the pointer exits into devtools or
    // another window, which left the grid stuck highlighting its last position.
    const clearHover = () => { mouse.isHovering = false; };
    window.addEventListener('mouseleave', clearHover);
    document.addEventListener('mouseout', (e) => { if (!e.relatedTarget) clearHover(); });
    window.addEventListener('blur', clearHover);
    document.addEventListener('visibilitychange', () => { if (document.hidden) clearHover(); });

    // Other user activity that restarts the settle timer: typing, clicking,
    // scrolling and touch. These are the same sources a human generates while
    // actually using the app — hover alone would settle while a keyboard user
    // was still working.
    const activityEvents = ['keydown', 'pointerdown', 'pointermove', 'wheel', 'touchstart'];
    activityEvents.forEach((type) =>
      window.addEventListener(type, noteActivity, { passive: true }));
  }

  motionQuery.addEventListener('change', (e) => {
    reduceMotion = e.matches;
    if (reduceMotion) {
      mouse.isHovering = false;
      clearTimeout(settleTimer);
      if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
      active = false;
      drawGrid();               // settle to a static frame; the rAF loop stays off
    } else {
      noteActivity();           // motion re-enabled -> animate again, settle in 5 s
    }
  });

  function getHexCorners(cx, cy, r, tiltOffset) {
    const pts = [];
    for (let i = 0; i < 6; i++) {
      const angle = (Math.PI / 180) * (60 * i - 30);
      pts.push({ x: cx + r * Math.cos(angle), y: cy + (r * 0.85) * Math.sin(angle) + tiltOffset });
    }
    return pts;
  }

  function drawGrid() {
    ctx.clearRect(0, 0, width, height);

    const cols = Math.ceil(width / horizDist) + 2;
    const rows = Math.ceil(height / vertDist) + 2;

    for (let row = -1; row < rows; row++) {
      const yOffset = row * vertDist;
      const xOffsetOdd = (row % 2 !== 0) ? (horizDist / 2) : 0;
      for (let col = -1; col < cols; col++) {
        const cx = col * horizDist + xOffsetOdd;
        const cy = yOffset;
        let intensity = 0, tilt = 0;

        if (!reduceMotion) {
          const dx = mouse.x - cx, dy = mouse.y - cy;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (mouse.isHovering && dist < mouse.radius) {
            const norm = 1 - (dist / mouse.radius);
            intensity = Math.pow(norm, 2.2);
            tilt = -intensity * 1.2;
          } else {
            tilt = Math.sin(time + (cx + cy) * 0.008) * 0.25;
          }
        }

        const corners = getHexCorners(cx, cy, hexRadius * 0.94, tilt);
        ctx.beginPath();
        ctx.moveTo(corners[0].x, corners[0].y);
        for (let i = 1; i < 6; i++) ctx.lineTo(corners[i].x, corners[i].y);
        ctx.closePath();

        if (intensity > 0.01) {
          const alpha = 0.035 + intensity * 0.16;
          ctx.strokeStyle = `rgba(${accentHiRgb} / ${alpha})`;
          ctx.lineWidth = 0.8 + intensity * 0.6;
          ctx.stroke();
          if (intensity > 0.4) {
            ctx.fillStyle = `rgba(${accentRgb} / ${intensity * 0.03})`;
            ctx.fill();
          }
        } else {
          ctx.strokeStyle = `rgba(${accentRgb} / 0.032)`;
          ctx.lineWidth = 0.75;
          ctx.stroke();
        }
      }
    }
  }

  /* MEASURED COST, and the reason for everything below this comment.
     tools/perf-check.mjs case 4 measured this loop at 15.4% of wall time in
     tasks while the app sat completely idle. The same measurement with the
     loop stopped is 0.26% — so the honeycomb was not merely the largest idle
     cost, it was effectively the ONLY one. Target is under 5%.

     Two changes, neither of which is visible:

     1. A fixed frame rate instead of whatever rAF offers (60 on most
        displays, 120 on some). The frame budget below skips the work on
        intervening callbacks rather than cancelling the loop, so nothing has
        to be restarted. `time` advances per RENDERED frame, scaled by
        60 / FPS, which keeps the wave moving at exactly the speed it did at
        60fps — lowering the frame rate without that scaling visibly slows the
        animation, which is a different change from the one intended.

        30fps was measured first and gave 6.71%, still over target; 20fps is
        what actually lands under it. The motion is a slow ambient wave, which
        is why this is imperceptible where it would not be on, say, a cursor.

     2. Nothing is drawn while the window is unfocused or the tab is hidden.
        Browsers throttle rAF in BACKGROUND TABS, but a visible, unfocused
        window keeps running at full rate — which is most of the time this app
        is open. `blur` already cleared the hover highlight; now it stops the
        drawing too.

     The loop keeps ticking while paused rather than exiting, so focus returns
     to a live animation with no restart logic to get wrong. A paused tick is
     one comparison and a rAF call. */
  const FPS = 15;
  const FRAME_MS = 1000 / FPS;
  // Per RENDERED frame, scaled so the wave keeps the speed it had at 60fps:
  // 0.008 × (60 / FPS). Changing FPS without changing this visibly changes how
  // fast the animation moves, which is a different edit from the one intended.
  const TIME_STEP = 0.008 * (60 / FPS);

  /* Focus is TRACKED, not polled.
     The first version called `document.hasFocus()` inside render(), i.e. on
     every rAF callback — 60 to 120 times a second regardless of the frame
     budget, because the callback still fires at display rate even when the
     frame is skipped. hasFocus() is a synchronous query into the browser's
     window state, not a variable read, and it showed: dropping 30fps to 20fps
     moved the measurement only 6.71% -> 6.25%, nothing like the 15.4% -> 6.7%
     that halving the rate had just produced. The remaining cost was not the
     drawing at all.

     Events give the same answer for free. `document.hidden` stays a direct
     read because it is a plain property. */
  let hasFocus = document.hasFocus();
  window.addEventListener('focus', () => { hasFocus = true; noteActivity(); });
  window.addEventListener('blur', () => { hasFocus = false; });

  function render(now) {
    if (reduceMotion || !active) { rafId = null; return; }  // stop the loop entirely
    rafId = requestAnimationFrame(render);
    if (document.hidden || !hasFocus) return;
    if (now - lastFrame < FRAME_MS) return;
    lastFrame = now;
    time += TIME_STEP;
    mouse.x += (mouse.targetX - mouse.x) * 0.08;
    mouse.y += (mouse.targetY - mouse.y) * 0.08;
    drawGrid();
  }

  /* Activity keeps the loop alive; inactivity settles it. settle() CANCELS the
     rAF loop — a still honeycomb costs 0 rAF callbacks — then paints one final
     frame so the grid stays visible, frozen at its current phase. */
  function noteActivity() {
    if (reduceMotion) return;
    clearTimeout(settleTimer);
    settleTimer = setTimeout(settle, SETTLE_MS);
    if (active) return;
    active = true;
    lastFrame = 0;              // first frame after a restart renders immediately
    rafId = requestAnimationFrame(render);
  }

  function settle() {
    if (reduceMotion) return;
    active = false;
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
    drawGrid();                 // the STILL frame, frozen at the current phase
  }

  // While Claude is working or streaming, that counts as activity too.
  // main.js:269 flips #header-dot to header-dot-streaming while the store says
  // streaming, and chat_pane.js:357 paints .status-processing on the assistant
  // meta. A MutationObserver checks only on DOM changes, so a settled page has
  // no polling loop and no callbacks at all.
  function watchStreamingActivity() {
    const STREAMING_SELECTOR = '.status-processing, #header-dot.header-dot-streaming';
    const markIfStreaming = () => {
      if (document.querySelector(STREAMING_SELECTOR)) noteActivity();
    };
    new MutationObserver(markIfStreaming).observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    });
    markIfStreaming();
  }
  if (!reduceMotion) watchStreamingActivity();

  // Coming back to the window should not wait for the next scheduled frame,
  // and should not jump: reset the frame clock so the first frame after focus
  // renders immediately rather than appearing to stutter. Returning is also
  // activity, so the settle timer restarts.
  const resume = () => { lastFrame = 0; noteActivity(); };
  window.addEventListener('focus', resume);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) resume(); });

  if (reduceMotion) drawGrid();
  else noteActivity();
})();
