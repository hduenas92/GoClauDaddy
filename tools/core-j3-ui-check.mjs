// RESTORATION CORE J3 frozen oracle, UI half (Opus, 2026-10-14): the theme/motion CONTRACT, measured in a real browser against an
// isolated clone server (GCA_URL). Never :8765. Every check prints exactly one "PASS <id>" or "FAIL <id>" line, also on a base that does not
// comply (each step catches its own errors), then "CHECKS_REACHED <n>". Form-agnostic: it reads computed styles and the stylesheet rules,
// it does not care which selector form a look uses (the Python half pins the structural rule).
//
// CONTRACT (frozen from the record: RESTORATION-CORE J3 row, RULINGS 10, AUDIT 2.1, S4, OVERSIGHTS A9, Q2):
//  C1 token set      every look in LOOKS, in each MODE, SUPPLIES every token in REQ through a rule keyed on [data-theme] (a bare :root fallback
//                    does not count); that includes --motion-ms and the font tokens. LOOKS = calm, dense, warm, bold.
//  C1b mode          localStorage gca_theme + gca_mode (light|dark|system) set data-theme and data-mode on <html> BEFORE <body> exists
//                    (pre-paint); light is really light (ground luminance > 0.5), dark really dark (< 0.2). System follows the OS
//                    (prefers-color-scheme); unset gca_mode = system. Unset/unknown gca_theme = calm.
//  C2 contrast       text >= 4.5:1 (WCAG formula, alpha-composited backgrounds), all text, no large-text relaxation, per element class:
//                    template-picker category count (.tp-cat-count), composer hint (#composer-hint), and every other visible text node in the
//                    default state and the open template picker. Disabled controls are exempt. Legacy looks (cyberpunk-console, lit-workbench)
//                    stay guarded in their one mode.
//  C3 overflow       code/diff cards (.msg-bubble pre, .code-frame pre, .tool-call-input, .tool-call-result): a long line either wraps or
//                    scrolls inside the card; never text-overflow:ellipsis, never silently clipped (overflow hidden/clip with content wider
//                    than the box), never a horizontal page scrollbar, and the card box itself always fits inside the chat area (an unwrapped
//                    unscrollable card that widens its bubble makes the whole chat pane scroll: added after mutant 'code pre no longer scrolls' survived).
//  C4 reduced motion with prefers-reduced-motion: reduce, --motion-ms resolves to <= 1ms, no element has a transition or animation
//                    longer than 10ms, and no infinite animation is running (app-wide, every look).
//  C5 fonts          zero requests to non-localhost hosts across every load; every non-system primary font family named by --font-ui /
//                    --font-shell has a FontFace that loaded from a same-origin @font-face.
//  instrument_selftest proves the contrast measurer (alpha compositing) and the luminance helper on known pairs.
import { chromium } from 'playwright';

const URL = process.env.GCA_URL;
if (!URL || URL.includes(':8765')) { console.log('FAIL refusing: need an isolated GCA_URL'); process.exit(1); }
const ORIGIN = new globalThis.URL(URL).origin;
const LOOKS = ['calm', 'dense', 'warm', 'bold'];
const MODES = ['dark', 'light'];
const LEGACY = ['cyberpunk-console', 'lit-workbench'];
const REQ = [
  '--ground-rgb', '--panel-rgb', '--surface-rgb', '--hover-rgb', '--border-quiet-rgb', '--border-strong-rgb',
  '--text-primary-rgb', '--text-secondary-rgb', '--text-muted-rgb', '--accent-rgb', '--accent-hi-rgb', '--cta-rgb',
  '--working-rgb', '--success-rgb', '--warning-rgb', '--error-rgb', '--shadow-rgb',
  '--violet', '--cyan', '--amber-rgb', '--rose-rgb', '--sky-rgb',
  '--role-user', '--role-assistant', '--role-system', '--role-error', '--status-warn', '--status-info', '--panel-header',
  '--accent2-rgb', '--cta-label-rgb', '--font-ui', '--font-shell', '--motion-ms',
];
const SYSTEM_FONTS = new Set(['segoe ui', 'cascadia code', 'cascadia mono', 'consolas', 'system-ui', 'sans-serif', 'monospace', 'serif',
  'ui-monospace', 'ui-sans-serif', 'arial', 'helvetica', 'georgia', 'times new roman', 'courier new']);

const results = new Map();
const ok = (id, cond, detail = '') => {
  if (results.has(id)) return;
  results.set(id, !!cond);
  console.log(`${cond ? 'PASS' : 'FAIL'} ${id}${detail ? ' :: ' + String(detail).slice(0, 500) : ''}`);
};
const step = async (ids, fn) => {
  try { await fn(); } catch (e) { for (const id of ids) ok(id, false, 'step error: ' + (e?.message || e)); }
  for (const id of ids) if (!results.has(id)) ok(id, false, 'not asserted');
};

const browser = await chromium.launch();
const external = new Set();
async function open({ theme, mode, scheme = 'dark', reduced = 'no-preference', width = 1440, height = 900, seed = true, ls = {} } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, reducedMotion: reduced });
  ctx.on('request', (r) => {
    try { const u = new globalThis.URL(r.url()); if (/^https?:$/.test(u.protocol) && u.origin !== ORIGIN) external.add(u.origin); } catch { /* ignore */ }
  });
  const page = await ctx.newPage();
  await page.addInitScript(({ theme, mode, seed, ls }) => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]);
      if (seed && theme) localStorage.setItem('gca_theme', theme);
      if (seed && mode) localStorage.setItem('gca_mode', mode);
    } catch { /* blocked */ }
    window.__pre = { theme: null, mode: null, bodyExisted: null };
    const orig = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (n, v) {
      if (this === document.documentElement && (n === 'data-theme' || n === 'data-mode')) {
        if (window.__pre.bodyExisted === null) window.__pre.bodyExisted = !!document.body;
        window.__pre[n === 'data-theme' ? 'theme' : 'mode'] = v;
      }
      return orig.call(this, n, v);
    };
  }, { theme, mode, seed, ls });
  await page.goto(URL + '/', { waitUntil: 'load' });
  await page.waitForTimeout(400);
  return { ctx, page };
}

// ---- in-page helpers (serialised into evaluate) ---------------------------------------------------------------------------------
const HELPERS = `
(() => {
  const parse = (s) => {
    let m = s.match(/^rgba?\\(([^)]+)\\)$/);
    if (m) { const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; }
    m = s.match(/^color\\(srgb ([^)]+)\\)$/);
    if (m) { const p = m[1].split(/[ \\/]+/).filter(Boolean).map(Number); return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: p.length > 3 ? p[3] : 1 }; }
    return null;
  };
  const over = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
  const lum = (c) => { const k = [c.r, c.g, c.b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * k[0] + 0.7152 * k[1] + 0.0722 * k[2]; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const effBg = (el) => {
    const chain = [];
    for (let e = el; e; e = e.parentElement) chain.push(e);
    let base = { r: 255, g: 255, b: 255, a: 1 }, uncertain = false;
    for (let i = chain.length - 1; i >= 0; i--) {
      const cs = getComputedStyle(chain[i]);
      if (cs.backgroundImage && cs.backgroundImage !== 'none' && !/^url/.test(cs.backgroundImage)) uncertain = true;
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) base = over(c, base);
    }
    return { bg: base, uncertain };
  };
  const effOpacity = (el) => { let o = 1; for (let e = el; e; e = e.parentElement) o *= Number(getComputedStyle(e).opacity); return o; };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      if (e.hasAttribute('hidden') || e.hasAttribute('inert')) return false;
    }
    return effOpacity(el) > 0.05;
  };
  const disabled = (el) => !!el.closest('[disabled],[aria-disabled="true"]');
  const sweep = (scopeSel) => {
    const scope = scopeSel ? document.querySelector(scopeSel) : document.body;
    if (!scope) return { n: 0, bad: [], skipped: 0, missing: true };
    const seen = new Set(), bad = []; let n = 0, skipped = 0;
    const w = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    for (let t = w.nextNode(); t; t = w.nextNode()) {
      if (!t.nodeValue.trim()) continue;
      const el = t.parentElement;
      if (!el || seen.has(el) || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) continue;
      seen.add(el);
      if (!visible(el) || disabled(el)) continue;
      const cs = getComputedStyle(el);
      const fg0 = parse(cs.color); if (!fg0) { skipped++; continue; }
      const { bg, uncertain } = effBg(el);
      if (uncertain) { skipped++; continue; }
      const fg = over({ ...fg0, a: fg0.a * effOpacity(el) }, bg);
      const r = ratio(fg, bg); n++;
      if (r < 4.5) bad.push({ sel: (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).join('.') : '') || el.tagName, ratio: Math.round(r * 100) / 100, size: cs.fontSize, text: t.nodeValue.trim().slice(0, 24) });
    }
    return { n, bad, skipped, missing: false };
  };
  const one = (sel) => {
    const els = [...document.querySelectorAll(sel)].filter((e) => visible(e) && !disabled(e));
    if (!els.length) return { absent: true, bad: [] };
    const bad = [];
    for (const el of els) {
      const cs = getComputedStyle(el), fg0 = parse(cs.color), { bg, uncertain } = effBg(el);
      if (!fg0 || uncertain) { bad.push({ sel, ratio: 0, note: 'unmeasurable' }); continue; }
      const r = ratio(over({ ...fg0, a: fg0.a * effOpacity(el) }, bg), bg);
      if (r < 4.5) bad.push({ sel, ratio: Math.round(r * 100) / 100, size: cs.fontSize, text: el.textContent.trim().slice(0, 24) });
    }
    return { absent: false, bad, n: els.length };
  };
  window.__j3 = { parse, over, lum, ratio, sweep, one, visible };
})();
`;

async function install(page) { await page.evaluate(HELPERS); }
async function openPicker(page) {
  await page.keyboard.press('Control+Shift+T');
  await page.waitForSelector('.tp-overlay', { state: 'visible', timeout: 5000 });
  await page.waitForTimeout(250);
}
const fmt = (bad) => bad.slice(0, 4).map((b) => `${b.sel} ${b.ratio}:1 ${b.size || ''}`).join('; ');

// ---- instrument self-test --------------------------------------------------------------------------------------------------------
await step(['instrument_selftest'], async () => {
  const { ctx, page } = await open({ theme: 'calm', mode: 'dark' });
  await install(page);
  const r = await page.evaluate(() => {
    const J = window.__j3, W = { r: 255, g: 255, b: 255, a: 1 }, K = { r: 0, g: 0, b: 0, a: 1 };
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;left:0;top:0;width:200px;height:40px;background:#000';
    const inner = document.createElement('span');
    inner.textContent = 'sample';
    inner.style.cssText = 'color:#808080;background:rgba(255,255,255,0.5);display:inline-block;padding:4px';
    d.appendChild(inner); document.body.appendChild(d);
    const out = J.one('span[style*="rgba(255, 255, 255, 0.5)"]');
    // expected: bg = 0.5 white over black = (127.5,127.5,127.5); fg #808080 = 128 -> ratio ~1.0 ; an opaque-bg mistake (white) would give ~3.9
    const bad = out.bad[0];
    d.remove();
    return { bw: J.ratio(W, K), expectedSmall: bad ? bad.ratio : null, lumW: J.lum(W), lumK: J.lum(K) };
  });
  await ctx.close();
  ok('instrument_selftest', Math.abs(r.bw - 21) < 0.01 && r.expectedSmall !== null && r.expectedSmall < 1.1 && r.lumW === 1 && r.lumK === 0,
    JSON.stringify(r));
});

// ---- C1 token set (supplied by the look, per mode) -----------------------------------------------------------------------------
const tokIds = LOOKS.flatMap((l) => MODES.map((m) => `tokens_${l}_${m}`));
await step(tokIds, async () => {
  const { ctx, page } = await open({ theme: 'calm', mode: 'dark' });
  for (const look of LOOKS) for (const mode of MODES) {
    const r = await page.evaluate(({ look, mode, REQ }) => {
      const h = document.documentElement;
      h.setAttribute('data-theme', look); h.setAttribute('data-mode', mode);
      const supplied = new Set();
      const visit = (rules) => {
        for (const rule of rules) {
          if (rule.cssRules && rule.type !== 1) { if (rule.type === 4 && !window.matchMedia(rule.conditionText || rule.media.mediaText).matches) continue; visit(rule.cssRules); continue; }
          if (rule.type !== 1) continue;
          for (const part of rule.selectorText.split(',')) {
            const sel = part.trim();
            if (!sel.includes('[data-theme')) continue;
            let m = false; try { m = h.matches(sel); } catch { m = false; }
            if (!m) continue;
            for (let i = 0; i < rule.style.length; i++) if (rule.style[i].startsWith('--')) supplied.add(rule.style[i]);
          }
        }
      };
      for (const ss of document.styleSheets) { try { visit(ss.cssRules); } catch { /* cross-origin */ } }
      const cs = getComputedStyle(h);
      const missing = REQ.filter((t) => !supplied.has(t));
      const empty = REQ.filter((t) => !cs.getPropertyValue(t).trim());
      return { missing, empty };
    }, { look, mode, REQ });
    ok(`tokens_${look}_${mode}`, r.missing.length === 0 && r.empty.length === 0,
      r.missing.length ? `not supplied by the look (${r.missing.length}): ${r.missing.slice(0, 8).join(' ')}` : `empty: ${r.empty.join(' ')}`);
  }
  await ctx.close();
});

// ---- C1b mode / registry behaviour -----------------------------------------------------------------------------------------------
const modeIds = LOOKS.flatMap((l) => MODES.map((m) => `mode_${l}_${m}`));
await step(modeIds, async () => {
  for (const look of LOOKS) for (const mode of MODES) {
    const { ctx, page } = await open({ theme: look, mode, scheme: mode === 'light' ? 'dark' : 'light' }); // OS scheme OPPOSITE: explicit mode must win
    const r = await page.evaluate(() => {
      const h = document.documentElement, g = getComputedStyle(h).getPropertyValue('--ground-rgb').trim().split(/\s+/).map(Number);
      const lin = g.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
      return { theme: h.getAttribute('data-theme'), mode: h.getAttribute('data-mode'), pre: window.__pre, lum: 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] };
    });
    const lumOk = mode === 'light' ? r.lum > 0.5 : r.lum < 0.2;
    ok(`mode_${look}_${mode}`, r.theme === look && r.mode === mode && r.pre && r.pre.theme === look && r.pre.mode === mode && r.pre.bodyExisted === false && lumOk,
      `theme=${r.theme} mode=${r.mode} pre=${JSON.stringify(r.pre)} groundLum=${r.lum.toFixed(3)}`);
    await ctx.close();
  }
});

await step(['default_is_calm', 'unknown_theme_falls_back', 'mode_system_light', 'mode_system_dark', 'mode_default_is_system'], async () => {
  let c = await open({ theme: null, mode: null });
  ok('default_is_calm', (await c.page.evaluate(() => document.documentElement.getAttribute('data-theme'))) === 'calm', 'no gca_theme saved');
  await c.ctx.close();
  c = await open({ theme: 'no-such-theme', mode: 'banana' });
  const u = await c.page.evaluate(() => ({ t: document.documentElement.getAttribute('data-theme'), m: document.documentElement.getAttribute('data-mode') }));
  ok('unknown_theme_falls_back', u.t === 'calm' && (u.m === 'light' || u.m === 'dark'), JSON.stringify(u));
  await c.ctx.close();
  c = await open({ theme: 'calm', mode: 'system', scheme: 'light' });
  ok('mode_system_light', (await c.page.evaluate(() => document.documentElement.getAttribute('data-mode'))) === 'light', 'gca_mode=system, OS light');
  await c.ctx.close();
  c = await open({ theme: 'calm', mode: 'system', scheme: 'dark' });
  ok('mode_system_dark', (await c.page.evaluate(() => document.documentElement.getAttribute('data-mode'))) === 'dark', 'gca_mode=system, OS dark');
  await c.ctx.close();
  c = await open({ theme: 'calm', mode: null, scheme: 'light' });
  ok('mode_default_is_system', (await c.page.evaluate(() => document.documentElement.getAttribute('data-mode'))) === 'light', 'no gca_mode, OS light');
  await c.ctx.close();
});

// ---- C2 contrast ---------------------------------------------------------------------------------------------------------------
async function contrastFor(theme, mode) {
  const { ctx, page } = await open({ theme, mode, scheme: mode === 'light' ? 'light' : 'dark' });
  await install(page);
  const out = { tp: null, hint: null, text: [], attrs: await page.evaluate(() => document.documentElement.getAttribute('data-theme') + '/' + document.documentElement.getAttribute('data-mode')) };
  out.themeOk = out.attrs.split('/')[0] === theme;
  const hint = await page.evaluate(() => window.__j3.one('#composer-hint'));
  const d0 = await page.evaluate(() => window.__j3.sweep(null));
  await openPicker(page);
  await install(page);
  const tp = await page.evaluate(() => window.__j3.one('.tp-cat-count'));
  const d1 = await page.evaluate(() => window.__j3.sweep('.tp-overlay'));
  await ctx.close();
  out.hint = hint; out.tp = tp; out.text = [...d0.bad, ...d1.bad]; out.n = d0.n + d1.n;
  return out;
}
const cIds = LOOKS.flatMap((l) => [`contrast_tpcat_${l}`, `contrast_hint_${l}`, `contrast_text_${l}`]);
await step(cIds, async () => {
  for (const look of LOOKS) {
    const per = [];
    for (const mode of MODES) per.push({ mode, ...(await contrastFor(look, mode)) });
    const bad = (k) => per.flatMap((p) => (p[k] ? p[k].bad.map((b) => ({ ...b, sel: `${p.mode}:${b.sel}` })) : []));
    ok(`contrast_tpcat_${look}`, per.every((p) => p.themeOk && p.tp && !p.tp.absent) && bad('tp').length === 0, fmt(bad('tp')) || 'ok');
    ok(`contrast_hint_${look}`, per.every((p) => p.themeOk && p.hint && !p.hint.absent) && bad('hint').length === 0, fmt(bad('hint')) || 'ok');
    const t = per.flatMap((p) => p.text.map((b) => ({ ...b, sel: `${p.mode}:${b.sel}` })));
    ok(`contrast_text_${look}`, per.every((p) => p.themeOk && p.n > 20) && t.length === 0, t.length ? `${t.length} below 4.5: ${fmt(t)}` : per.every((p) => p.themeOk) ? `n=${per.map((p) => p.n).join('/')}` : `look not applied: ${per.map((p) => p.attrs).join(',')}`);
  }
});
await step(LEGACY.map((l) => `legacy_contrast_${l}`), async () => {
  for (const id of LEGACY) {
    const p = await contrastFor(id, 'dark');
    const bad = [...(p.hint?.bad || []), ...(p.tp?.bad || [])]; // named classes only: legacy looks are guarded where S4 measured them, not swept
    ok(`legacy_contrast_${id}`, bad.length === 0 && p.themeOk && !p.hint.absent && !p.tp.absent, bad.length ? fmt(bad) : `n=${p.n} attrs=${p.attrs}`);
  }
});

// ---- C3 overflow rule ------------------------------------------------------------------------------------------------------------
const oIds = ['overflow_code_pre', 'overflow_code_frame', 'overflow_tool_input', 'overflow_tool_result', 'overflow_page'];
await step(oIds, async () => {
  const agg = Object.fromEntries(oIds.map((i) => [i, []]));
  for (const look of LOOKS) {
    const { ctx, page } = await open({ theme: look, mode: 'dark', width: 1100 });
    const r = await page.evaluate(() => {
      /* fixture mirrors the real DOM: chat_pane.js renders markdown <pre>/.code-frame INSIDE .text-block; tool-call details sit directly in the bubble */ const long = 'x'.repeat(520), diff = '+ ' + 'newLine_'.repeat(70), wrap = document.getElementById('chat-messages');
      if (!wrap) return { overflow_code_pre: 'missing #chat-messages', overflow_code_frame: 'missing #chat-messages', overflow_tool_input: 'missing #chat-messages', overflow_tool_result: 'missing #chat-messages', overflow_page: 'missing #chat-messages' };
      const host = document.createElement('div');
      host.innerHTML =
        // one message per element: the bubble is shrink-to-fit, so a shared bubble would couple the verdicts
        `<div class="msg msg-assistant"><div class="msg-bubble"><div class="text-block"><pre id="o-pre"><code>${long}</code></pre></div></div></div>
         <div class="msg msg-assistant"><div class="msg-bubble"><div class="text-block"><div class="code-frame"><div class="code-tabbar"></div><pre id="o-frame"><code>${diff}</code></pre></div></div></div></div>
         <div class="msg msg-assistant"><div class="msg-bubble"><details class="tool-call-block" open><summary class="tool-call-summary">Edit</summary>
           <pre class="tool-call-input" id="o-in">${diff}</pre></details></div></div>
         <div class="msg msg-assistant"><div class="msg-bubble"><details class="tool-call-block" open><summary class="tool-call-summary">Edit</summary>
           <div class="tool-call-result" id="o-out">${long}</div></details></div></div>`;
      while (host.firstElementChild) wrap.appendChild(host.firstElementChild);
      const res = {};
      const judge = (id, key) => {
        const e = document.getElementById(id); if (!e) { res[key] = 'missing'; return; }
        const bad = [];
        const nodes = [e, ...e.querySelectorAll('*')];
        for (const n of nodes) if (getComputedStyle(n).textOverflow === 'ellipsis') bad.push('ellipsis on ' + n.tagName);
        const ox = getComputedStyle(e).overflowX;
        if (e.scrollWidth > e.clientWidth + 1 && !['auto', 'scroll'].includes(ox)) bad.push(`content ${e.scrollWidth}px in ${e.clientWidth}px box, overflow-x:${ox}`);
        const er = e.getBoundingClientRect(), wr = wrap.getBoundingClientRect();
        if (er.right > wr.right + 1) bad.push(`card box ${Math.round(er.right - wr.right)}px wider than the chat area (the chat pane, not the card, would scroll)`);
        for (let a = e.parentElement; a; a = a.parentElement) {
          const aox = getComputedStyle(a).overflowX;
          if (a.scrollWidth > a.clientWidth + 1 && ['hidden', 'clip'].includes(aox)) bad.push(`ancestor ${a.className || a.tagName} clips (${a.scrollWidth}>${a.clientWidth})`);
          if (a === wrap) break;
        }
        res[key] = bad.join('; ');
      };
      judge('o-pre', 'overflow_code_pre'); judge('o-frame', 'overflow_code_frame'); judge('o-in', 'overflow_tool_input'); judge('o-out', 'overflow_tool_result');
      const de = document.documentElement;
      res.overflow_page = de.scrollWidth > innerWidth + 1 ? `page scrolls horizontally (${de.scrollWidth}>${innerWidth})` : '';
      return res;
    });
    for (const k of oIds) if (r[k]) agg[k].push(`${look}: ${r[k]}`);
    await ctx.close();
  }
  for (const k of oIds) ok(k, agg[k].length === 0, agg[k].slice(0, 3).join(' | ') || 'wraps or scrolls inside the card, all looks');
});

// ---- C4 reduced motion -----------------------------------------------------------------------------------------------------------
await step(LOOKS.map((l) => `reduced_motion_${l}`), async () => {
  for (const look of LOOKS) {
    const { ctx, page } = await open({ theme: look, mode: 'dark', reduced: 'reduce' });
    await install(page);
    await openPicker(page);
    const r = await page.evaluate(() => {
      const h = document.documentElement, motion = getComputedStyle(h).getPropertyValue('--motion-ms').trim();
      const ms = /^([\d.]+)\s*(ms|s)$/.exec(motion); const val = ms ? Number(ms[1]) * (ms[2] === 's' ? 1000 : 1) : null;
      const sec = (s) => Math.max(0, ...s.split(',').map((x) => { const v = parseFloat(x); return x.trim().endsWith('ms') ? v / 1000 : v; }));
      const bad = [];
      for (const el of document.querySelectorAll('*')) {
        for (const ps of [null, '::before', '::after']) {
          const cs = getComputedStyle(el, ps);
          const hasT = cs.transitionProperty && cs.transitionProperty !== 'none' && cs.transitionProperty !== 'all 0s';
          if (hasT && sec(cs.transitionDuration) > 0.011) bad.push(`transition ${sec(cs.transitionDuration)}s on ${el.tagName}${el.className ? '.' + String(el.className).split(' ')[0] : ''}${ps || ''}`);
          if (cs.animationName !== 'none' && sec(cs.animationDuration) > 0.011) bad.push(`animation ${cs.animationName} ${sec(cs.animationDuration)}s on ${el.tagName}${ps || ''}`);
        }
        if (bad.length > 6) break;
      }
      const inf = document.getAnimations().filter((a) => a.playState === 'running' && a.effect && a.effect.getComputedTiming().iterations === Infinity).length;
      return { attr: h.getAttribute('data-theme'), motion, val, bad, inf };
    });
    ok(`reduced_motion_${look}`, r.attr === look && r.val !== null && r.val <= 1 && r.bad.length === 0 && r.inf === 0,
      `theme=${r.attr} --motion-ms=${r.motion || 'unset'} infinite=${r.inf} ${r.bad.slice(0, 3).join('; ')}`);
    await ctx.close();
  }
});

// ---- C5 fonts ---------------------------------------------------------------------------------------------------------------------
await step(['fonts_local', 'no_external_requests'], async () => {
  const problems = [];
  for (const look of LOOKS) {
    const { ctx, page } = await open({ theme: look, mode: 'dark' });
    const r = await page.evaluate(async (SYS) => {
      const cs = getComputedStyle(document.documentElement), out = [];
      const faces = [];
      for (const ss of document.styleSheets) {
        let rules; try { rules = ss.cssRules; } catch { out.push('cross-origin stylesheet ' + (ss.href || '')); continue; }
        for (const rule of rules) if (rule.type === 5) faces.push({ fam: rule.style.getPropertyValue('font-family').replace(/["']/g, '').trim(), src: rule.style.getPropertyValue('src') });
      }
      for (const f of faces) if (/https?:\/\/(?!(127\.0\.0\.1|localhost))/.test(f.src)) out.push(`remote @font-face ${f.fam}`);
      for (const tok of ['--font-ui', '--font-shell']) {
        const first = (cs.getPropertyValue(tok).split(',')[0] || '').replace(/["']/g, '').trim();
        if (!first || SYS.includes(first.toLowerCase()) || first.startsWith('var(')) continue;
        if (!faces.some((f) => f.fam.toLowerCase() === first.toLowerCase())) { out.push(`${tok} primary "${first}" has no local @font-face`); continue; }
        try { await document.fonts.load(`16px "${first}"`); } catch { /* fall through */ }
        const loaded = [...document.fonts].some((ff) => ff.family.replace(/["']/g, '').toLowerCase() === first.toLowerCase() && ff.status === 'loaded');
        if (!loaded) out.push(`${tok} "${first}" did not load`);
      }
      return out;
    }, [...SYSTEM_FONTS]);
    if (r.length) problems.push(`${look}: ${r.slice(0, 2).join(', ')}`);
    await ctx.close();
  }
  ok('fonts_local', problems.length === 0, problems.slice(0, 4).join(' | ') || 'every non-system primary family loaded from a same-origin @font-face');
  ok('no_external_requests', external.size === 0, external.size ? 'requests to: ' + [...external].join(', ') : 'only ' + ORIGIN);
});

// ===========================================================================================================================================
// RULED AMENDMENT R-J3-2 (Houston, 2026-10-14, after the J3 gate failed 24/28 on core-j3 570608e): the 52 checks above are unchanged (their
// ids and bodies are frozen). These checks add what the gate caught and the oracle never asserted:
//  C6 console lines  every console line level a real run can render (.lvl-error/.lvl-warn/.lvl-info/.lvl-debug, in the real #rsb-console)
//                    is >= 4.5:1 (alpha-composited, same instrument as C2), per look x mode. Gate probe: ui-check 'SEEDED' lines.
//  C7 focus ring     every visible keyboard-focusable control (the gate's FOCUSABLE set, same visibility filter, same before/after pixel
//                    method as tools/a11y-check.mjs '2.4.7+1.4.11 focus visible: pixel sweep') shows a focus indicator >= 3:1 against the
//                    adjacent ring pixels, per look x mode, on the empty app PLUS the real message markup (.msg-actions .copy-btn /
//                    .msg-action-btn, built as chat_pane.js buildMessageEl does). Guarded: the sweep must reach >= 20 controls and must
//                    include #conv-search, #composer-send, .copy-btn and .msg-action-btn (a sweep that misses them proves nothing).
//  Legacy looks (cyberpunk-console, lit-workbench) are guarded in their one (dark) mode.
// ===========================================================================================================================================
const FOCUSABLE = 'button, a[href], input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex="-1"]), [role="button"]';
const AMEND_COMBOS = [...LOOKS.flatMap((l) => MODES.map((m) => [l, m])), ...LEGACY.map((l) => [l, 'dark'])];
// KNOWN BASELINE, excluded from C7 and recorded (not fixed here, not hidden): the same sweep also fails calm/dark (button.msg-action-btn 2.89:1) and
// cyberpunk-console/dark (msg-action-btn 2.99:1, copy-btn/msg-action-btn no visible change) IDENTICALLY on master 8b7b507, so J3 did not cause them and
// the ruling scopes the fix to light mode. Every other combination is asserted, dark ones as guards.
const FOCUS_COMBOS = AMEND_COMBOS.filter(([l, m]) => !(m === 'dark' && (l === 'calm' || l === 'cyberpunk-console')));
const amendIds = (p, combos = AMEND_COMBOS) => combos.map(([l, m]) => `${p}_${l}_${m}`);
async function amendPage(theme, mode) {
  const { ctx, page } = await open({ theme, mode, scheme: mode === 'light' ? 'light' : 'dark', ls: { gca_sb_open: '1' } });
  await install(page);
  return { ctx, page };
}
await step(amendIds('console_lines'), async () => {
  for (const [theme, mode] of AMEND_COMBOS) {
    const { ctx, page } = await amendPage(theme, mode);
    const r = await page.evaluate(() => {
      const host = document.querySelector('#rsb-console');
      if (!host) return { missing: true };
      const levels = ['error', 'warn', 'info', 'debug'];
      for (const l of levels) { const d = document.createElement('div'); d.className = 'console-line lvl-' + l; d.textContent = 'probe: ' + l + ' line'; host.appendChild(d); }
      return { levels: levels.map((l) => ({ l, ...window.__j3.one('#rsb-console .console-line.lvl-' + l) })) };
    });
    const attrs = await page.evaluate(() => document.documentElement.getAttribute('data-theme') + '/' + document.documentElement.getAttribute('data-mode'));
    const bad = r.missing ? [] : r.levels.flatMap((x) => x.bad.map((b) => ({ ...b, sel: x.l })));
    const absent = r.missing ? ['#rsb-console'] : r.levels.filter((x) => x.absent).map((x) => x.l);
    ok(`console_lines_${theme}_${mode}`, attrs.split('/')[0] === theme && !absent.length && !bad.length,
      bad.length ? bad.map((b) => `${b.sel} ${b.ratio}:1 ${b.size || ''}`).join('; ') : absent.length ? 'not rendered/visible: ' + absent.join(',') : 'error/warn/info/debug all >= 4.5:1');
    await ctx.close();
  }
});
await step(amendIds('focus_ring', FOCUS_COMBOS), async () => {
  for (const [theme, mode] of FOCUS_COMBOS) {
    const { ctx, page } = await amendPage(theme, mode);
    await page.evaluate(() => {
      const wrap = document.getElementById('chat-messages');
      if (!wrap) return;
      const mk = (role, text, btns) => `<div class="msg msg-${role} glow"><div class="msg-bubble"><div class="text-block">${text}</div></div><div class="msg-meta">08:00 AM</div><div class="msg-actions">${btns}</div></div>`;
      const h = document.createElement('div');
      h.innerHTML = mk('user', 'Show me the helper.', '<button class="copy-btn" title="Copy">⎘</button><button class="msg-action-btn" title="Retry">↺</button>')
        + mk('assistant', 'Here is the helper you asked for.', '<button class="copy-btn" title="Copy">⎘</button><button class="msg-action-btn regen-btn" title="Regenerate">↺</button>');
      while (h.firstElementChild) wrap.appendChild(h.firstElementChild);
      const st = document.createElement('style');
      st.textContent = '*,*::before,*::after{transition:none!important;animation-play-state:paused!important;caret-color:transparent!important}';
      document.head.appendChild(st);
    });
    const controls = await page.evaluate((foc) => [...document.querySelectorAll(foc)].filter((el) => {
      if (el.disabled) return false;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return false;
      if (el.closest('[hidden]') || el.closest('details:not([open])') || el.closest('.sb-collapsed')) return false;
      if (r.top < -4 || r.left < -4 || r.right > innerWidth + 4 || r.bottom > innerHeight + 4) return false;
      return true;
    }).slice(0, 60).map((el, idx) => {
      const r = el.getBoundingClientRect();
      const cls = (el.className && typeof el.className === 'string' ? el.className.trim().split(/\s+/)[0] : '');
      const sel = el.id ? '#' + CSS.escape(el.id) : el.tagName.toLowerCase() + (cls ? '.' + CSS.escape(cls) : '');
      el.dataset.a11yIdx = String(idx);
      return { sel, idx, x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
    }), FOCUSABLE);
    await page.evaluate(() => document.activeElement?.blur?.());
    await page.waitForTimeout(80);
    const before = (await page.screenshot({ type: 'png' })).toString('base64');
    const offenders = [];
    for (const c of controls) {
      const rect = { x: Math.max(0, c.x - 8), y: Math.max(0, c.y - 8), w: c.w + 16, h: c.h + 16 };
      const after = await page.evaluate((idx) => {
        const el = document.querySelector(`[data-a11y-idx="${idx}"]`);
        if (!el) return { outline: 'not-found' };
        el.focus({ preventScroll: true });
        const cs = getComputedStyle(el);
        return { outline: cs.outlineStyle + '/' + cs.outlineWidth + ' ' + cs.outlineColor, shadow: cs.boxShadow === 'none' ? '' : ' shadow ' + cs.boxShadow.slice(0, 40) };
      }, c.idx);
      await page.waitForTimeout(60);
      const afterShot = (await page.screenshot({ type: 'png' })).toString('base64');
      const scored = await page.evaluate(async ({ a, b, rect }) => {
        const load = (s) => new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.src = 'data:image/png;base64,' + s; });
        const [ia, ib] = [await load(a), await load(b)];
        const w = rect.w, h = rect.h, cv = new OffscreenCanvas(w, h), cx = cv.getContext('2d');
        cx.drawImage(ia, rect.x, rect.y, w, h, 0, 0, w, h); const da = cx.getImageData(0, 0, w, h).data;
        cx.drawImage(ib, rect.x, rect.y, w, h, 0, 0, w, h); const db = cx.getImageData(0, 0, w, h).data;
        let changed = 0, mR = 0, mG = 0, mB = 0, rR = 0, rG = 0, rB = 0, rN = 0; const inner = 3;
        for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
          const i = (y * w + x) * 4;
          const d = Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]);
          const inInner = x > inner && y > inner && x < w - inner && y < h - inner;
          if (d > 24) { changed++; mR += db[i]; mG += db[i + 1]; mB += db[i + 2]; } else if (!inInner) { rR += db[i]; rG += db[i + 1]; rB += db[i + 2]; rN++; }
        }
        if (!changed) return { changed: 0, ratio: null };
        if (!rN) return { changed, ratio: null };
        return { changed, ratio: +window.__j3.ratio({ r: mR / changed, g: mG / changed, b: mB / changed }, { r: rR / rN, g: rG / rN, b: rB / rN }).toFixed(2) };
      }, { a: before, b: afterShot, rect });
      await page.evaluate(() => document.activeElement?.blur?.());
      await page.waitForTimeout(40);
      if (!scored.changed) offenders.push(`${c.sel} (no visible change; ${after.outline})`);
      else if (!(scored.ratio >= 3)) offenders.push(`${c.sel} ${scored.ratio}:1 [${after.outline}${after.shadow}]`);
    }
    const sels = controls.map((c) => c.sel);
    const need = ['#conv-search', '#composer-send', 'button.copy-btn', 'button.msg-action-btn'].filter((s) => !sels.includes(s));
    const attrs = await page.evaluate(() => document.documentElement.getAttribute('data-theme') + '/' + document.documentElement.getAttribute('data-mode'));
    ok(`focus_ring_${theme}_${mode}`, attrs.split('/')[0] === theme && controls.length >= 20 && !need.length && !offenders.length,
      offenders.length ? `${offenders.length} of ${controls.length} fail: ${offenders.slice(0, 6).join(' | ')}` : need.length ? `sweep missed ${need.join(',')} (swept ${controls.length})` : controls.length < 20 ? `only ${controls.length} controls swept` : `${controls.length} controls swept, all >= 3:1`);
    await ctx.close();
  }
});

await browser.close();
console.log(`CHECKS_REACHED ${results.size}`);
