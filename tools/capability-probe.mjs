/**
 * capability-probe.mjs — what can a user actually reach in the running app?
 *
 * Not a pass/fail harness. This is an evidence-gathering instrument for a scope
 * decision: static reading tells you a control exists, but not whether a person
 * can get to it. Reachability is a runtime property.
 *
 * Deliberately separate from ui-check.mjs so it can run while that file is being
 * edited, and so a scope probe never gets confused for a regression gate.
 */
import { chromium } from 'playwright';

const ASSERT = process.argv.includes('--assert');
const APP_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

const consoleMsgs = [];
page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') consoleMsgs.push(`${m.type()}: ${m.text()}`);
});
const failedReqs = [];
page.on('requestfailed', (r) => failedReqs.push(`${r.method()} ${r.url()} — ${r.failure()?.errorText}`));
const apiCalls = new Map();
page.on('response', (r) => {
  const u = new URL(r.url());
  if (u.pathname.startsWith('/api/')) {
    apiCalls.set(`${r.request().method()} ${u.pathname}`, r.status());
  }
});

await page.addInitScript(() => { try { localStorage.setItem('gca_onboarded', '1'); } catch {} });
await page.goto(APP_URL, { waitUntil: 'networkidle' });
await page.waitForTimeout(1200);

const data = await page.evaluate(() => {
  // Opacity is INHERITED VISUALLY but not as a computed value: a button inside
  // `.msg-actions { opacity: 0 }` reports its OWN opacity as 1. Reading only the
  // element's own computed opacity therefore calls an invisible control visible.
  // That is exactly what the first version of this probe did — it reported
  // "possibly hover-gated: 0" and listed every per-message Copy button as
  // visible, while all of them sit inside an opacity:0 container until hover.
  // Effective opacity must be multiplied up the ancestor chain.
  const effectiveOpacity = (el) => {
    let o = 1;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      o *= Number(getComputedStyle(n).opacity);
      if (o <= 0.01) return 0;
    }
    return o;
  };

  const vis = (el) => {
    const r = el.getBoundingClientRect();
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    }
    return effectiveOpacity(el) > 0.01 && r.width > 0 && r.height > 0;
  };

  // An accessible name is what a screen reader announces AND what a person reads.
  // A control with none is unusable by either.
  const name = (el) =>
    (el.getAttribute('aria-label') || el.getAttribute('title') ||
     el.textContent?.trim() || el.getAttribute('placeholder') || '').slice(0, 60);

  // Which ancestor is actually hiding this control, and how? Naming the mechanism
  // matters: `opacity: 0` keeps the element in the tab order and clickable (so a
  // keyboard user can reach an invisible button), while `display: none` removes it
  // from both. Those need different fixes, so the probe must distinguish them.
  const hiddenBy = (el) => {
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const who = n === el ? 'self' : (n.className && typeof n.className === 'string'
        ? '.' + n.className.trim().split(/\s+/)[0] : n.tagName.toLowerCase());
      if (cs.display === 'none') return `${who} display:none`;
      if (cs.visibility === 'hidden') return `${who} visibility:hidden`;
      if (Number(cs.opacity) <= 0.01) return `${who} opacity:${cs.opacity}`;
    }
    return null;
  };

  const controls = [...document.querySelectorAll(
    'button, a[href], input, select, textarea, summary, [role="button"], [onclick], [tabindex]',
  )].map((el) => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    cls: (el.className && typeof el.className === 'string' ? el.className : '').slice(0, 50) || null,
    name: name(el),
    visible: vis(el),
    hiddenBy: hiddenBy(el),
    disabled: !!el.disabled,
    inCollapsed: !!el.closest('details:not([open])') ||
                 !!el.closest('.sb-collapsed, [hidden], dialog:not([open])'),
    hasAccessibleName: name(el).length > 0,
    effectiveOpacity: effectiveOpacity(el),
    inTabOrder: el.tabIndex >= 0,
  }));

  const msgActionSel = '.msg-actions button, .copy-btn, .code-copy-btn';
  const msgActionOffenders = [...document.querySelectorAll(msgActionSel)]
    .filter((el) => effectiveOpacity(el) <= 0.01)
    .map((el) => `<${el.tagName.toLowerCase()}> ${el.className ? '.' + String(el.className).split(' ')[0] : ''} "${name(el)}"`);
  const tabOrderOffenders = controls
    .filter((c) => c.inTabOrder && !c.inCollapsed && c.effectiveOpacity <= 0.01)
    .map((c) => `<${c.tag}> ${c.id ? '#' + c.id : (c.cls ? '.' + c.cls.split(' ')[0] : '')} "${c.name}"`);

  const clickableNonSemantic = [...document.querySelectorAll('div, span, li')]
    .filter((el) => el.onclick || el.getAttribute('onclick'))
    .filter((el) => !el.getAttribute('role') && !el.hasAttribute('tabindex'))
    .map((el) => `${el.tagName.toLowerCase()}.${(el.className || '').toString().slice(0, 40)}`);

  return {
    title: document.title,
    controls,
    msgActionOffenders,
    tabOrderOffenders,
    clickableNonSemantic,
    dialogs: [...document.querySelectorAll('dialog')].map((d) => ({ id: d.id, open: d.open })),
    detailsSections: [...document.querySelectorAll('details')].map((d) => ({
      id: d.id || null, summary: d.querySelector('summary')?.textContent?.trim().slice(0, 30), open: d.open,
    })),
    localStorageKeys: (() => { try { return Object.keys(localStorage); } catch { return ['<blocked>']; } })(),
    emptyStateVisible: !!document.querySelector('.empty-state') &&
                        getComputedStyle(document.querySelector('.empty-state')).display !== 'none',
    messageCount: document.querySelectorAll('.msg').length,
  };
});

await browser.close();

const L = (s = '') => console.log(s);
L(`=== capability-probe  ${APP_URL} ===`);
L(`page title       : ${data.title}`);
L(`messages on load : ${data.messageCount}   empty-state visible: ${data.emptyStateVisible}`);
L(`localStorage keys: ${data.localStorageKeys.join(', ') || '(none)'}`);
L();

const c = data.controls;
L(`Controls found: ${c.length}`);
L(`  visible now          : ${c.filter((x) => x.visible).length}`);
L(`  hidden now           : ${c.filter((x) => !x.visible).length}`);
L(`  inside collapsed/modal: ${c.filter((x) => x.inCollapsed).length}`);
L(`  HIDDEN AT REST        : ${c.filter((x) => x.hiddenBy && !x.inCollapsed).length}`);
L(`  disabled             : ${c.filter((x) => x.disabled).length}`);
L(`  NO accessible name   : ${c.filter((x) => !x.hasAccessibleName).length}`);
L();

const unnamed = c.filter((x) => !x.hasAccessibleName);
if (unnamed.length) {
  L('Controls with NO accessible name (unusable by screen reader AND by eye):');
  for (const x of unnamed) L(`  <${x.tag}> id=${x.id ?? '-'} class=${x.cls ?? '-'} visible=${x.visible}`);
  L();
}

const hoverish = c.filter((x) => x.hiddenBy && !x.inCollapsed);
if (hoverish.length) {
  L('HIDDEN AT REST — in the DOM, invisible to the eye (mechanism named):');
  for (const x of hoverish) L(`  <${x.tag}> ${x.id ? "#"+x.id : "."+(x.cls??"").split(" ")[0]}  "${x.name}"   hidden by: ${x.hiddenBy}`);
  L();
}

L('Visible top-level controls (what a user sees with no interaction):');
for (const x of c.filter((v) => v.visible && !v.inCollapsed)) {
  L(`  <${x.tag}> ${x.id ? '#' + x.id : (x.cls ? '.' + x.cls.split(' ')[0] : '')}  "${x.name}"`);
}
L();

L('<details> sections and default state:');
for (const d of data.detailsSections) L(`  ${d.open ? 'OPEN  ' : 'closed'} ${d.id ?? '-'}  "${d.summary ?? ''}"`);
L();
L(`<dialog> elements: ${data.dialogs.map((d) => `${d.id || '?'}(${d.open ? 'open' : 'closed'})`).join(', ') || '(none)'}`);
L();

if (data.clickableNonSemantic.length) {
  L('Click handlers on non-semantic elements (no role, no tabindex — keyboard cannot reach):');
  for (const s of data.clickableNonSemantic) L(`  ${s}`);
  L();
}

L(`API calls made during a plain page load (${apiCalls.size}):`);
for (const [k, v] of [...apiCalls].sort()) L(`  ${v}  ${k}`);
L();
L(`Failed requests: ${failedReqs.length}`);
for (const f of failedReqs) L(`  ${f}`);
L();
L(`Console errors/warnings: ${consoleMsgs.length}`);
for (const m of consoleMsgs) L(`  ${m}`);

if (ASSERT) {
  L();
  const offenders = [...new Set([...data.msgActionOffenders, ...data.tabOrderOffenders])];
  if (offenders.length) {
    L(`ASSERT FAILED — ${offenders.length} control(s) hidden at rest (effective opacity <= 0.01):`);
    for (const o of offenders) L(`  ${o}`);
    process.exit(1);
  }
  L('ASSERT PASSED — no message-action control or tab-order element is hidden at rest.');
  process.exit(0);
}
