/**
 * ui-check.mjs — browser-side verification for GoClaudaddy.
 *
 * The pytest suite proves backend units. smoke.ps1 proves the server boots.
 * Neither can tell you whether a color renders, a contrast ratio passes, or a
 * line is 150 characters wide. This does.
 *
 * Everything here is a MEASUREMENT, not an opinion. It reads computed styles
 * out of a real Chromium, composites actual rendered backgrounds, and computes
 * WCAG ratios from the pixels the user would see.
 *
 *   node tools/ui-check.mjs --baseline     write tools/baseline.json
 *   node tools/ui-check.mjs                check against baseline + rules
 *   node tools/ui-check.mjs --shot out.png screenshot too
 *   node tools/ui-check.mjs --reduced      emulate prefers-reduced-motion
 *   node tools/ui-check.mjs --width 2560   viewport width (default 1440)
 *
 * Exit 0 = all rules passed. Exit 1 = at least one failed.
 */

import { chromium } from 'playwright';
import { writeFileSync, readFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const BASELINE = resolve(HERE, 'baseline.json');
const BASE_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

const argv = process.argv.slice(2);
const has = (f) => argv.includes(f);
const val = (f, d) => { const i = argv.indexOf(f); return i >= 0 ? argv[i + 1] : d; };

const MODE_BASELINE = has('--baseline');
const MODE_GENERALIZE = has('--generalize');
const REDUCED = has('--reduced');
const WIDTH = parseInt(val('--width', '1440'), 10);
const SHOT = val('--shot', null);

/* ── theme generalization proof (--generalize) ────────────────────────────
   The claim under test: adding a theme requires ONE [data-theme] token block
   and nothing else. That is only true if no component rule holds a literal.

   A weak version of this test injected a probe theme and checked that
   --accent-rgb changed. That proves the variable changed, not that anything
   USES it. This version does the opposite and much stronger thing: it swaps
   every token to an unmistakable value, then hunts the rendered page for any
   surviving trace of the ORIGINAL palette. A survivor is, by definition, a
   hardcoded literal that no theme can ever reach. */
const SHIPPED_PALETTE = {
  '--ground-rgb': '12 12 15',
  '--panel-rgb': '17 17 24',
  '--surface-rgb': '24 24 31',
  '--hover-rgb': '31 31 42',
  '--border-quiet-rgb': '39 39 58',
  '--border-strong-rgb': '60 60 86',
  '--text-primary-rgb': '240 240 248',
  '--text-secondary-rgb': '148 148 172',
  '--text-muted-rgb': '138 138 170',
  '--accent-rgb': '0 163 165',
  '--accent-hi-rgb': '45 212 191',
  '--cta-rgb': '240 125 40',
  '--working-rgb': '255 207 23',
  '--success-rgb': '108 184 40',
  '--warning-rgb': '229 161 63',
  '--error-rgb': '226 86 75',
};
// Deliberately absurd, mutually distinct, and nothing like the real palette, so
// a match is unambiguous. Also avoids 0/255 extremes that appear incidentally.
const PROBE_PALETTE = {
  '--ground-rgb': '17 34 51',
  '--panel-rgb': '34 51 68',
  '--surface-rgb': '51 68 85',
  '--hover-rgb': '68 85 102',
  '--border-quiet-rgb': '85 102 119',
  '--border-strong-rgb': '102 119 136',
  '--text-primary-rgb': '250 240 230',
  '--text-secondary-rgb': '210 190 170',
  '--text-muted-rgb': '170 150 130',
  '--accent-rgb': '204 0 153',
  '--accent-hi-rgb': '238 17 187',
  '--cta-rgb': '119 204 17',
  '--working-rgb': '17 187 204',
  '--success-rgb': '187 17 204',
  '--warning-rgb': '204 119 17',
  '--error-rgb': '17 204 119',
};

/* ── what to measure ──────────────────────────────────────────────────────
   `synthetic: true` means the element is injected before measuring, so CSS
   rules for message bubbles are testable without needing real conversation
   data in the DB. */
const PROBES = [
  { name: 'body',              selector: 'body',                    props: ['backgroundColor', 'color', 'fontFamily', 'fontSize'] },
  { name: 'right-sidebar',     selector: '#right-sidebar',          props: ['backgroundColor', 'borderLeftColor', 'width'] },
  { name: 'assistant-bubble',  selector: '.msg-assistant .msg-bubble', synthetic: true,
    props: ['backgroundColor', 'borderLeftColor', 'borderLeftWidth', 'paddingLeft', 'boxShadow', 'borderRadius', 'fontFamily'] },
  { name: 'user-bubble',       selector: '.msg-user .msg-bubble',   synthetic: true,
    props: ['backgroundColor', 'borderColor', 'boxShadow', 'borderRadius'] },
  { name: 'assistant-text',    selector: '.msg-assistant .text-block', synthetic: true,
    props: ['color', 'fontFamily', 'fontSize', 'lineHeight'] },
  { name: 'code-block',        selector: '.msg-bubble pre',         synthetic: true,
    props: ['backgroundColor', 'boxShadow', 'fontFamily', 'borderColor'] },
  { name: 'inline-code',       selector: '.msg-bubble :not(pre) > code', synthetic: true,
    props: ['color', 'backgroundColor', 'textShadow', 'fontFamily'] },
  { name: 'msg-meta',          selector: '.msg-meta',               synthetic: true, props: ['color', 'fontSize', 'fontFamily'] },
  { name: 'honeycomb-canvas',  selector: '#honeycombCanvas',        props: ['position', 'zIndex', 'pointerEvents', 'display'] },
  { name: 'ambient-bg',        selector: '.ambient-bg',             props: ['position', 'zIndex', 'pointerEvents', 'opacity', 'backgroundImage'] },
  { name: 'scanline-overlay',  selector: '.scanline-overlay',       props: ['position', 'zIndex', 'pointerEvents'] },
];

/* text whose contrast must pass WCAG AA (4.5:1 normal, 3:1 large ≥18.66px bold or ≥24px) */
const CONTRAST_TARGETS = [
  { name: 'assistant body text', selector: '.msg-assistant .text-block', synthetic: true },
  { name: 'message metadata',    selector: '.msg-meta',                  synthetic: true },
  { name: 'inline code',         selector: '.msg-bubble :not(pre) > code', synthetic: true },
];

/* prose containers whose measure must land in 45–75 characters.
   Targets the <p> INSIDE .text-block, not .text-block itself: markdown renders
   into .text-block via innerHTML, so the container also holds code blocks and
   tables that must stay full-width. Capping the container would cap those too. */
const MEASURE_TARGETS = [
  { name: 'assistant prose', selector: '.msg-assistant .text-block > p', synthetic: true },
];

/* Mirrors the REAL rendered structure, which matters more than convenience:
   chat_pane.js does `textEl.innerHTML = render("text", content)` on .text-block,
   so marked.js output — <p>, <pre>, <table> — lands INSIDE .text-block, not as a
   sibling. An earlier version of this file nested <pre> as a sibling, which made
   the measure check pass while real code blocks were being over-constrained. */
const SYNTHETIC_HTML = `
<div id="__uicheck" style="position:absolute;left:0;top:0;width:100%">
  <div class="msg-row msg-user"><div class="msg-bubble"><div class="text-block"><p>user probe text</p></div></div>
    <div class="msg-meta">meta probe</div></div>
  <div class="msg-row msg-assistant"><div class="msg-bubble">
      <div class="text-block">
        <p>${'Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud. '.repeat(3)}<code>inline_code()</code></p>
        <pre><code>def probe_with_a_deliberately_long_line(argument_one, argument_two, argument_three):\n    return {"key": "a value long enough to need the full bubble width to read comfortably"}</code></pre>
      </div>
    </div><div class="msg-meta">meta probe</div></div>
</div>`;

/* ── in-page helpers (stringified into the browser) ──────────────────────── */
const PAGE_LIB = `
window.__ui = {
  parseColor(s) {
    if (!s || s === 'transparent' || s === 'none') return null;
    const m = s.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(/[,\\/\\s]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.slice(0,3).some(Number.isNaN)) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  },
  // Walk up compositing every semi-transparent background onto the first
  // opaque one. An element with a transparent background does not "have" the
  // colour behind it — it has to be computed.
  effectiveBg(el) {
    const stack = [];
    for (let n = el; n; n = n.parentElement) {
      const c = this.parseColor(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a === 1) break; }
    }
    if (!stack.length) return { r: 255, g: 255, b: 255, a: 1 };
    let out = stack.pop();                       // opaque-most, furthest up
    while (stack.length) {
      const top = stack.pop();
      out = {
        r: top.r * top.a + out.r * (1 - top.a),
        g: top.g * top.a + out.g * (1 - top.a),
        b: top.b * top.a + out.b * (1 - top.a),
        a: 1,
      };
    }
    return out;
  },
  lum(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  },
  contrast(fg, bg) {
    // flatten a semi-transparent foreground onto its background first
    const f = fg.a < 1
      ? { r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a) }
      : fg;
    const L1 = this.lum(f), L2 = this.lum(bg);
    const [hi, lo] = L1 > L2 ? [L1, L2] : [L2, L1];
    return (hi + 0.05) / (lo + 0.05);
  },
  cssPath(el) {
    const bits = [];
    for (let n = el; n && n.nodeType === 1 && bits.length < 4; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id) { bits.unshift('#' + n.id); break; }
      const cls = (n.className || '').toString().trim().split(/\\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      bits.unshift(s);
    }
    return bits.join(' > ');
  },
  // Enumerate EVERY element that renders its own visible text and compute the
  // contrast it actually presents. Written this way on purpose: a hand-listed
  // set of selectors silently omits whatever the author forgot, and the author
  // forgot the entire left rail and header the first time.
  auditAllText() {
    const seen = new Set();
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      if (el.closest('#__uicheck')) continue;                 // skip our own probes
      const own = Array.from(el.childNodes)
        .filter((n) => n.nodeType === 3 && n.textContent.trim().length > 1)
        .map((n) => n.textContent.trim()).join(' ');
      if (!own) continue;
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') continue;
      if (parseFloat(cs.opacity) === 0) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      if (r.bottom < 0 || r.top > innerHeight * 3) continue;   // offscreen-ish
      const fg = this.parseColor(cs.color);
      if (!fg || fg.a === 0) continue;
      const bg = this.effectiveBg(el);
      const ratio = +this.contrast(fg, bg).toFixed(2);
      const path = this.cssPath(el);
      const key = path + '|' + cs.color + '|' + cs.fontSize;
      if (seen.has(key)) continue;                             // collapse repeats
      seen.add(key);
      out.push({
        path,
        sample: own.slice(0, 40),
        ratio,
        fg: cs.color,
        bg: 'rgb(' + Math.round(bg.r) + ', ' + Math.round(bg.g) + ', ' + Math.round(bg.b) + ')',
        fontSize: parseFloat(cs.fontSize),
        fontWeight: cs.fontWeight,
        opacity: parseFloat(cs.opacity),
      });
    }
    return out.sort((a, b) => a.ratio - b.ratio);
  },
  // Characters per line: measure a 100-char run in the element's own computed
  // font, then divide the content box width by the resulting per-char width.
  measureCh(el) {
    const cs = getComputedStyle(el);
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre';
    probe.style.font = cs.font || (cs.fontStyle+' '+cs.fontWeight+' '+cs.fontSize+'/'+cs.lineHeight+' '+cs.fontFamily);
    probe.style.letterSpacing = cs.letterSpacing;
    probe.textContent = 'x'.repeat(100);
    document.body.appendChild(probe);
    const per = probe.getBoundingClientRect().width / 100;
    probe.remove();
    const w = el.clientWidth - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0);
    return per > 0 ? Math.round(w / per) : null;
  },
};`;

/* ── contrast requirement depends on rendered size ─────────────────────── */
function requiredRatio(fontSizePx, fontWeight) {
  const w = parseInt(fontWeight, 10);
  const large = fontSizePx >= 24 || (fontSizePx >= 18.66 && (w >= 700 || fontWeight === 'bold'));
  return large ? 3.0 : 4.5;
}

/* ── run ────────────────────────────────────────────────────────────────── */
const results = {
  meta: { url: BASE_URL, width: WIDTH, reducedMotion: REDUCED, at: new Date().toISOString() },
  styles: {}, contrast: [], measure: [], console: [], layers: {}, listeners: null, missing: [],
};
const failures = [];
const notes = [];

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: WIDTH, height: 900 },
  reducedMotion: REDUCED ? 'reduce' : 'no-preference',
});

page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') {
    results.console.push({ type: m.type(), text: m.text().slice(0, 300) });
  }
});
page.on('pageerror', (e) => results.console.push({ type: 'pageerror', text: String(e).slice(0, 300) }));

// Playwright starts from a clean profile, so the onboarding overlay and the
// budget toast both show and cover the app. Seed the flags the app itself uses
// so we measure the working UI rather than a modal.
await page.addInitScript(() => {
  try {
    localStorage.setItem('gca_onboarded', '1');
    localStorage.setItem('gca_sb_open', '1');
    // Force the once-a-day spend toast to render. Without this the toast is
    // absent during every harness run, so the occlusion sweep cannot see it —
    // and the toast covering #composer-send is the exact bug the sweep exists
    // to catch. A check that cannot observe its motivating defect is decoration.
    localStorage.removeItem('gca_cost_notified');
  } catch { /* private mode */ }
});

try {
  const resp = await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 20000 });
  if (!resp || !resp.ok()) throw new Error(`GET ${BASE_URL} -> ${resp ? resp.status() : 'no response'}`);
} catch (e) {
  console.error(`FATAL: could not load ${BASE_URL}\n  ${e.message}\n  Is the server running?  cd backend && ../.venv/Scripts/python.exe run.py`);
  await browser.close();
  process.exit(1);
}

await page.addScriptTag({ content: PAGE_LIB });
await page.evaluate((html) => {
  document.getElementById('__uicheck')?.remove();
  const messages = document.querySelector('#messages') || document.body;
  messages.insertAdjacentHTML('afterbegin', html);
}, SYNTHETIC_HTML);
await page.waitForTimeout(120); // let styles settle

// computed styles
for (const p of PROBES) {
  const got = await page.evaluate(({ selector, props }) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const o = {};
    for (const k of props) o[k] = cs[k];
    return o;
  }, p);
  if (got === null) { results.missing.push(p.name); continue; }
  results.styles[p.name] = got;
}

// contrast
for (const t of CONTRAST_TARGETS) {
  const r = await page.evaluate(({ selector }) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const fg = window.__ui.parseColor(cs.color);
    const bg = window.__ui.effectiveBg(el);
    if (!fg) return null;
    return {
      ratio: +window.__ui.contrast(fg, bg).toFixed(2),
      fg: cs.color, bg: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
      fontSize: parseFloat(cs.fontSize), fontWeight: cs.fontWeight,
    };
  }, t);
  if (!r) { results.missing.push(`contrast:${t.name}`); continue; }
  const need = requiredRatio(r.fontSize, r.fontWeight);
  const pass = r.ratio >= need;
  results.contrast.push({ name: t.name, ...r, required: need, pass });
  if (!pass) failures.push(`contrast "${t.name}" ${r.ratio}:1 < ${need}:1 required (${r.fg} on ${r.bg} @ ${r.fontSize}px)`);
}

// STATE-DEPENDENT STYLES.
//
// The sweep below is exhaustive over what is RENDERED, which is not the same as
// exhaustive over what the app can render. A console error line exists only
// after something is logged at that level, so .lvl-error was measured only when
// a run happened to produce one — it passed three gate runs and failed the
// fourth, on identical code — and .lvl-warn had never been measured at all.
// Coverage that depends on luck is not coverage; it is a sometimes-vacuous test
// that reports green.
//
// So: render one sample of each state-dependent style into its REAL container
// (not a synthetic one — the whole point is the real composited background),
// let the sweep pick them up as ordinary text, then remove them.
//
// Missing containers are recorded, never skipped silently. "Nothing seeded"
// must not look like "everything passed".
const SEEDED = [
  { host: '#rsb-console', cls: 'console-line lvl-error', text: 'probe: error line' },
  { host: '#rsb-console', cls: 'console-line lvl-warn',  text: 'probe: warn line' },
  { host: '#rsb-console', cls: 'console-line lvl-info',  text: 'probe: info line' },
  { host: '#rsb-console', cls: 'console-line lvl-debug', text: 'probe: debug line' },
  // 4-P2 search results. Same reason as the console levels: these exist only
  // while a search is in flight, so a sweep of "what happens to be rendered"
  // would never see them and their contrast would go unmeasured indefinitely.
  { host: '#conv-list', cls: 'conv-group-header', text: 'Messages' },
  { host: '#conv-list', cls: 'conv-group-note',   text: 'Showing the first 50 - refine to see more' },
  // `html` rather than `text`: the highlight is a NESTED element, and a probe
  // that could only set textContent would leave the one colour most likely to
  // fail — a highlight — permanently unmeasured. The markup is ours, not any
  // server's.
  { host: '#conv-list', cls: 'conv-item conv-hit',
    html: '<span class="conv-hit-snippet">probe snippet with a <mark>highlighted</mark> hit</span>' },
];
results.seeded = await page.evaluate((specs) => {
  const made = [];
  const missing = [];
  for (const s of specs) {
    const host = document.querySelector(s.host);
    if (!host) { missing.push(`${s.host} (for .${s.cls.split(' ').pop()})`); continue; }
    const d = document.createElement('div');
    d.className = s.cls;
    d.dataset.uiSeed = '1';
    if (s.html) d.innerHTML = s.html; else d.textContent = s.text;
    host.appendChild(d);
    // Report what ATTACHED, not what we asked to attach. Under mutation the
    // append was removed and this line still claimed four probes were seeded
    // while the sweep measured 49 elements instead of 52 — a report describing
    // its own intent rather than its effect, which is the failure mode this
    // project keeps finding in its own instruments.
    if (d.isConnected) made.push(s.cls);
    else missing.push(`${s.host} (.${s.cls.split(' ').pop()} did not attach)`);
  }
  return { made, missing };
}, SEEDED);
if (results.seeded.missing.length) {
  failures.push(
    `state-dependent style probes could not be seeded: ${results.seeded.missing.join(', ')} ` +
    `— those styles went UNMEASURED, so a pass here is not evidence they are accessible`,
  );
}

// exhaustive contrast sweep over every element rendering its own text
results.textAudit = await page.evaluate(() => window.__ui.auditAllText());
// Seeded probes have served their purpose; remove them before anything else
// measures the DOM, so node counts and layer checks see the real page.
await page.evaluate(() => {
  document.querySelectorAll('[data-ui-seed="1"]').forEach((el) => el.remove());
});
for (const t of results.textAudit) {
  t.required = t.fontSize >= 24 || (t.fontSize >= 18.66 && (parseInt(t.fontWeight, 10) >= 700 || t.fontWeight === 'bold')) ? 3.0 : 4.5;
  t.pass = t.ratio >= t.required;
}
const aaFails = results.textAudit.filter((t) => !t.pass);
if (aaFails.length) {
  failures.push(`${aaFails.length} of ${results.textAudit.length} text elements fail WCAG AA (worst ${aaFails[0].ratio}:1 at ${aaFails[0].path})`);
}

// line measure
for (const t of MEASURE_TARGETS) {
  const ch = await page.evaluate(({ selector }) => {
    const el = document.querySelector(selector);
    return el ? window.__ui.measureCh(el) : null;
  }, t);
  if (ch === null) { results.missing.push(`measure:${t.name}`); continue; }
  const pass = ch >= 45 && ch <= 75;
  results.measure.push({ name: t.name, chars: ch, target: '45-75', pass });
  if (!pass) failures.push(`measure "${t.name}" is ${ch} characters (target 45-75)`);
}

// Code blocks must NOT inherit the prose measure cap. Capping the container
// instead of its text children silently shrinks every code block — the exact
// regression an earlier version of this harness could not see, because its
// synthetic markup put <pre> outside .text-block.
results.codeWidth = await page.evaluate(() => {
  const pre = document.querySelector('.msg-assistant .text-block pre');
  const para = document.querySelector('.msg-assistant .text-block > p');
  if (!pre || !para) return null;
  const cs = getComputedStyle(pre);
  return {
    preWidth: Math.round(pre.getBoundingClientRect().width),
    proseWidth: Math.round(para.getBoundingClientRect().width),
    preMaxWidth: cs.maxWidth,
    overflowX: cs.overflowX,
  };
});
if (results.codeWidth) {
  const { preWidth, proseWidth, overflowX } = results.codeWidth;
  // A code block should be able to use more width than capped prose.
  if (preWidth <= proseWidth + 1) {
    failures.push(`code block is capped to the prose measure (pre ${preWidth}px vs prose ${proseWidth}px) — cap the text children of .text-block, not the container`);
  }
  if (overflowX !== 'auto' && overflowX !== 'scroll') {
    failures.push(`code block overflow-x is "${overflowX}" — long lines will break the page instead of scrolling in place`);
  }
} else {
  results.missing.push('codeWidth probe');
}

// theme generalization proof
if (MODE_GENERALIZE) {
  results.generalize = await page.evaluate(({ shipped, probe }) => {
    const COLOR_PROPS = [
      'color', 'backgroundColor', 'borderTopColor', 'borderRightColor',
      'borderBottomColor', 'borderLeftColor', 'outlineColor', 'boxShadow',
      'textShadow', 'backgroundImage', 'fill', 'stroke', 'caretColor',
      'textDecorationColor', 'columnRuleColor',
    ];
    // "12 34 56" -> the rgb(12, 34, 56) form a computed style reports
    const asRgb = (triplet) => `rgb(${triplet.trim().split(/\s+/).join(', ')})`;
    const shippedRgb = Object.fromEntries(
      Object.entries(shipped).map(([k, v]) => [k, asRgb(v)])
    );

    // Scan every element's computed style for any surviving shipped colour.
    function scanForShipped() {
      const hits = [];
      for (const el of document.querySelectorAll('body *')) {
        if (el.closest('#__uicheck')) continue;
        const cs = getComputedStyle(el);
        for (const prop of COLOR_PROPS) {
          const v = cs[prop];
          if (!v || v === 'none' || v === 'rgba(0, 0, 0, 0)') continue;
          for (const [token, rgb] of Object.entries(shippedRgb)) {
            // Match the exact rgb() form, and the bare "r, g, b" inside rgba()/
            // gradients, so an alpha-composed literal is caught too.
            const bare = rgb.slice(4, -1);
            if (v.includes(rgb) || v.includes(`rgba(${bare}`)) {
              hits.push({
                token,
                prop,
                value: v.slice(0, 90),
                path: (el.id ? '#' + el.id : el.tagName.toLowerCase()) +
                      (typeof el.className === 'string' && el.className
                        ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''),
              });
              break;
            }
          }
        }
      }
      return hits;
    }

    // CRITICAL: kill transitions before measuring anything.
    //
    // Many components carry `transition: all 0.22s`. getComputedStyle during a
    // transition returns the CURRENT ANIMATED value, so reading immediately
    // after a theme swap reports the PRE-swap colour — while custom properties,
    // which do not transition, report the new value instantly. That combination
    // produced 530 phantom "hardcoded colour" survivors across 286 sites on the
    // first run of this check. Measured proof: the same element read
    // rgb(45,212,191) at t=0 and rgb(238,17,187) at t=400ms.
    const killer = document.createElement('style');
    killer.textContent = '*,*::before,*::after{transition:none !important;animation:none !important}';
    document.head.appendChild(killer);
    void document.body.offsetHeight;

    const beforeHits = scanForShipped();

    // Inject the probe theme and select it, exactly as a real theme would be.
    const style = document.createElement('style');
    style.id = '__probe-theme';
    style.textContent = '[data-theme="__probe"]{' +
      Object.entries(probe).map(([k, v]) => `${k}:${v};`).join('') + '}';
    document.head.appendChild(style);
    const previous = document.documentElement.dataset.theme || '';
    document.documentElement.dataset.theme = '__probe';

    // force a style flush
    void document.body.offsetHeight;

    const survivors = scanForShipped();

    // Confirm the probe actually took effect, so a zero-survivor result cannot
    // be a false pass caused by the theme never applying at all.
    const probeApplied =
      getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim()
        === probe['--accent-rgb'];

    // restore
    document.documentElement.dataset.theme = previous;
    style.remove();
    killer.remove();

    return {
      shippedColoursFoundBefore: beforeHits.length,
      probeApplied,
      survivors,
      tokensSwapped: Object.keys(probe).length,
    };
  }, { shipped: SHIPPED_PALETTE, probe: PROBE_PALETTE });

  const g = results.generalize;
  if (!g.probeApplied) {
    failures.push('generalize: the probe theme never applied, so the result is meaningless');
  } else if (g.shippedColoursFoundBefore === 0) {
    failures.push('generalize: found zero shipped colours BEFORE switching — the scan is not seeing the page, so a clean result proves nothing');
  } else if (g.survivors.length) {
    const uniq = [...new Set(g.survivors.map((s) => `${s.path} { ${s.prop} }`))];
    failures.push(`generalize: ${g.survivors.length} hardcoded colour(s) survived a full theme swap — ${uniq.slice(0, 4).join(' · ')}`);
  }
}

// full-viewport layer stacking — the reachability question for pointer events
results.layers = await page.evaluate(() => {
  const out = {};
  for (const sel of ['#honeycombCanvas', '.ambient-bg', '.scanline-overlay']) {
    const el = document.querySelector(sel);
    if (!el) { out[sel] = null; continue; }
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    out[sel] = {
      zIndex: cs.zIndex, pointerEvents: cs.pointerEvents, position: cs.position,
      coversViewport: r.width >= innerWidth - 2 && r.height >= innerHeight - 2,
    };
  }
  return out;
});
for (const [sel, info] of Object.entries(results.layers)) {
  if (info && info.coversViewport && info.pointerEvents !== 'none') {
    failures.push(`${sel} covers the viewport with pointer-events: ${info.pointerEvents} — it will swallow clicks`);
  }
}

// occlusion sweep — visibility and clickability are different properties.
// A control can pass every computed-style visibility check and still be
// unclickable because something else sits on top of it at its own centre
// point. Only a hit-test (elementFromPoint) catches that.
results.occlusion = await page.evaluate(() => {
  const SEL = 'button, a[href], input, select, textarea, summary, [role="button"]';
  const effectiveOpacity = (el) => {
    let o = 1;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      o *= Number(getComputedStyle(n).opacity);
      if (o <= 0.01) return 0;
    }
    return o;
  };
  const legitimatelyHidden = (el) =>
    !!el.closest('details:not([open])') ||
    !!el.closest('[hidden]') ||
    !!el.closest('dialog:not([open])') ||
    !!el.closest('.sb-collapsed') ||
    effectiveOpacity(el) <= 0.01;

  // A control inside a scrolling container can sit outside that container's
  // VISIBLE band while still being inside the browser viewport. It is not
  // occluded — it is scrolled out of view, and scrolling reveals it.
  // elementFromPoint at its centre then returns whatever is painted there
  // (the header above, the composer below), which reads as a false occlusion.
  // Measured: with #chat-scroll showing 56..782, message buttons at cy=10 and
  // cy=804 reported as "covered by" #chat-header and #composer-row. Neither is
  // covered. Both scroll into reach. Without this check the sweep fails on any
  // scrollable list, which is every chat transcript.
  const clippedByScrollAncestor = (el, cx, cy) => {
    for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (!/(auto|scroll|hidden)/.test(cs.overflowY + ' ' + cs.overflowX)) continue;
      const b = n.getBoundingClientRect();
      if (cx < b.left || cx > b.right || cy < b.top || cy > b.bottom) return true;
    }
    return false;
  };

  let swept = 0;
  let offscreen = 0;
  let clipped = 0;
  const occluded = [];
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (legitimatelyHidden(el)) continue;
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) { offscreen++; continue; }
    if (clippedByScrollAncestor(el, cx, cy)) { clipped++; continue; }
    swept++;
    const top = document.elementFromPoint(cx, cy);
    if (top !== el && !el.contains(top)) {
      occluded.push({
        control: window.__ui.cssPath(el),
        coveredBy: top ? window.__ui.cssPath(top) : '(nothing — outside document)',
      });
    }
  }
  return { swept, offscreen, clipped, occluded };
});
{
  const oc = results.occlusion;
  if (oc.swept < 10) {
    failures.push(`occlusion sweep: ANOMALY — only ${oc.swept} control(s) swept (< 10), the sweep likely looked at nothing`);
  }
  for (const o of oc.occluded) {
    failures.push(`occlusion: ${o.control} is covered by ${o.coveredBy} and cannot be clicked`);
  }
}

// listener accounting — the hover blind test needs a number, not a vibe
results.listeners = await page.evaluate(() => {
  // patched at load is impossible post-hoc; report what we can observe cheaply
  const nodes = document.querySelectorAll('*').length;
  return { domNodes: nodes };
});

// prefers-reduced-motion sweep — must MEASURE, not just request, the emulation.
// Deliberately does NOT touch the --generalize transition-killer <style> tag:
// injecting `transition:none!important` here would kill transitions itself,
// making any duration check trivially pass regardless of whether base.css's
// own @media block does anything. Reads real computed values only.
if (REDUCED) {
  results.reducedMotion = await page.evaluate(() => {
    const offenders = [];
    let swept = 0;
      // getComputedStyle reports durations as e.g. "0.2s" or "10ms" — parseFloat
      // alone drops the unit, so convert explicitly rather than assume seconds.
    const toMs = (s) => {
      const first = (s || '0s').split(',')[0].trim();
      return first.endsWith('ms') ? parseFloat(first) : parseFloat(first) * 1000;
    };
    for (const el of document.querySelectorAll('*')) {
      swept++;
      const cs = getComputedStyle(el);
      const transDur = toMs(cs.transitionDuration);
      const animDur = toMs(cs.animationDuration);
      if (transDur > 1 || animDur > 1) {
        offenders.push({
          path: window.__ui.cssPath(el),
          transitionDuration: cs.transitionDuration,
          animationDuration: cs.animationDuration,
          animationName: cs.animationName,
        });
      }
    }
    return { swept, offenders };
  });
  const rm = results.reducedMotion;
  if (rm.swept < 50) {
    failures.push(`reduced-motion sweep touched only ${rm.swept} elements — ANOMALY, the sweep likely looked at nothing`);
  }
  if (rm.offenders.length) {
    for (const o of rm.offenders.slice(0, 20)) {
      failures.push(`reduced-motion violation: ${o.path} transition-duration=${o.transitionDuration} animation-duration=${o.animationDuration} (animation-name=${o.animationName})`);
    }
    if (rm.offenders.length > 20) failures.push(`... and ${rm.offenders.length - 20} more reduced-motion violations`);
  }
}

// console must be clean of errors
const errs = results.console.filter((c) => c.type === 'error' || c.type === 'pageerror');
if (errs.length) failures.push(`${errs.length} console error(s), first: ${errs[0].text}`);

if (SHOT) {
  mkdirSync(dirname(resolve(SHOT)), { recursive: true });
  await page.evaluate(() => document.getElementById('__uicheck')?.remove());
  await page.screenshot({ path: resolve(SHOT), fullPage: false });
  notes.push(`screenshot -> ${resolve(SHOT)}`);
}

await browser.close();

/* ── baseline compare ───────────────────────────────────────────────────── */
if (MODE_BASELINE) {
  writeFileSync(BASELINE, JSON.stringify(results, null, 2), 'utf8');
  console.log(`baseline written -> ${BASELINE}`);
} else if (existsSync(BASELINE)) {
  const base = JSON.parse(readFileSync(BASELINE, 'utf8'));
  const drift = [];
  for (const [name, props] of Object.entries(results.styles)) {
    const b = base.styles?.[name];
    if (!b) { drift.push(`${name}: new probe (no baseline)`); continue; }
    for (const [k, v] of Object.entries(props)) {
      if (b[k] !== undefined && b[k] !== v) drift.push(`${name}.${k}: ${b[k]}  ->  ${v}`);
    }
  }
  results.drift = drift;
}

/* ── report ─────────────────────────────────────────────────────────────── */
const line = (s = '') => console.log(s);
line(`=== ui-check  ${BASE_URL}  ${WIDTH}px${REDUCED ? '  reduced-motion' : ''} ===`);
line();

line('Contrast (WCAG AA):');
for (const c of results.contrast) line(`  ${c.pass ? 'PASS' : 'FAIL'}  ${c.name}: ${c.ratio}:1 (need ${c.required}:1)  ${c.fg} on ${c.bg}`);
if (!results.contrast.length) line('  (none measured)');
line();

const audit = results.textAudit ?? [];
const bad = audit.filter((t) => !t.pass);
if (results.seeded) {
  line(`State-dependent styles seeded: ${results.seeded.made.length ? results.seeded.made.map((c) => '.' + c.split(' ').pop()).join(', ') : 'NONE'}` +
       (results.seeded.missing.length ? `  [UNMEASURED: ${results.seeded.missing.join(', ')}]` : ''));
}
line(`Text contrast sweep: ${audit.length} elements measured, ${bad.length} below AA`);
for (const t of bad.slice(0, 18)) {
  line(`  FAIL ${String(t.ratio).padStart(6)}:1 (need ${t.required})  ${t.fontSize}px  ${t.path}`);
  line(`         ${t.fg} on ${t.bg}   "${t.sample}"`);
}
if (bad.length > 18) line(`  ... and ${bad.length - 18} more`);
if (audit.length && !bad.length) line(`  all pass — worst is ${audit[0].ratio}:1 at ${audit[0].path}`);
line();

line('Line measure:');
for (const m of results.measure) line(`  ${m.pass ? 'PASS' : 'FAIL'}  ${m.name}: ${m.chars} chars (target ${m.target})`);
if (!results.measure.length) line('  (none measured)');
line();

if (results.codeWidth) {
  const cw = results.codeWidth;
  const ok = cw.preWidth > cw.proseWidth + 1;
  line(`Code block width: ${ok ? 'PASS' : 'FAIL'}  pre ${cw.preWidth}px vs prose ${cw.proseWidth}px  (overflow-x: ${cw.overflowX})`);
  line();
}
if (results.generalize) {
  const g = results.generalize;
  line('Theme generalization proof:');
  line(`  swapped ${g.tokensSwapped} tokens · probe applied: ${g.probeApplied}`);
  line(`  shipped colours visible before swap: ${g.shippedColoursFoundBefore}  (must be > 0 or the scan is blind)`);
  if (!g.survivors.length) {
    line('  PASS  no shipped colour survived — every rendered colour is token-driven');
  } else {
    const byPath = new Map();
    for (const s of g.survivors) {
      const k = `${s.path} { ${s.prop} }`;
      if (!byPath.has(k)) byPath.set(k, s);
    }
    line(`  FAIL  ${g.survivors.length} survivor(s), ${byPath.size} distinct site(s):`);
    for (const [k, s] of [...byPath].slice(0, 15)) {
      line(`          ${k}  <- ${s.token}`);
      line(`            ${s.value}`);
    }
    if (byPath.size > 15) line(`          ... and ${byPath.size - 15} more`);
  }
  line();
}

if (results.reducedMotion) {
  const rm = results.reducedMotion;
  line(`Reduced-motion sweep: ${rm.swept} elements measured, ${rm.offenders.length} violation(s)`);
  if (!rm.offenders.length) line('  all pass — every transition/animation duration is <=1ms');
  line();
}

line(`Occlusion sweep: ${results.occlusion.swept} control(s) swept (${results.occlusion.offscreen} offscreen, ${results.occlusion.clipped} scroll-clipped skipped), ${results.occlusion.occluded.length} occluded`);
for (const o of results.occlusion.occluded) line(`  FAIL  ${o.control}  covered by  ${o.coveredBy}`);
line();

line('Full-viewport layers:');
for (const [sel, i] of Object.entries(results.layers)) {
  line(i ? `  ${sel}  z=${i.zIndex}  pointer-events=${i.pointerEvents}  covers=${i.coversViewport}` : `  ${sel}  ABSENT`);
}
line();

line(`Console: ${errs.length} error(s), ${results.console.length - errs.length} warning(s)`);
for (const c of results.console.slice(0, 5)) line(`  [${c.type}] ${c.text}`);
line();

if (results.missing.length) { line(`Selectors not found (${results.missing.length}): ${results.missing.join(', ')}`); line(); }
if (results.drift?.length) { line(`Style drift vs baseline (${results.drift.length}):`); for (const d of results.drift) line(`  ${d}`); line(); }
for (const n of notes) line(n);

writeFileSync(resolve(HERE, 'last-run.json'), JSON.stringify(results, null, 2), 'utf8');

if (failures.length) {
  line(`RESULT: FAIL — ${failures.length}`);
  for (const f of failures) line(`  - ${f}`);
  process.exit(1);
}
line(`RESULT: PASS (${results.contrast.length} contrast, ${results.measure.length} measure, ${Object.keys(results.styles).length} style probes)`);
process.exit(0);
