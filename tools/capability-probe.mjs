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

// Navigation uses domcontentloaded, never networkidle. networkidle is
// documented by Playwright as discouraged and inherently racy, and it hung in a
// clean Linux container against the /api/server/logs SSE stream. It was also
// redundant here: every navigation below is followed by an explicit wait, and
// that is what actually establishes readiness.

const ASSERT = process.argv.includes('--assert');
const APP_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

// ---- Fixture conversation, served by route interception ---------------------
// The probe's per-message assertions (Copy, "Reuse this message") exist only
// while a transcript containing a user message is open. Real data must not be
// required for that, so the conversation list, the boot-time POST (the app
// issues one when the list is empty — main.js:296-298), and the conversation
// detail are all fulfilled from a fixture and nothing real is ever written.
// GCA_PROBE_ZERO_CONVS=1 forces the LIST to be empty to prove the probe no
// longer depends on it: boot then creates via the intercepted POST, which
// returns this same fixture, and the probe still runs against it.
const FIXTURE_ID = 'probe-fixture-0001';
const ZERO_CONVS = process.env.GCA_PROBE_ZERO_CONVS === '1';
const fixtureConversation = () => ({
  id: FIXTURE_ID,
  name: 'Probe fixture conversation',
  project_id: null,
  model: 'claude-sonnet-5',
  status: 'idle',
  source: 'web',
  cost_usd: 0,
  session_id: null,
  system_prompt: null,
  permission_mode: null,
  thinking_budget: null,
  max_tokens: null,
  created_at: new Date(Date.now() - 120_000).toISOString(),
  updated_at: new Date(Date.now() - 30_000).toISOString(),
  completed_at: null,
  started_at: null,
});
const fixtureMessages = () => [
  {
    id: 'probe-msg-1', conversation_id: FIXTURE_ID, role: 'user',
    content: 'Probe fixture: is every control reachable and honestly labelled?',
    thinking: null, tool_calls: null, input_tokens: 12, output_tokens: null,
    cache_read_tokens: 0, cache_creation_tokens: 0, model: 'claude-sonnet-5',
    seq: 1, created_at: new Date(Date.now() - 60_000).toISOString(),
    stopped: 0, superseded_by: null,
  },
  {
    id: 'probe-msg-2', conversation_id: FIXTURE_ID, role: 'assistant',
    content: 'Probe fixture reply.',
    thinking: null, tool_calls: null, input_tokens: 6, output_tokens: 24,
    cache_read_tokens: 0, cache_creation_tokens: 0, model: 'claude-sonnet-5',
    seq: 2, created_at: new Date(Date.now() - 30_000).toISOString(),
    stopped: 0, superseded_by: null,
  },
];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

// Scoped by exact pathname, never a `**/api/**` glob: that glob also matches
// the static module at /static/js/api/http.js and breaks module loading.
await page.route('**/*', async (route) => {
  const u = new URL(route.request().url());
  const m = route.request().method();
  if (u.pathname === '/api/conversations' && m === 'GET') {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(ZERO_CONVS ? [] : [fixtureConversation()]),
    });
    return;
  }
  if (u.pathname === '/api/conversations' && m === 'POST') {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(fixtureConversation()),
    });
    return;
  }
  if (u.pathname === `/api/conversations/${FIXTURE_ID}` && m === 'GET') {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ conversation: fixtureConversation(), messages: fixtureMessages() }),
    });
    return;
  }
  await route.continue();
});

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
await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);

// Open a conversation before measuring. The per-message controls — Copy and the
// reuse button — are built per message, so on an empty transcript the whole
// `.msg-actions` sweep runs over an EMPTY SET and reports a clean pass having
// measured nothing. That is the vacuous-verification failure this project has
// already paid for once. The conversation now comes from the fixture above, not
// from real data: click the fixture row when it rendered (default mode), and in
// ZERO_CONVS mode rely on boot's POST-created fixture — either way the wait for
// a real user message is what gates the sweep, not the presence of a row.
const convRow = await page.$('.conv-body');
if (convRow) {
  await convRow.click();
}
await page.waitForSelector('.msg-user', { timeout: 8000 }).catch(() => {});
await page.waitForTimeout(800);

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
    // Label inventory for tasks 2.2 / 2.3. Collected as data rather than asserted
    // in-page so the non-empty-set guard lives with the other assertions.
    titledControls: [...document.querySelectorAll('[title]')].map((el) => ({
      what: el.id
        ? '#' + el.id
        : (el.className && typeof el.className === 'string'
            ? '.' + el.className.trim().split(/\s+/)[0]
            : el.tagName.toLowerCase()),
      title: el.getAttribute('title') || '',
    })),
    // The reuse control is built only for user messages, so its absence must read
    // as INCONCLUSIVE, never as a pass.
    userMsgCount: document.querySelectorAll('.msg-user').length,
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
  const failures = [];
  let inconclusive = 0;

  // 4-I0 — nothing that looks functional may be invisible at rest.
  const offenders = [...new Set([...data.msgActionOffenders, ...data.tabOrderOffenders])];
  if (offenders.length) {
    failures.push(`${offenders.length} control(s) hidden at rest (effective opacity <= 0.01):\n` +
      offenders.map((o) => `    ${o}`).join('\n'));
  }

  // Non-empty-set guard (§2.2). A label sweep over zero titled controls passes
  // vacuously, which is the exact failure mode that already cost this project a
  // session. Fail loudly instead.
  if (data.titledControls.length === 0) {
    L('ASSERT FAILED — no [title] control found at all; every label assertion below would pass vacuously.');
    process.exit(1);
  }

  // Task 2.3 (4-I2) — a hint that advertises a key binding with no handler has
  // negative value: a beginner who tries it learns to distrust every hint.
  const phantom = data.titledControls.filter((c) => /\(F2\)|\(Ctrl\+Shift\+A\)/.test(c.title));
  if (phantom.length) {
    failures.push(`${phantom.length} control(s) advertise a shortcut with no handler:\n` +
      phantom.map((c) => `    ${c.what} title="${c.title}"`).join('\n'));
  }

  // Task 2.2 (4-I9) — the control neither edits nor retries; the label must not claim it does.
  const dishonest = data.titledControls.filter((c) => /Edit\s*&\s*retry/i.test(c.title));
  if (dishonest.length) {
    failures.push(`${dishonest.length} control(s) still labelled "Edit & retry":\n` +
      dishonest.map((c) => `    ${c.what} title="${c.title}"`).join('\n'));
  }

  // Positive assertions. Each is guarded so it cannot pass over an absent element.
  const attach = data.titledControls.find((c) => c.what === '#composer-attach');
  if (!attach) {
    failures.push('#composer-attach not found — its label could not be asserted');
  } else if (attach.title !== 'Attach file') {
    failures.push(`#composer-attach title is "${attach.title}", expected "Attach file"`);
  }

  const rename = data.titledControls.filter((c) => c.what === '.conv-rename');
  if (rename.length === 0) {
    L('INCONCLUSIVE — no .conv-rename button rendered; its label was not asserted.');
    inconclusive++;
  } else {
    const bad = rename.filter((c) => c.title !== 'Rename');
    if (bad.length) failures.push(`${bad.length} .conv-rename button(s) titled "${bad[0].title}", expected "Rename"`);
  }

  const reuse = data.titledControls.filter((c) => /Reuse this message/.test(c.title));
  if (data.userMsgCount === 0) {
    L('INCONCLUSIVE — no user message in the transcript, so the reuse control does not exist; its label was not asserted.');
    inconclusive++;
  } else if (reuse.length === 0) {
    failures.push(`${data.userMsgCount} user message(s) present but no control titled "Reuse this message"`);
  }

  if (failures.length) {
    L(`ASSERT FAILED — ${failures.length} violation(s):`);
    for (const f of failures) L(`  ${f}`);
    process.exit(1);
  }
  L(`ASSERT PASSED — nothing hidden at rest; no phantom shortcut hints; retry control honestly labelled.`);
  L(`             swept ${data.titledControls.length} titled control(s), ${data.userMsgCount} user message(s)`);
  if (inconclusive) {
    L(`RESULT: INCONCLUSIVE — ${inconclusive} assertion(s) could not be exercised`);
    process.exit(2);
  }
  process.exit(0);
}
