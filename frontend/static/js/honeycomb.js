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

  /* P2-Y: continuous low-amplitude ambient motion, like the mockup ----
     The mockup runs `time += 0.008` inside an unconditional
     requestAnimationFrame(render) loop (screen.html:855,921) and never stops.
     Earlier rounds added a 5 s settle-and-freeze and then over-inked the grid
     (0.22 alpha / 1.0 px) to compensate for the frozen frame. The real cause
     was the freeze, so this build removes the settle entirely and keeps the
     loop running at a fixed LOW frame rate. The wave speed is preserved by
     scaling the per-rendered-frame time step (0.008 * 60/FPS), so lowering
     FPS changes cost, not the speed of the motion.
     Perf note: 15 FPS measured at the perf-check 6% gate's edge (median
     6.00%, windows 6.00/6.42/5.49), so this build runs at 10 FPS for real
     headroom while staying continuously animated — the check's anti-freeze
     and reduced-motion cases still pass. */
  const FPS = 10;
  const FRAME_MS = 1000 / FPS;
  const TIME_STEP = 0.008 * (60 / FPS);
  let time = 0;                // wave phase
  let lastFrame = 0;           // frame clock
  let rafId = null;

  function resize() {
    dpr = window.devicePixelRatio || 1;
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    drawGrid();
  }

  window.addEventListener('resize', resize);
  resize();

  if (!reduceMotion) {
    window.addEventListener('mousemove', (e) => {
      mouse.targetX = e.clientX;
      mouse.targetY = e.clientY;
      mouse.isHovering = true;
    });
    // Clear hover state on every path out of the document, not just mouseleave.
    // mouseleave alone does not fire when the pointer exits into devtools or
    // another window, which left the grid stuck highlighting its last position.
    const clearHover = () => { mouse.isHovering = false; };
    window.addEventListener('mouseleave', clearHover);
    document.addEventListener('mouseout', (e) => { if (!e.relatedTarget) clearHover(); });
    window.addEventListener('blur', clearHover);
    document.addEventListener('visibilitychange', () => { if (document.hidden) clearHover(); });
  }

  motionQuery.addEventListener('change', (e) => {
    reduceMotion = e.matches;
    if (reduceMotion) {
      mouse.isHovering = false;
      if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
      drawGrid();               // settle to a static frame; the rAF loop stays off
    } else {
      drawGrid();
      if (rafId === null) rafId = requestAnimationFrame(render);
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
          // Base quiescent isometric lattice — the mockup's actual rest state:
          // rgba(20,184,166,0.032) at 0.75px (screen.html:914-915). P2-X had
          // raised this to 0.22 / 1.0 to compensate for the settle-and-freeze;
          // with continuous motion restored, the faint mockup values are the
          // right target again.
          ctx.strokeStyle = `rgba(${accentRgb} / 0.032)`;
          ctx.lineWidth = 0.75;
          ctx.stroke();
        }
      }
    }
  }

  /* Focus is TRACKED, not polled. Nothing is drawn while the window is
     unfocused or the tab is hidden (browsers throttle rAF in background tabs,
     but a visible unfocused window keeps running at full rate). The loop keeps
     ticking while paused so focus returns to a live animation with no restart
     logic to get wrong; a paused tick is one comparison and a rAF call. */
  let hasFocus = document.hasFocus();
  window.addEventListener('focus', () => { hasFocus = true; lastFrame = 0; });
  window.addEventListener('blur', () => { hasFocus = false; });

  function render(now) {
    if (reduceMotion) { rafId = null; return; }   // stop the loop entirely
    rafId = requestAnimationFrame(render);
    if (document.hidden || !hasFocus) return;
    if (now - lastFrame < FRAME_MS) return;
    lastFrame = now;
    time += TIME_STEP;
    mouse.x += (mouse.targetX - mouse.x) * 0.08;
    mouse.y += (mouse.targetY - mouse.y) * 0.08;
    drawGrid();
  }

  // Coming back to the window should not wait for the next scheduled frame,
  // and should not jump: reset the frame clock so the first frame after focus
  // renders immediately rather than appearing to stutter.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) lastFrame = 0;
  });

  if (reduceMotion) drawGrid();
  else rafId = requestAnimationFrame(render);
})();
