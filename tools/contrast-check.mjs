/**
 * contrast-check.mjs - WCAG 2.1 SC 1.4.3 (Contrast, Minimum), measured from PIXELS.
 *
 * Copied unchanged from ../_wcag/contrast-check.mjs (designed by Opus,
 * 2026-09-25) except this header. Why pixels and not computed styles: this app
 * paints a honeycomb <canvas> behind content and uses translucent surfaces, so
 * walking ancestor background-color gives the wrong answer exactly where it
 * matters. Method, per UI state:
 *   1. Collect every visible text-bearing element (direct non-blank text node,
 *      or an input/textarea with a value or placeholder). Visible = has client
 *      rects inside the viewport AND is the hit-test target at its centre (so
 *      text under a modal backdrop is not scored as if it were active).
 *   2. Make ONLY the glyphs transparent (color, -webkit-text-fill-color,
 *      text-shadow, placeholder), freeze animations, take ONE screenshot.
 *      That screenshot is the true background behind every glyph.
 *   3. For each element, sample the screenshot inside the text's own line boxes
 *      (Range.getClientRects, not the padded element box, so borders do not
 *      pollute the sample). Composite the text colour over each background
 *      pixel if it has alpha. Score = 10th-percentile contrast (worst tenth of
 *      the background), and report the minimum too.
 *   4. Threshold: 4.5:1, or 3:1 for large text (>= 24px, or >= 18.66px at
 *      weight >= 700). Disabled controls are EXEMPT (SC 1.4.3 "incidental").
 * Hover pass: every visible button/link is hovered and re-measured alone.
 *
 * Exit: 0 PASS, 1 FAIL, 2 INCONCLUSIVE. Non-empty guard first in every state.
 */
import { createRequire } from 'node:module';
import path from 'node:path';

const REPO = process.env.GCA_REPO ?? 'C:\\Users\\hduenas\\LLMs\\Claude\\Projects\\ClaudioUI';
const require = createRequire(path.join(REPO, 'package.json'));
const { chromium } = require('playwright');

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const HOVER_CAP = Number(process.env.CC_HOVER_CAP ?? 120);
const results = [];          // { state, verdict, ratio, min, need, fg, bg, text, sel }
const stateLog = [];         // { state, targets, verdict, note }

const browser = await chromium.launch();

async function newPage({ onboarded = true } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await ctx.addInitScript((seen) => {
    try { if (seen) localStorage.setItem('gca_onboarded', '1'); else localStorage.removeItem('gca_onboarded'); } catch {}
  }, onboarded);
  const page = await ctx.newPage();
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  return { ctx, page };
}

// ---- in-page helpers, installed once per page --------------------------------
const INSTALL = () => {
  if (window.__cc) return;
  const parse = (s) => {
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a) });
  const hex = (c) => '#' + [c.r, c.g, c.b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');
  const selOf = (el) => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && parts.length < 4; e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(s + '#' + e.id); break; }
      if (e.classList.length) s += '.' + [...e.classList].slice(0, 2).join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const inView = (r) => r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
  const clip = (r) => ({ x0: Math.max(0, Math.floor(r.left)), y0: Math.max(0, Math.floor(r.top)),
                         x1: Math.min(innerWidth, Math.ceil(r.right)), y1: Math.min(innerHeight, Math.ceil(r.bottom)) });

  function textRects(el) {
    if (el.matches('input, textarea')) return [el.getBoundingClientRect()].filter(inView);
    const rects = [];
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      const rg = document.createRange(); rg.selectNodeContents(n);
      rects.push(...rg.getClientRects());
    }
    return rects.filter(inView);
  }
  function hitVisible(el, rects) {
    for (const r of rects) {
      const x = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2));
      const y = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
      const hit = document.elementFromPoint(x, y);
      if (hit && (hit === el || el.contains(hit) || hit.contains(el))) return true;
    }
    return false;
  }
  function collect(scopeSel) {
    const scope = scopeSel ? document.querySelector(scopeSel) : document.body;
    if (!scope) return [];
    const out = [];
    for (const el of scope.querySelectorAll('*')) {
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) continue;
      const isField = el.matches('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=range]):not([type=color]), textarea');
      const hasText = isField ? !!(el.value || el.placeholder)
        : [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (!hasText) continue;
      const rects = textRects(el);
      if (!rects.length || !hitVisible(el, rects)) continue;
      const usingPlaceholder = isField && !el.value && !!el.placeholder;
      const color = parse(usingPlaceholder ? getComputedStyle(el, '::placeholder').color : cs.color);
      const px = parseFloat(cs.fontSize), wt = Number(cs.fontWeight) || 400;
      out.push({
        el, rects: rects.map((r) => clip(r)),
        color, px, wt, large: px >= 24 || (px >= 18.66 && wt >= 700),
        disabled: !!el.closest(':disabled, [aria-disabled="true"]'),
        placeholder: usingPlaceholder,
        clipText: cs.backgroundClip === 'text' || cs.webkitBackgroundClip === 'text',
        text: (isField ? (el.value || el.placeholder) : el.textContent).trim().replace(/\s+/g, ' ').slice(0, 40),
        sel: selOf(el),
      });
    }
    return out;
  }
  function hide(targets, on) {
    let st = document.getElementById('__cc_style');
    if (!st) {
      st = document.createElement('style'); st.id = '__cc_style';
      st.textContent = `[data-cc-hide]{color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important;caret-color:transparent!important}
        [data-cc-hide]::placeholder{color:transparent!important;-webkit-text-fill-color:transparent!important}
        *,*::before,*::after{transition:none!important;animation-play-state:paused!important;caret-color:transparent!important}`;
      document.head.appendChild(st);
    }
    for (const t of targets) { if (on) t.el.setAttribute('data-cc-hide', ''); else t.el.removeAttribute('data-cc-hide'); }
  }
  async function score(targets, pngB64) {
    const img = new Image(); img.src = 'data:image/png;base64,' + pngB64; await img.decode();
    const cv = new OffscreenCanvas(img.width, img.height); const cx = cv.getContext('2d');
    cx.drawImage(img, 0, 0); const data = cx.getImageData(0, 0, img.width, img.height).data;
    return targets.map((t) => {
      const base = { text: t.text, sel: t.sel, px: t.px, wt: t.wt, placeholder: t.placeholder };
      const req = t.large ? 3 : 4.5;
      if (t.disabled) return { ...base, verdict: 'EXEMPT', need: req, note: 'disabled' };
      if (!t.color || t.clipText) return { ...base, verdict: 'INCONCLUSIVE', need: req, note: t.clipText ? 'background-clip:text' : 'unparsed colour' };
      const rs = [];
      const bgSum = { r: 0, g: 0, b: 0, n: 0 };
      for (const r of t.rects) for (let y = r.y0; y < r.y1; y++) for (let x = r.x0; x < r.x1; x++) {
        const i = (y * img.width + x) * 4; const bg = { r: data[i], g: data[i + 1], b: data[i + 2] };
        const fg = t.color.a < 1 ? over(t.color, bg) : t.color;
        rs.push(ratio(fg, bg)); bgSum.r += bg.r; bgSum.g += bg.g; bgSum.b += bg.b; bgSum.n++;
      }
      if (!rs.length) return { ...base, verdict: 'INCONCLUSIVE', need: req, note: 'no pixels in view' };
      rs.sort((a, b) => a - b);
      const p10 = rs[Math.floor(rs.length * 0.1)], min = rs[0];
      const bgAvg = { r: bgSum.r / bgSum.n, g: bgSum.g / bgSum.n, b: bgSum.b / bgSum.n };
      return { ...base, verdict: p10 >= req ? 'PASS' : 'FAIL', need: req, ratio: +p10.toFixed(2), min: +min.toFixed(2),
               fg: hex(t.color) + (t.color.a < 1 ? `@${t.color.a}` : ''), bg: hex(bgAvg), pixels: rs.length };
    });
  }
  window.__cc = { collect, hide, score };
};

async function measure(page, state, scopeSel = null) {
  await page.evaluate(INSTALL);
  const n = await page.evaluate((s) => { window.__ccT = window.__cc.collect(s); window.__cc.hide(window.__ccT, true); return window.__ccT.length; }, scopeSel);
  if (n === 0) {
    await page.evaluate(() => window.__cc.hide(window.__ccT, false));
    stateLog.push({ state, targets: 0, verdict: 'FAIL', note: 'ZERO text targets found: a sweep here would pass vacuously' });
    return;
  }
  await page.waitForTimeout(80);
  const png = (await page.screenshot({ type: 'png' })).toString('base64');
  const scored = await page.evaluate(async (b64) => { const r = await window.__cc.score(window.__ccT, b64); window.__cc.hide(window.__ccT, false); return r; }, png);
  for (const s of scored) results.push({ state, ...s });
  const bad = scored.filter((s) => s.verdict === 'FAIL').length;
  stateLog.push({ state, targets: n, verdict: bad ? 'FAIL' : 'PASS', note: `${bad} failing of ${n}` });
}

async function hoverPass(page, state) {
  await page.evaluate(INSTALL);
  const count = await page.evaluate(() => {
    const els = [...document.querySelectorAll('button, a[href], [role="button"], [role="tab"], [role="menuitem"]')]
      .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight && e.textContent.trim(); });
    els.forEach((e, i) => e.setAttribute('data-cc-hv', String(i)));
    return els.length;
  });
  let measured = 0;
  for (let i = 0; i < Math.min(count, HOVER_CAP); i++) {
    const loc = page.locator(`[data-cc-hv="${i}"]`);
    try { await loc.hover({ timeout: 1000 }); } catch { continue; }
    await page.waitForTimeout(60);
    const n = await page.evaluate((i) => {
      const el = document.querySelector(`[data-cc-hv="${i}"]`);
      window.__ccT = el ? window.__cc.collect(null).filter((t) => t.el === el || el.contains(t.el)) : [];
      window.__cc.hide(window.__ccT, true); return window.__ccT.length;
    }, i);
    if (!n) { await page.evaluate(() => window.__cc.hide(window.__ccT, false)); continue; }
    const png = (await page.screenshot({ type: 'png' })).toString('base64');
    const scored = await page.evaluate(async (b64) => { const r = await window.__cc.score(window.__ccT, b64); window.__cc.hide(window.__ccT, false); return r; }, png);
    for (const s of scored) results.push({ state: `${state} :hover`, ...s });
    measured++;
  }
  await page.mouse.move(1, 1);
  stateLog.push({ state: `${state} :hover`, targets: measured, verdict: measured ? 'PASS' : 'FAIL',
                  note: measured ? `${measured} of ${count} hoverable controls measured (cap ${HOVER_CAP})` : 'ZERO hoverable controls measured' });
}

// ---- states -------------------------------------------------------------------
const STATES = [
  { name: 'default (empty state)', hover: !process.env.CC_SELFTEST, open: async (p) => {
    // CC_SELFTEST: plant three texts with KNOWN ratios on a solid white box, so
    // the instrument is proven to fail what it should fail and pass what it
    // should pass (#777 = 4.48:1 FAIL; #767676 = 4.54:1 PASS; 24px #949494 = 3.03:1 PASS as large).
    if (!process.env.CC_SELFTEST) return;
    await p.evaluate(() => {
      const box = document.createElement('div');
      box.style.cssText = 'position:fixed;left:40px;top:40px;z-index:2147483647;background:#fff;padding:12px;font:14px/1.4 sans-serif';
      box.innerHTML = '<div id="cc-st-fail" style="color:#777">CCSELFTEST fail 4.48</div>'
        + '<div id="cc-st-pass" style="color:#767676">CCSELFTEST pass 4.54</div>'
        + '<div id="cc-st-large" style="color:#949494;font-size:24px">CCSELFTEST large 3.03</div>';
      document.body.appendChild(box);
    });
  } },
  { name: 'new-project modal', open: async (p) => { await p.click('#new-project-btn'); await p.waitForSelector('.modal-overlay', { state: 'visible', timeout: 5000 }); }, scope: '.modal-overlay' },
  { name: 'template picker', open: async (p) => { await p.keyboard.press('Control+Shift+T'); await p.waitForSelector('.tp-overlay', { state: 'visible', timeout: 5000 }); }, scope: '.tp-overlay' },
  { name: 'shortcuts overlay', open: async (p) => { await p.click('#shortcuts-btn'); await p.waitForSelector('#shortcuts-overlay:not([hidden])', { state: 'visible', timeout: 5000 }); }, scope: '#shortcuts-overlay' },
  { name: 'onboarding tour', onboarded: false, open: async (p) => { await p.waitForSelector('.ob-overlay', { state: 'visible', timeout: 5000 }); }, scope: '.ob-overlay' },
];
if (process.env.CC_EXTRA_STATES) {
  // JSON: [{ "name": "...", "click": "#selector", "key": "Control,", "wait": ".selector",
  //          "scope": ".selector", "hover": false,
  //          "script": "(function(){ /* seed a state-dependent element */ })()" }]
  for (const s of JSON.parse(process.env.CC_EXTRA_STATES)) {
    STATES.push({ name: s.name, scope: s.scope ?? null, hover: !!s.hover,
      open: async (p) => {
        if (s.script) await p.evaluate(s.script);
        if (s.click) await p.click(s.click);
        if (s.key) await p.keyboard.press(s.key);
        if (s.wait) await p.waitForSelector(s.wait, { state: 'visible', timeout: 5000 });
      } });
  }
}

for (const st of STATES) {
  const { ctx, page } = await newPage({ onboarded: st.onboarded !== false });
  try {
    try { await st.open(page); } catch (e) {
      stateLog.push({ state: st.name, targets: 0, verdict: 'INCONCLUSIVE', note: 'could not open: ' + String(e.message).split('\n')[0] });
      continue;
    }
    await page.waitForTimeout(300);
    await measure(page, st.name, st.scope ?? null);
    if (st.hover) await hoverPass(page, st.name);
  } finally { await ctx.close(); }
}
await browser.close();

// ---- report -------------------------------------------------------------------
const fails = results.filter((r) => r.verdict === 'FAIL');
const pairs = new Map();
for (const r of results.filter((r) => r.fg)) {
  const k = `${r.fg} on ${r.bg}`;
  const p = pairs.get(k) ?? { k, worst: Infinity, need: r.need, n: 0, fail: 0, ex: r };
  p.n++; if (r.verdict === 'FAIL') p.fail++;
  if (r.ratio < p.worst) { p.worst = r.ratio; p.ex = r; p.need = r.need; }
  pairs.set(k, p);
}
console.log('\n=== states ===');
for (const s of stateLog) console.log(`  ${s.verdict.padEnd(13)} ${s.state.padEnd(34)} ${s.note}`);
console.log(`\n=== failing elements (${fails.length}) ===`);
for (const f of fails.sort((a, b) => a.ratio - b.ratio))
  console.log(`  FAIL  ${String(f.ratio).padStart(5)}:1 (min ${f.min}, need ${f.need})  ${f.fg} on ~${f.bg}  ${f.px}px/${f.wt}${f.placeholder ? ' placeholder' : ''}  "${f.text}"  [${f.state}]  ${f.sel}`);
console.log(`\n=== distinct colour pairs: ${pairs.size} (failing: ${[...pairs.values()].filter((p) => p.fail).length}) ===`);
const exempt = results.filter((r) => r.verdict === 'EXEMPT').length;
const inconc = results.filter((r) => r.verdict === 'INCONCLUSIVE');
for (const i of inconc) console.log(`  INCONCLUSIVE  ${i.note}  "${i.text}"  [${i.state}]  ${i.sel}`);
console.log(`\n${results.length} text measurements · ${fails.length} failed · ${exempt} exempt (disabled) · ${inconc.length} inconclusive · ${stateLog.length} state(s)`);
if (process.env.CC_JSON) { const fs = await import('node:fs'); fs.writeFileSync(process.env.CC_JSON, JSON.stringify({ stateLog, results, pairs: [...pairs.values()].map(({ ex, ...p }) => ({ ...p, example: ex.text, sel: ex.sel })) }, null, 1)); }
if (process.env.CC_SELFTEST) {
  const want = { 'CCSELFTEST fail 4.48': 'FAIL', 'CCSELFTEST pass 4.54': 'PASS', 'CCSELFTEST large 3.03': 'PASS' };
  let ok = true;
  for (const [t, v] of Object.entries(want)) {
    const r = results.find((x) => x.text === t);
    const good = r && r.verdict === v;
    if (!good) ok = false;
    console.log(`SELFTEST ${good ? 'OK   ' : 'WRONG'} "${t}" expected ${v}, got ${r ? r.verdict + ' ' + r.ratio + ':1 (min ' + r.min + ')' : 'NOT MEASURED'}`);
  }
  process.exit(ok ? 0 : 1);
}
const stateBad = stateLog.filter((s) => s.verdict === 'FAIL').length;
const stateInc = stateLog.filter((s) => s.verdict === 'INCONCLUSIVE').length;
if (fails.length || stateBad) { console.log('RESULT: FAIL'); process.exit(1); }
if (inconc.length || stateInc) { console.log('RESULT: INCONCLUSIVE'); process.exit(2); }
console.log('RESULT: PASS - every measured text meets WCAG 2.1 AA 1.4.3'); process.exit(0);
