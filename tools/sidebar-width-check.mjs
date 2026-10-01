/**
 * Right sidebar starts collapsed on narrow windows without losing the user's choice,
 * and "collapsed" really collapses — in every theme.
 *
 * Below 1280 px the sidebar must start collapsed even if the user last left it
 * open; at >= 1280 px the saved choice (gca_sb_open) is honoured; the saved
 * value itself must never be rewritten just by loading the page narrow.
 * In both themes and every state: collapsed renders <= 1 px wide, paints no border or
 * shadow, and is out of the
 * keyboard order and out of Chrome's accessibility tree (what a screen reader reads);
 * open renders at the theme's design width and is reachable; #sb-toggle-btn reports the state
 * (aria-expanded) and names what it controls (aria-controls="right-sidebar").
 * The button and Ctrl+B flip all of that and save the choice.
 * Exit 0 = every case passes, 1 = any case fails.
 * Control: GCA_SIM_FIX=visibility|inert simulates a correct fix inside the page and must
 * PASS; GCA_SIM_FIX=flatten (correct collapse, but the theme's open width lost) must FAIL
 * only on the cyberpunk open cases; GCA_SIM_FIX=ghost (inert, but border/shadow still
 * painted) must FAIL only on collapsed cases; unset, against 52a2da6 (theme width overrides the
 * collapse, no ARIA state) it FAILs.
 */
import { chromium } from 'playwright';

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const SIM = process.env.GCA_SIM_FIX ?? '';
const OPEN_WIDTH = { 'cyberpunk-console': 320, 'lit-workbench': 220 }; // theme design widths
const THEMES = Object.keys(OPEN_WIDTH);
const loads = [
  { name: 'narrow 1024, saved open  -> collapsed', width: 1024, saved: '1', collapsed: true },
  { name: 'narrow 1279, saved open  -> collapsed', width: 1279, saved: '1', collapsed: true },
  { name: 'wide 1280, saved open    -> open', width: 1280, saved: '1', collapsed: false },
  { name: 'wide 1440, saved open    -> open', width: 1440, saved: '1', collapsed: false },
  { name: 'wide 1440, saved closed  -> collapsed', width: 1440, saved: '0', collapsed: true },
  { name: 'wide 1440, nothing saved -> collapsed', width: 1440, saved: null, collapsed: true },
];

// Control only: the end state a correct fix produces (collapse wins over the theme width,
// collapsed content hidden from keyboard + AT, toggle exposes its state).
function simulateFix(mode) {
  if (!mode) return;
  const wire = () => {
    const sb = document.getElementById('right-sidebar');
    const btn = document.getElementById('sb-toggle-btn');
    if (!sb || !btn) return false;
    const sync = () => {
      const c = sb.classList.contains('sb-collapsed');
      btn.setAttribute('aria-expanded', String(!c));
      btn.setAttribute('aria-controls', 'right-sidebar');
      if (mode === 'inert' || mode === 'ghost') sb.inert = c;
    };
    new MutationObserver(sync).observe(sb, { attributes: true, attributeFilter: ['class'] });
    sync();
    return true;
  };
  document.addEventListener('DOMContentLoaded', () => {
    const st = document.createElement('style');
    const hide = mode === 'inert' ? 'border-left-color: transparent !important; box-shadow: none !important;'
      : mode === 'ghost' ? '' : 'visibility: hidden;';
    st.textContent = `#right-sidebar.sb-collapsed { width: 0 !important; ${hide} }`
      + (mode === 'flatten' ? ' #right-sidebar:not(.sb-collapsed) { width: 220px !important; }' : '');
    document.head.append(st);
    if (!wire()) new MutationObserver((_, o) => { if (wire()) o.disconnect(); }).observe(document.body, { childList: true, subtree: true });
  });
}

async function readState(page, cdp) {
  // Wait for every running animation/transition on #right-sidebar to finish.
  // Force a style flush first so a transition that has not started yet is
  // instantiated, and bound the wait so a stuck page cannot hang the harness.
  await page.evaluate(() => {
    const el = document.getElementById('right-sidebar');
    if (!el) return;
    void getComputedStyle(el).width; // force style flush
    const finished = Promise.all(el.getAnimations().map(a => a.finished.catch(() => {})));
    const timeout = new Promise(resolve => setTimeout(resolve, 3000));
    return Promise.race([finished, timeout]);
  });
  // Second guard: after transitions settle, confirm two equal width readings.
  let w = -1;
  for (let i = 0; i < 20; i += 1) {
    const now = await page.evaluate(() => document.getElementById('right-sidebar').getBoundingClientRect().width);
    if (now === w) break;
    w = now;
    await page.waitForTimeout(100);
  }
  const s = await page.evaluate(() => {
    const sb = document.getElementById('right-sidebar');
    const btn = document.getElementById('sb-toggle-btn');
    const sum = document.querySelector('#rsb-panel-session > summary');
    sum.focus();
    const focusable = document.activeElement === sum;
    document.activeElement?.blur();
    const cs = getComputedStyle(sb);
    const n = cs.borderLeftColor.match(/[\d.]+/g) ?? [];
    const borderAlpha = n.length === 4 ? Number(n[3]) : 1;
    return {
      ghost: cs.visibility !== 'hidden' && (borderAlpha > 0 || cs.boxShadow !== 'none'),
      theme: document.documentElement.dataset.theme,
      collapsed: sb.classList.contains('sb-collapsed'),
      width: Math.round(sb.getBoundingClientRect().width),
      expanded: btn.getAttribute('aria-expanded'),
      controls: btn.getAttribute('aria-controls'),
      focusable,
      saved: localStorage.getItem('gca_sb_open'),
    };
  });
  const { root } = await cdp.send('DOM.getDocument', { depth: 0 });
  const exposed = async (selector) => {
    const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector });
    if (!nodeId) return false;
    const { nodes } = await cdp.send('Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false });
    return nodes.length > 0 && !nodes[0].ignored;
  };
  s.exposed = (await exposed('#right-sidebar')) || (await exposed('#rsb-panel-session > summary'));
  return s;
}

function problems(s, theme, wantCollapsed, wantSaved) {
  const bad = [];
  if (s.theme !== theme) bad.push(`theme=${s.theme}`);
  if (s.collapsed !== wantCollapsed) bad.push(`collapsed=${s.collapsed}`);
  if (wantCollapsed ? s.width > 1 : s.width !== OPEN_WIDTH[theme]) bad.push(`width=${s.width}`);
  if (wantCollapsed && s.ghost) bad.push('border/shadow still painted');
  if (s.expanded !== String(!wantCollapsed)) bad.push(`aria-expanded=${s.expanded}`);
  if (s.controls !== 'right-sidebar') bad.push(`aria-controls=${s.controls}`);
  if (s.focusable === wantCollapsed) bad.push(`focusable=${s.focusable}`);
  if (s.exposed === wantCollapsed) bad.push(`in-a11y-tree=${s.exposed}`);
  if (s.saved !== wantSaved) bad.push(`saved=${JSON.stringify(s.saved)}`);
  return bad;
}

const browser = await chromium.launch();
let failed = 0;
let total = 0;
const report = (theme, name, bad, s) => {
  total += 1;
  if (bad.length) failed += 1;
  console.log(`${bad.length ? 'FAIL' : 'PASS'}  [${theme}] ${name}  -> ${bad.length ? bad.join(' ') : `width=${s.width} aria-expanded=${s.expanded}`}`);
};
async function open(theme, width, saved) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 } });
  const page = await ctx.newPage();
  await page.addInitScript(({ saved, theme }) => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_theme', theme);
      if (saved === null) localStorage.removeItem('gca_sb_open');
      else localStorage.setItem('gca_sb_open', saved);
    } catch {}
  }, { saved, theme });
  await page.addInitScript(simulateFix, SIM);
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#rsb-panel-session > summary', { state: 'attached', timeout: 8000 });
  await page.waitForTimeout(300);
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Accessibility.enable');
  return { ctx, page, cdp };
}

for (const theme of THEMES) {
  for (const c of loads) {
    const { ctx, page, cdp } = await open(theme, c.width, c.saved);
    const s = await readState(page, cdp);
    report(theme, c.name, problems(s, theme, c.collapsed, c.saved), s);
    await ctx.close();
  }
  const { ctx, page, cdp } = await open(theme, 1440, '1');
  let s = await readState(page, cdp);
  report(theme, 'toggle: start open', problems(s, theme, false, '1'), s);
  await page.click('#sb-toggle-btn');
  s = await readState(page, cdp);
  report(theme, 'toggle: button   -> collapsed, saved 0', problems(s, theme, true, '0'), s);
  await page.keyboard.press('Control+b');
  s = await readState(page, cdp);
  report(theme, 'toggle: Ctrl+B   -> open, saved 1', problems(s, theme, false, '1'), s);
  await ctx.close();
}
await browser.close();
console.log(failed ? `RESULT: FAIL — ${failed} of ${total}` : `RESULT: PASS — ${total} cases`);
process.exit(failed ? 1 : 0);
