/**
 * a11y-check.mjs — WCAG 2.1 AA audit harness (P2-D, WRITE job).
 *
 * One named case per criterion/state, PASS / FAIL / INCONCLUSIVE, exit 0/1/2.
 * A non-empty guard runs FIRST: a sweep over an unrendered page is the
 * vacuous-verification failure this project keeps paying for.
 *
 * Methods:
 *   - axe-core (devDependency, Houston-approved) run per UI state with
 *     runOnly wcag2a/wcag2aa/wcag21a/wcag21aa. Every violation becomes a FAIL
 *     line carrying rule id, WCAG tags, and target.
 *   - Keyboard reachability/operability (2.1.1), trap (2.1.2 incl. xterm),
 *     single-char shortcuts (2.1.4), focus return (2.4.3).
 *   - Pixel focus-indicator sweep (2.4.7 + 1.4.11): whole-page screenshot
 *     before/after focusing each visible control, changed pixels inside the
 *     control's ring must contrast >= 3:1 against the adjacent unchanged ring.
 *   - Viewport cases: 1.4.4 at 720x450 CSS viewport @ deviceScaleFactor 2
 *     (NOT body.style.zoom), 1.4.10 at 320 CSS px, 1.4.12 with the WCAG
 *     text-spacing overrides injected.
 *   - Tooltips (1.4.13), status messages (4.1.3), delete confirmation (3.3.4),
 *     and the basics (2.4.2 title, 3.1.1 lang, 2.4.1 skip link/landmarks).
 *
 * All REST is route-intercepted so the real data dir is never written. The
 * chat WebSocket is mocked with page.routeWebSocket (Playwright >= 1.63).
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE (a case could not be exercised)
 */
import { chromium } from 'playwright';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
let AXE_PATH = null;
try {
  AXE_PATH = require.resolve('axe-core');
} catch {
  console.error('FATAL: axe-core is not resolvable from node_modules. Stopping.');
  process.exit(1);
}

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const browser = await chromium.launch();

const results = [];
const add = (label, state, expect, detail) => results.push({ label, state, expect, detail });

// ---- fixtures (route interception; the real data dir must never be written) ---
const PROJ = {
  id: 'p1', name: 'P2D Fixture Project', working_dir: null, system_prompt: null,
  created_at: '2026-09-25T00:00:00Z',
};
const CONV = {
  id: 'c1', project_id: null, name: 'P2D Fixture Chat', session_id: null,
  model: 'claude-sonnet-4-6', permission_mode: null, system_prompt: null,
  thinking_budget: null, max_tokens: null, status: 'idle', source: 'web',
  cost_usd: 0.5,
  created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:05:00Z',
  started_at: null, completed_at: null,
};
const MESSAGES = [
  {
    id: 'm1', conversation_id: 'c1', role: 'user',
    content: 'Fixture question: hello contrast world', thinking: null,
    tool_calls: null, input_tokens: 12, output_tokens: null, model: 'claude-sonnet-4-6',
    cache_read_tokens: 0, cache_creation_tokens: 0, seq: 1,
    created_at: '2026-09-25T00:01:00Z', stopped: 0, superseded_by: null,
  },
  {
    id: 'm2', conversation_id: 'c1', role: 'assistant',
    content: 'Fixture reply with a **bold** word and a code block below.',
    thinking: 'Fixture thinking text.', tool_calls: null,
    input_tokens: 12, output_tokens: 24, model: 'claude-sonnet-4-6',
    cache_read_tokens: 0, cache_creation_tokens: 0, seq: 2,
    created_at: '2026-09-25T00:02:00Z', stopped: 0, superseded_by: null,
  },
];
const TEMPLATES = [
  { id: 't1', title: 'Report writer', category: 'report', description: 'A report template', body: 'Write a report about {{topic}}', is_builtin: true },
  { id: 't2', title: 'Email reply', category: 'email', description: 'An email template', body: 'Reply to {{sender}}', is_builtin: true },
  { id: 't3', title: 'Custom note', category: 'custom', description: 'A custom template', body: 'Note: {{note}}', is_builtin: false },
];
const CONFIG_BODY = {
  models: [
    { value: 'claude-sonnet-4-6', label: 'Sonnet 4.6', context_window: 1000000, input_rate: 3, output_rate: 15 },
    { value: 'claude-opus-4-5', label: 'Opus 4.5', context_window: 1000000, input_rate: 5, output_rate: 25 },
  ],
  default_model: 'claude-sonnet-4-6',
  permission_modes: ['auto', 'acceptEdits', 'plan'],
};

async function stubBoot(page, { projects = [PROJ], conversations = [CONV], messages = MESSAGES } = {}) {
  // Pathname-based routing so query strings (?project_id=…) still match.
  // A `**/api/conversations` glob does NOT match `/api/conversations?project_id=p1`.
  await page.route('**/*', async (r) => {
    const u = new globalThis.URL(r.request().url());
    const m = r.request().method();
    const p = u.pathname;
    if (p === '/api/projects' && m === 'GET') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(projects) });
    }
    if (p === '/api/flow-templates' && m === 'GET') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TEMPLATES) });
    }
    if (p === '/api/conversations') {
      if (m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(conversations) });
      if (m === 'POST') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONV) });
    }
    if (p === '/api/conversations/c1') {
      if (m === 'GET') {
        return r.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({ conversation: CONV, messages, model_correction: null }),
        });
      }
    }
    if (p === '/api/conversations/c1/stats') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ step_count: 0, tool_call_count: 0 }) });
    }
    if (p === '/api/config' && m === 'GET') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONFIG_BODY) });
    }
    if (p === '/api/server/info') {
      const m = /^https?:\/\/([^/:]+)(?::(\d+))?/.exec(URL);
      return r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ url: URL, host: m?.[1] ?? '127.0.0.1', port: m?.[2] ? Number(m[2]) : 80 }),
      });
    }
    if (p === '/api/server/stats') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ chat_count: 1, message_count: 2, total_input: 12, total_output: 24, monthly_cost_usd: 0, monthly_input: 0, monthly_output: 0, budget_usd: 200 }) });
    }
    if (p === '/api/agents/status') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    if (p === '/api/teams') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    }
    return r.continue();
  });
}

const SEED = {
  onboarded: true, sbOpen: true, teams: false, terminal: false, assess: false, approval: false,
};

async function newPage(seed = {}) {
  const s = { ...SEED, ...seed };
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(({ s }) => {
    try {
      localStorage.setItem('gca_onboarded', s.onboarded ? '1' : '0');
      if (!s.onboarded) localStorage.removeItem('gca_onboarded');
      localStorage.setItem('gca_sb_open', s.sbOpen ? '1' : '0');
      localStorage.setItem('gca_cost_notified', new Date().toISOString().slice(0, 10));
      localStorage.setItem('gca_feat_teams', s.teams ? '1' : '0');
      localStorage.setItem('gca_feat_terminal', s.terminal ? '1' : '0');
      localStorage.setItem('gca_feat_assess', s.assess ? '1' : '0');
      localStorage.setItem('gca_feat_approval', s.approval ? '1' : '0');
    } catch { /* private mode */ }
  }, { s });
  const page = await ctx.newPage();
  await page.routeWebSocket('**/ws/chat/*', (route) => {
    // Playwright mocking mode: the page socket auto-opens; frames are
    // injected with route.send() when a case needs a stream.
    if (!route._mock) { route._mock = true; }
  });
  return { ctx, page };
}

async function bootPage(page, { stub = true } = {}) {
  if (stub) await stubBoot(page);
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#composer-input', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(600);
  return page;
}

const FOCUSABLE =
  'button, a[href], input:not([type=hidden]), select, textarea, summary, [tabindex]:not([tabindex="-1"]), [role="button"]';

// ===========================================================================
// CASE 0 — non-empty guard, FIRST. Everything after this may assume a live page.
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    const guard = await page.evaluate((foc) => {
      const els = [...document.querySelectorAll(foc)].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && !el.closest('[hidden]') && !el.closest('details:not([open])');
      });
      return {
        total: document.querySelectorAll(foc).length,
        visible: els.length,
        hasComposer: !!document.getElementById('composer-input'),
        hasAllConversations: !!document.querySelector('#project-list [data-all="1"]'),
        hasHeader: !!document.getElementById('settings-btn'),
      };
    }, FOCUSABLE);
    const ok = guard.visible >= 10 && guard.hasComposer && guard.hasAllConversations && guard.hasHeader;
    add('guard: page renders a non-empty interactive control set', ok ? 'PASS' : 'FAIL',
      '>= 10 visible focusable controls, composer, All conversations, header present',
      `total=${guard.total} visible=${guard.visible} composer=${guard.hasComposer} allConv=${guard.hasAllConversations} header=${guard.hasHeader}`);
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// axe-core run per state
// ===========================================================================
async function axeCase(label, seed, open) {
  const { ctx, page } = await newPage(seed);
  try {
    await bootPage(page);
    let opened = true;
    if (open) {
      try { await open(page); } catch (e) {
        add(`axe ${label}`, 'INCONCLUSIVE', 'axe ran with 0 violations in this state', `could not open state: ${String(e.message).split('\n')[0]}`);
        opened = false;
      }
    }
    if (!opened) return;
    await page.addScriptTag({ path: AXE_PATH });
    const res = await page.evaluate(async () => {
      return await window.axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
        resultTypes: ['violations', 'incomplete'],
      });
    });
    const viol = res.violations || [];
    const inc = res.incomplete || [];
    if (viol.length === 0) {
      add(`axe ${label}`, 'PASS', 'axe reports 0 violations in this state',
        `0 violations${inc.length ? `, ${inc.length} incomplete (reported, not failed)` : ''}`);
    } else {
      for (const v of viol) {
        const targets = v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' ; ');
        add(`axe ${label}: ${v.id}`, 'FAIL', `rule ${v.id} passes`,
          `impact=${v.impact} tags=${(v.tags || []).join(',')} target=${targets}`);
      }
    }
  } finally {
    await ctx.close();
  }
}

await axeCase('default state', {}, null);
await axeCase('new-project modal', {}, async (page) => {
  await page.click('#new-project-btn');
  await page.waitForSelector('.modal-overlay', { state: 'visible', timeout: 5000 });
});
await axeCase('template picker', {}, async (page) => {
  await page.keyboard.press('Control+Shift+T');
  await page.waitForSelector('.tp-overlay', { state: 'visible', timeout: 5000 });
  await page.waitForSelector('.tp-card', { timeout: 5000 }).catch(() => {});
});
await axeCase('shortcuts overlay', {}, async (page) => {
  await page.click('#shortcuts-btn');
  await page.waitForSelector('#shortcuts-overlay:not([hidden])', { state: 'visible', timeout: 5000 });
});
await axeCase('onboarding tour', { onboarded: false }, null);
await axeCase('settings panel', {}, async (page) => {
  await page.click('#settings-btn');
  await page.waitForSelector('.settings-drawer.drawer-open', { state: 'visible', timeout: 5000 });
});
await axeCase('right sidebar expanded', { sbOpen: true }, async (page) => {
  await page.waitForSelector('#right-sidebar:not(.sb-collapsed)', { timeout: 5000 });
});

// ===========================================================================
// 2.1.1 — keyboard reachability + operability
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    const sweep = await page.evaluate((foc) => {
      const els = [...document.querySelectorAll(foc)].filter((el) => {
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        if (el.closest('[hidden]')) return false;
        if (el.closest('details:not([open])')) return false;
        if (el.closest('.sb-collapsed')) return false;
        return true;
      });
      const bad = els.filter((el) => el.tabIndex < 0);
      const allConv = document.querySelector('#project-list [data-all="1"]');
      return {
        total: els.length,
        unreachable: bad.map((el) => `<${el.tagName.toLowerCase()}>${el.id ? '#' + el.id : '.' + String(el.className).split(' ')[0]}`),
        allConvTabIndex: allConv ? allConv.tabIndex : null,
        allConvRole: allConv ? (allConv.getAttribute('role') || null) : null,
      };
    }, FOCUSABLE);
    if (sweep.total < 10) {
      add('2.1.1 keyboard: every visible control is tab-reachable', 'FAIL',
        'no visible control may have tabIndex < 0', `swept ${sweep.total} (guard says this is too few)`);
    } else {
      add('2.1.1 keyboard: every visible control is tab-reachable',
        sweep.unreachable.length === 0 ? 'PASS' : 'FAIL',
        'no visible control may have tabIndex < 0',
        sweep.unreachable.length === 0
          ? `${sweep.total} controls swept, none with tabIndex -1`
          : `${sweep.unreachable.length} unreachable: ${sweep.unreachable.join(', ')}`);
    }

    // All conversations (sidebar_projects.js:36,75) must be reachable and operable.
    const allConv = await page.$('#project-list [data-all="1"]');
    if (!allConv) {
      add('2.1.1 keyboard: "All conversations" reachable and operable by Enter', 'INCONCLUSIVE',
        'focusing it and pressing Enter selects the All conversations view', 'the control is not in the DOM');
      add('2.1.1 keyboard: "All conversations" operable by Space', 'INCONCLUSIVE',
        'focusing it and pressing Space selects the All conversations view', 'the control is not in the DOM');
    } else {
      // Drive the UI into a project-filtered state first so activation is
      // observable. Target a PROJECT row, not the All-conversations row (whose
      // .project-name span is the first such span in the document).
      const projectName = await page.$('#project-list li.project-item:not([data-all="1"]) .project-name');
      if (projectName) { await projectName.click(); await page.waitForTimeout(300); }
      for (const key of ['Enter', 'Space']) {
        const activeBefore = await page.evaluate(() => !!document.querySelector('#project-list .project-item.active[data-all="1"]'));
        const focusTarget = await page.$('#project-list [data-all="1"] span.project-name');
        if (!focusTarget) {
          // H6: a missing element is a FAIL, never a TypeError on null.focus().
          add(`2.1.1 keyboard: "All conversations" operable by ${key}`, 'FAIL',
            `pressing ${key} on the focused control selects the All conversations view`,
            'the All-conversations project-name span is not in the DOM');
          continue;
        }
        await focusTarget.focus().catch(() => {});
        await page.keyboard.press(key);
        await page.waitForTimeout(350);
        const activeAfter = await page.evaluate(() => !!document.querySelector('#project-list .project-item.active[data-all="1"]'));
        add(`2.1.1 keyboard: "All conversations" operable by ${key}`,
          !activeBefore && activeAfter ? 'PASS' : 'FAIL',
          `pressing ${key} on the focused control selects the All conversations view`,
          `wasActiveBefore=${activeBefore} activeAfter=${activeAfter}`);
        // Re-select the project so the next key starts from the same state.
        const projectRow = await page.$('#project-list li.project-item:not([data-all="1"]) .project-name');
        if (projectRow) { await projectRow.click(); await page.waitForTimeout(250); }
      }
    }

    // .tp-card Enter/Space (mirrors focus-check; kept here so a11y-check stands alone).
    for (const key of ['Enter', 'Space']) {
      // A previous card with {{placeholders}} opens a fill-in modal; close any
      // surface first or Ctrl+Shift+T is inert while a modal is open.
      await page.keyboard.press('Escape').catch(() => {});
      await page.waitForTimeout(150);
      await page.keyboard.press('Control+Shift+T');
      const ok = await page.waitForSelector('.tp-card', { timeout: 5000 }).catch(() => null);
      if (!ok) {
        add(`2.1.1 keyboard: .tp-card operable by ${key}`, 'INCONCLUSIVE',
          'activating a focused card selects its template', 'no .tp-card rendered');
        continue;
      }
      const card = await page.$('.tp-card');
      await card.focus();
      await page.keyboard.press(key);
      await page.waitForTimeout(400);
      const st = await page.evaluate(() => ({
        overlayGone: !document.querySelector('.tp-overlay'),
        composerText: (document.getElementById('composer-input')?.value ?? '').length,
      }));
      add(`2.1.1 keyboard: .tp-card operable by ${key}`,
        st.overlayGone || st.composerText > 0 ? 'PASS' : 'FAIL',
        'activating a focused card selects its template',
        `overlayClosed=${st.overlayGone} composerChars=${st.composerText}`);
      // Close whatever the card opened so the next key starts clean.
      await page.keyboard.press('Escape').catch(() => {});
      await page.waitForTimeout(200);
    }
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 1.4.1 — conv-dot busy/error state must not be colour-only
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    const BUSY = { ...CONV, id: 'c-busy', name: 'Busy fixture', status: 'busy' };
    const ERR = { ...CONV, id: 'c-err', name: 'Error fixture', status: 'error' };
    const IDLE = { ...CONV };
    await stubBoot(page, { conversations: [BUSY, ERR, IDLE] });
    await bootPage(page, { stub: false });
    const dots = await page.evaluate(() =>
      [...document.querySelectorAll('.conv-dot-busy, .conv-dot-error')].map((el) => ({
        cls: el.className,
        label: el.getAttribute('aria-label'),
        role: el.getAttribute('role'),
        title: el.getAttribute('title'),
        text: (el.querySelector('.sr-only')?.textContent ?? '').trim(),
      })));
    // Non-empty guard first: zero dots is a renamed-selector failure, not a pass.
    const ok = dots.length >= 2 && dots.every((d) =>
      (d.label === 'Busy' || d.label === 'Error') && d.title === d.label &&
      (d.role === 'img' || d.text === d.label));
    add('1.4.1 conv-dot busy/error state is not colour-only', ok ? 'PASS' : 'FAIL',
      'every busy/error sidebar dot carries a text alternative (aria-label or sr-only text) and a matching title',
      JSON.stringify(dots));
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 2.1.2 — no keyboard trap (overlays + terminal)
// ===========================================================================
async function trapCase(label, seed, open, selector) {
  const { ctx, page } = await newPage(seed);
  try {
    await bootPage(page);
    try { await open(page); } catch {
      add(`2.1.2 no trap: ${label}`, 'INCONCLUSIVE', 'Tab from the last control stays inside', 'could not open the surface');
      return;
    }
    const appeared = await page.waitForSelector(selector, { state: 'visible', timeout: 5000 }).catch(() => null);
    if (!appeared) {
      add(`2.1.2 no trap: ${label}`, 'INCONCLUSIVE', 'Tab from the last control stays inside', 'surface never appeared');
      return;
    }
    await page.waitForFunction(
      ({ sel, foc }) => {
        const root = document.querySelector(sel);
        return !!root && root.querySelectorAll(foc).length > 0;
      },
      { sel: selector, foc: FOCUSABLE },
      { timeout: 5000 },
    ).catch(() => {});
    const r = await page.evaluate(({ sel, foc }) => {
      const root = document.querySelector(sel);
      const els = [...root.querySelectorAll(foc)].filter((el) => {
        const rr = el.getBoundingClientRect();
        return rr.width > 0 && rr.height > 0 && !el.disabled;
      });
      if (els.length === 0) return { total: 0, inside: null, where: '(no focusable controls)' };
      els[els.length - 1].focus();
      return { total: els.length, inside: true, first: els[0] === document.activeElement };
    }, { sel: selector, foc: FOCUSABLE });
    if (r.total === 0) {
      add(`2.1.2 no trap: ${label}`, 'FAIL', 'the surface exposes at least one focusable control', 'ZERO focusable controls — a trap check over nothing passes vacuously');
      return;
    }
    await page.keyboard.press('Tab');
    await page.waitForTimeout(150);
    const after = await page.evaluate((sel) => ({
      inside: !!document.activeElement?.closest?.(sel),
      where: document.activeElement?.id
        ? '#' + document.activeElement.id
        : (document.activeElement?.className ? '.' + String(document.activeElement.className).split(' ')[0] : document.activeElement?.tagName?.toLowerCase() ?? '(none)'),
    }), selector);
    add(`2.1.2 no trap: ${label}`, after.inside ? 'PASS' : 'FAIL',
      'Tab from the last control keeps focus inside the surface',
      after.inside ? `focus stayed inside (${r.total} controls)` : `focus escaped to ${after.where}`);
  } finally {
    await ctx.close();
  }
}

await trapCase('new-project modal', {}, async (page) => { await page.click('#new-project-btn'); }, '.modal-overlay');
await trapCase('template picker', {}, async (page) => { await page.keyboard.press('Control+Shift+T'); }, '.tp-overlay');
await trapCase('shortcuts overlay', {}, async (page) => { await page.click('#shortcuts-btn'); }, '#shortcuts-overlay:not([hidden])');
await trapCase('onboarding tour', { onboarded: false }, async () => {}, '.ob-overlay');

// Terminal (xterm). Documented escape path: Esc (native <dialog> cancel ->
// closeTerminal) or the ✕ .term-close button. Tab is captured by xterm itself
// but the dialog still closes via Esc, so there is no trap.
{
  const { ctx, page } = await newPage({ terminal: true });
  try {
    await bootPage(page);
    await page.keyboard.press('Control+`');
    const dlg = await page.waitForSelector('dialog.term-dialog[open]', { timeout: 8000 }).catch(() => null);
    if (!dlg) {
      add('2.1.2 no trap: terminal (xterm)', 'INCONCLUSIVE',
        'Esc closes the terminal dialog and leaves no focus trap',
        'terminal did not open (xterm CDN load may be blocked in this environment)');
    } else {
      const shape = await page.evaluate(() => ({
        closeBtn: !!document.querySelector('.term-close'),
        hint: document.querySelector('.term-hint')?.textContent?.trim() ?? '',
        focusedInside: !!document.activeElement?.closest?.('dialog'),
      }));
      await page.keyboard.press('Escape');
      await page.waitForTimeout(400);
      const gone = await page.evaluate(() => !document.querySelector('dialog.term-dialog'));
      const focusBack = await page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName || '(none)');
      add('2.1.2 no trap: terminal (xterm)', gone ? 'PASS' : 'FAIL',
        'Esc closes the terminal dialog; keyboard user leaves via Esc or the ✕ button',
        `closeBtn=${shape.closeBtn} hint="${shape.hint}" focusInDialog=${shape.focusedInside} closedByEsc=${gone} focusNow=${focusBack}`);
    }
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 2.1.4 — single-character shortcuts
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    // In a text field: "?" and "/" must type, not fire.
    await page.focus('#composer-input');
    await page.keyboard.type('?');
    const typed = await page.evaluate(() => document.getElementById('composer-input').value);
    const overlayFromTyping = await page.evaluate(() => !document.getElementById('shortcuts-overlay').hidden);
    await page.evaluate(() => { document.getElementById('composer-input').value = ''; });
    // Outside a text field: "?" must open the overlay, "/" must focus search.
    await page.evaluate(() => document.getElementById('composer-input').blur());
    await page.keyboard.press('?');
    const overlayOpen = await page.evaluate(() => !document.getElementById('shortcuts-overlay').hidden);
    await page.keyboard.press('?');
    await page.keyboard.press('/');
    const searchFocused = await page.evaluate(() => document.activeElement?.id === 'conv-search');
    const ok = typed.includes('?') && !overlayFromTyping && overlayOpen && searchFocused;
    add('2.1.4 single-char shortcuts inert in text fields, active outside', ok ? 'PASS' : 'FAIL',
      '"?" and "/" type normally in the composer but fire when focus is outside any text field',
      `typedInComposer="${typed}" overlayFiredWhileTyping=${overlayFromTyping} overlayOpensOutside=${overlayOpen} slashFocusesSearch=${searchFocused}`);
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 2.2.2 — honeycomb animation under prefers-reduced-motion
// ===========================================================================
{
  // Normal motion: honeycomb.js animates on load and on activity, then settles
  // to a still frame after 5 s idle — when settled, NO rAF callbacks are
  // scheduled (honeycomb.js cancelAnimationFrame), so the instrumented count
  // must be flat. Reduced motion: the loop never starts.
  async function measureSettle(reduced) {
    const ctx = await browser.newContext({
      viewport: { width: 600, height: 400 },
      reducedMotion: reduced ? 'reduce' : 'no-preference',
    });
    const page = await ctx.newPage();
    await ctx.addInitScript(() => {
      try { localStorage.setItem('gca_onboarded', '1'); } catch {}
      window.__rafCount = 0;
      const orig = window.requestAnimationFrame;
      window.requestAnimationFrame = function (cb) {
        window.__rafCount += 1;
        return orig.call(this, cb);
      };
    });
    await page.goto(URL, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1000);
    // Headless Chromium often reports document.hasFocus() === false; honeycomb's
    // own focus listener flips its hasFocus flag on.
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.waitForTimeout(500);
    const t1 = await page.evaluate(() => window.__rafCount);
    await page.waitForTimeout(600);
    const t2 = await page.evaluate(() => window.__rafCount);

    if (reduced) {
      await ctx.close();
      return { grew: t2 > t1, counts: { t1, t2 }, settledFlat: null, resumed: null, resettledFlat: null };
    }

    // Wait out the 5 s settle (the focus event above restarted the timer), then
    // prove the loop is OFF: the rAF count must not move over another window.
    await page.waitForTimeout(5200);
    const s1 = await page.evaluate(() => window.__rafCount);
    await page.waitForTimeout(700);
    const s2 = await page.evaluate(() => window.__rafCount);

    // Activity restarts it: a synthetic pointermove is user activity.
    await page.evaluate(() => {
      window.dispatchEvent(new PointerEvent('pointermove', { clientX: 10, clientY: 10, bubbles: true }));
    });
    await page.waitForTimeout(500);
    const r1 = await page.evaluate(() => window.__rafCount);
    await page.waitForTimeout(600);
    const r2 = await page.evaluate(() => window.__rafCount);

    // And it settles again 5 s later.
    await page.waitForTimeout(5200);
    const q1 = await page.evaluate(() => window.__rafCount);
    await page.waitForTimeout(700);
    const q2 = await page.evaluate(() => window.__rafCount);

    await ctx.close();
    return {
      grew: t2 > t1,
      settledFlat: s1 === s2,
      resumed: r2 > r1,
      resettledFlat: q1 === q2,
      counts: { t1, t2, s1, s2, r1, r2, q1, q2 },
    };
  }
  const normal = await measureSettle(false);
  const reduced = await measureSettle(true);
  const ok = normal.grew && normal.settledFlat && normal.resumed && normal.resettledFlat
             && !reduced.grew;
  add('2.2.2 honeycomb settles after 5 s idle and restarts on activity', ok ? 'PASS' : 'FAIL',
    'normal motion: rAF grows while active, stays flat after 5 s idle, grows again on activity, flat after a second settle; reduced motion: never grows',
    `normal grew=${normal.grew} settledFlat=${normal.settledFlat} resumed=${normal.resumed} resettledFlat=${normal.resettledFlat} counts=${JSON.stringify(normal.counts)}; reduced grew=${reduced.grew} counts=${JSON.stringify(reduced.counts)}`);
}

// ===========================================================================
// 2.4.3 — focus returns to the trigger when each overlay closes
// ===========================================================================
async function focusReturnCase(label, seed, openTrigger, closeAction, triggerSel, surfaceSel) {
  const { ctx, page } = await newPage(seed);
  try {
    await bootPage(page);
    const trig = await page.$(triggerSel);
    if (!trig) {
      add(`2.4.3 focus returns: ${label}`, 'INCONCLUSIVE', `focus is back on ${triggerSel} after close`, `trigger ${triggerSel} not found`);
      return;
    }
    await openTrigger(page, trig);
    await page.waitForTimeout(300);
    // Move focus INTO the surface; 2.4.3 is only meaningful if the surface
    // actually took focus away from the trigger.
    await page.evaluate(({ sel, foc }) => {
      const root = document.querySelector(sel);
      if (!root) return;
      const els = [...root.querySelectorAll(foc)]
        .filter((el) => el.getClientRects().length > 0 && !el.disabled && !el.closest('[hidden]'));
      if (els.length) els[0].focus();
    }, { sel: surfaceSel, foc: FOCUSABLE });
    await page.waitForTimeout(150);
    await closeAction(page);
    await page.waitForTimeout(400);
    const back = await page.evaluate((sel) => {
      const el = document.activeElement;
      return !!el && el.matches(sel);
    }, triggerSel);
    const where = await page.evaluate(() => document.activeElement?.id ? '#' + document.activeElement.id : (document.activeElement?.className ? '.' + String(document.activeElement.className).split(' ')[0] : document.activeElement?.tagName || '(none)'));
    add(`2.4.3 focus returns: ${label}`, back ? 'PASS' : 'FAIL',
      `focus is back on ${triggerSel} after close`,
      back ? 'restored' : `focus is on ${where}`);
  } finally {
    await ctx.close();
  }
}

await focusReturnCase('shortcuts overlay', {}, async (page, trig) => { await trig.click(); await page.waitForSelector('#shortcuts-overlay:not([hidden])', { state: 'visible', timeout: 5000 }); }, async (page) => { await page.keyboard.press('Escape'); }, '#shortcuts-btn', '#shortcuts-overlay:not([hidden])');
await focusReturnCase('new-project modal', {}, async (page, trig) => { await trig.click(); await page.waitForSelector('.modal-overlay', { state: 'visible', timeout: 5000 }); }, async (page) => { await page.keyboard.press('Escape'); }, '#new-project-btn', '.modal-overlay');
await focusReturnCase('template picker', {}, async (page, trig) => { await trig.click(); await page.waitForSelector('.tp-overlay', { state: 'visible', timeout: 5000 }); }, async (page) => { await page.keyboard.press('Escape'); }, '#template-btn', '.tp-overlay');
await focusReturnCase('settings drawer', {}, async (page, trig) => { await trig.click(); await page.waitForSelector('.settings-drawer.drawer-open', { state: 'visible', timeout: 5000 }); }, async (page) => { await page.keyboard.press('Escape'); }, '#settings-btn', '.settings-drawer.drawer-open');

// Onboarding has no trigger button (auto-shown on first visit): assert that
// dismissing it leaves focus in a usable place (the composer) rather than body.
{
  const { ctx, page } = await newPage({ onboarded: false });
  try {
    await bootPage(page);
    const present = await page.$('.ob-overlay');
    if (!present) {
      add('2.4.3 focus returns: onboarding tour', 'INCONCLUSIVE', 'focus lands in the app after dismissal', 'tour did not render');
    } else {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(400);
      const where = await page.evaluate(() => document.activeElement?.id || document.activeElement?.tagName || '(none)');
      add('2.4.3 focus returns: onboarding tour', where === 'composer-input' ? 'PASS' : 'FAIL',
        'dismissing the tour leaves focus in the app (composer)', `focus is on ${where}`);
    }
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 2.4.7 + 1.4.11 — visible focus indicator, measured in pixels
// ===========================================================================
{
  const { ctx, page } = await newPage({ sbOpen: true });
  try {
    await bootPage(page);
    await page.addScriptTag({ content: `
      window.__foc = {
        parse(s){ const m=(s||'').match(/rgba?\\(([^)]+)\\)/); if(!m) return null;
          const p=m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
          return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}; },
        lin(c){ c/=255; return c<=0.03928? c/12.92 : Math.pow((c+0.055)/1.055,2.4); },
        lum(c){ return 0.2126*this.lin(c.r)+0.7152*this.lin(c.g)+0.0722*this.lin(c.b); },
        ratio(a,b){ const L1=this.lum(a), L2=this.lum(b); return (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05); },
        avg(d){ let r=0,g=0,b=0,n=0; for(let i=0;i<d.length;i+=4){r+=d[i];g+=d[i+1];b+=d[i+2];n++;} return n?{r:r/n,g:g/n,b:b/n}:{r:0,g:0,b:0}; },
        diff(da,db,rect,pad){ const W=rect.w, H=rect.h;
          const out=[];
          for(let y=0;y<H;y++){ for(let x=0;x<W;x++){ const i=(y*W+x)*4;
            const dr=Math.abs(da[i]-db[i]), dg=Math.abs(da[i+1]-db[i+1]), db2=Math.abs(da[i+2]-db[i+2]);
            const dist = Math.min(x, W-1-x, y, H-1-y);
            if(dr+dg+db2 > 24){ out.push({x,y,dist,inside: dist>=pad}); }
          } }
          return out; },
      };
    ` });
    // Freeze CSS animations + stop the honeycomb rAF by telling it the window blurred.
    await page.evaluate(() => {
      const st = document.createElement('style');
      st.textContent = '*,*::before,*::after{transition:none!important;animation-play-state:paused!important;caret-color:transparent!important}';
      document.head.appendChild(st);
      window.dispatchEvent(new Event('blur'));
    });
    const controls = await page.evaluate((foc) => {
      return [...document.querySelectorAll(foc)].filter((el) => {
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
        // A unique handle so focusing the exact element that was measured does
        // not depend on selector uniqueness (two .project-name spans, etc.).
        el.dataset.a11yIdx = String(idx);
        return { tag: el.tagName.toLowerCase(), id: el.id || null, sel, idx,
          cls,
          x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
      });
    }, FOCUSABLE);
    if (controls.length < 8) {
      add('2.4.7+1.4.11 focus visible: pixel sweep', 'FAIL',
        'every visible control shows a >=3:1 focus indicator when keyboard-focused',
        `only ${controls.length} controls found — sweep would be vacuous`);
    } else {
      // Clear the boot autofocus so the "before" shot has no focus ring at all.
      await page.evaluate(() => document.activeElement?.blur?.());
      await page.waitForTimeout(80);
      const beforeShot = (await page.screenshot({ type: 'png' })).toString('base64');
      const offenders = [];
      for (const c of controls) {
        const rect = { x: Math.max(0, c.x - 8), y: Math.max(0, c.y - 8), w: c.w + 16, h: c.h + 16 };
        const after = await page.evaluate((idx) => {
          const el = document.querySelector(`[data-a11y-idx="${idx}"]`);
          if (!el) return { ok: false, outline: 'not-found', border: 'not-found' };
          el.focus({ preventScroll: true });
          const fv = el.matches(':focus-visible');
          const cs = getComputedStyle(el);
          return { ok: true, fv, outline: cs.outlineStyle + '/' + cs.outlineWidth + ' ' + cs.outlineColor,
            border: cs.borderTopColor, bg: cs.backgroundColor };
        }, c.idx);
        await page.waitForTimeout(60);
        const afterShot = (await page.screenshot({ type: 'png' })).toString('base64');
        const scored = await page.evaluate(async ({ a, b, rect }) => {
          const load = (s) => new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.src = 'data:image/png;base64,' + s; });
          const [ia, ib] = [await load(a), await load(b)];
          const w = rect.w, h = rect.h;
          const cv = new OffscreenCanvas(w, h);
          const cx = cv.getContext('2d');
          cx.drawImage(ia, rect.x, rect.y, w, h, 0, 0, w, h);
          const da = cx.getImageData(0, 0, w, h).data;
          cx.drawImage(ib, rect.x, rect.y, w, h, 0, 0, w, h);
          const db = cx.getImageData(0, 0, w, h).data;
          let changed = 0; let maskR = 0, maskG = 0, maskB = 0, ringR = 0, ringG = 0, ringB = 0, ringN = 0;
          const inner = 3;
          for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4;
            const dr = Math.abs(da[i] - db[i]), dg = Math.abs(da[i+1] - db[i+1]), db2 = Math.abs(da[i+2] - db[i+2]);
            const inInner = x > inner && y > inner && x < w - inner && y < h - inner;
            if (dr + dg + db2 > 24) {
              changed++; maskR += db[i]; maskG += db[i+1]; maskB += db[i+2];
            } else if (!inInner) {
              ringR += db[i]; ringG += db[i+1]; ringB += db[i+2]; ringN++;
            }
          }
          if (!changed) return { changed: 0, ratio: null, note: 'no pixels changed on focus' };
          if (ringN === 0) return { changed, ratio: null, note: 'no adjacent ring pixels to compare' };
          const mask = { r: maskR / changed, g: maskG / changed, b: maskB / changed };
          const ring = { r: ringR / ringN, g: ringG / ringN, b: ringB / ringN };
          const ratio = window.__foc.ratio(mask, ring);
          return { changed, ratio: +ratio.toFixed(2), note: 'ok' };
        }, { a: beforeShot, b: afterShot, rect });
        await page.evaluate(() => { document.activeElement?.blur?.(); });
        await page.waitForTimeout(40);
        const sel = c.sel;
        if (!scored.changed) {
          offenders.push(`${sel} (no visible change on focus; outline=${after.outline} border=${after.border})`);
        } else if (!(scored.ratio >= 3)) {
          offenders.push(`${sel} (focus-indicator contrast ${scored.ratio}:1 < 3:1, ${scored.changed} px changed)`);
        }
      }
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      add('2.4.7+1.4.11 focus visible: pixel sweep',
        offenders.length === 0 ? 'PASS' : 'FAIL',
        'every visible control shows a >=3:1 focus indicator when keyboard-focused',
        offenders.length === 0
          ? `${controls.length} controls swept, every focus change showed pixels with >=3:1 contrast against the adjacent ring`
          : `${offenders.length} of ${controls.length} controls fail: ${offenders.slice(0, 6).join(' | ')}${offenders.length > 6 ? ' …' : ''}`);
    }
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 1.4.4 — 200% zoom via 720x450 CSS viewport @ deviceScaleFactor 2
// ===========================================================================
{
  const ctx = await browser.newContext({ viewport: { width: 720, height: 450 }, deviceScaleFactor: 2 });
  await ctx.addInitScript(() => { try { localStorage.setItem('gca_onboarded', '1'); localStorage.setItem('gca_sb_open', '0'); } catch {} });
  const page = await ctx.newPage();
  try {
    await stubBoot(page);
    await page.goto(URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#composer-input', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(600);
    const r = await page.evaluate(() => {
      const vw = innerWidth;
      const pageScroll = document.documentElement.scrollWidth;
      const clipped = [];
      for (const el of document.querySelectorAll('body *')) {
        if (el.closest('pre, code, .term-dialog, .console-log')) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) continue;
        const clipsX = cs.overflowX === 'hidden' || cs.overflowX === 'clip';
        const clipsY = cs.overflowY === 'hidden' || cs.overflowY === 'clip';
        if (!clipsX && !clipsY) continue;   // visible overflow is not clipping
        if (cs.textOverflow === 'ellipsis') continue;  // deliberate truncation
        const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
        if (!own) continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) {
          clipped.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0]) +
            ` (scroll ${el.scrollWidth}x${el.scrollHeight} vs client ${el.clientWidth}x${el.clientHeight})`);
        }
      }
      const input = document.getElementById('composer-input');
      const send = document.getElementById('composer-send');
      return {
        vw,
        pageScroll,
        clipped: clipped.slice(0, 12),
        composerW: input ? Math.round(input.getBoundingClientRect().width) : 0,
        composerVisible: input ? input.getBoundingClientRect().width > 120 : false,
        sendVisible: send ? send.getBoundingClientRect().width > 30 : false,
      };
    });
    const ok = r.vw === 720 && r.pageScroll <= 720 && r.clipped.length === 0 && r.composerVisible && r.sendVisible;
    add('1.4.4 200% zoom (720x450@2x): no clipped text, composer usable', ok ? 'PASS' : 'FAIL',
      'innerWidth=720, no page horizontal scroll, no clipped text, composer usable',
      JSON.stringify(r));
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 1.4.10 — reflow at 320 CSS px
// ===========================================================================
{
  const ctx = await browser.newContext({ viewport: { width: 320, height: 800 } });
  await ctx.addInitScript(() => { try { localStorage.setItem('gca_onboarded', '1'); localStorage.setItem('gca_sb_open', '0'); } catch {} });
  const page = await ctx.newPage();
  try {
    await stubBoot(page);
    await page.goto(URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#composer-input', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(600);
    const r = await page.evaluate(() => ({
      vw: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      offenders: [...document.querySelectorAll('body *')]
        .filter((el) => {
          if (el.closest('pre, code, .term-dialog, .console-log, .tp-overlay, .modal-overlay')) return false;
          const cs = getComputedStyle(el);
          if (cs.position === 'fixed' || cs.position === 'absolute') return false;  // off-canvas drawers don't cause page scroll
          const r = el.getBoundingClientRect();
          return r.right > innerWidth + 1 && r.width > 0;
        })
        .slice(0, 8)
        .map((el) => (el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0]) + ` right=${Math.round(el.getBoundingClientRect().right)}`),
    }));
    const ok = r.scrollWidth <= 320;
    add('1.4.10 reflow at 320px: no page-level horizontal scroll', ok ? 'PASS' : 'FAIL',
      'document scrollWidth <= 320 (terminal/code blocks exempt: pre, code, .term-dialog, .console-log)',
      JSON.stringify(r));
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 1.4.12 — WCAG text-spacing overrides
// ===========================================================================
{
  const { ctx, page } = await newPage({ sbOpen: true });
  try {
    await bootPage(page);
    await page.evaluate(() => {
      const st = document.createElement('style');
      st.textContent = `
        * { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }
        p { margin-bottom: 2em !important; }
      `;
      document.head.appendChild(st);
    });
    await page.waitForTimeout(400);
    const r = await page.evaluate(() => {
      const clipped = [];
      for (const el of document.querySelectorAll('body *')) {
        if (el.closest('pre, code, .term-dialog, .console-log, input, textarea, select, .suggest-dropdown, .tp-overlay, .modal-overlay')) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const clipsX = cs.overflowX === 'hidden' || cs.overflowX === 'clip';
        const clipsY = cs.overflowY === 'hidden' || cs.overflowY === 'clip';
        if (!clipsX && !clipsY) continue;   // visible/auto overflow is not clipping
        if (cs.textOverflow === 'ellipsis') continue;  // deliberate truncation
        const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
        if (!own) continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        if (el.scrollHeight > el.clientHeight + 3 || el.scrollWidth > el.clientWidth + 3) {
          clipped.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0]) +
            ` (scroll ${el.scrollWidth}x${el.scrollHeight} vs client ${el.clientWidth}x${el.clientHeight})`);
        }
      }
      return { clipped: clipped.slice(0, 12), pageScroll: document.documentElement.scrollWidth, vw: innerWidth };
    });
    const ok = r.clipped.length === 0;
    add('1.4.12 text spacing: no text clipped under WCAG overrides', ok ? 'PASS' : 'FAIL',
      'with line-height 1.5 / letter-spacing 0.12em / word-spacing 0.16em / p margin 2em injected, no text container clips',
      JSON.stringify(r));
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 1.4.13 — tooltips
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    const host = await page.$('[data-tooltip]');
    if (!host) {
      add('1.4.13 tooltips: Esc-dismissible, hoverable, persistent', 'INCONCLUSIVE',
        'tooltip shows on hover, survives pointer move onto it, and Esc hides it', 'no [data-tooltip] host in the DOM');
    } else {
      const hostBox = await host.boundingBox();
      await page.mouse.move(hostBox.x + hostBox.width / 2, hostBox.y + hostBox.height / 2);
      await page.waitForTimeout(400);
      const visibleAfterHover = await page.evaluate(() => {
        const el = document.querySelector('[data-tooltip]');
        return getComputedStyle(el, '::after').opacity === '1' || !!document.querySelector('.gca-tooltip:not([hidden])');
      });
      // Move the pointer onto the tooltip (it sits BELOW the host).
      const tipBox = await page.evaluate(() => {
        const el = document.querySelector('[data-tooltip]');
        const host = el.getBoundingClientRect();
        const cs = getComputedStyle(el, '::after');
        const js = document.querySelector('.gca-tooltip');
        if (js && getComputedStyle(js).display !== 'none') {
          const r = js.getBoundingClientRect();
          return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height };
        }
        const w = parseFloat(cs.width) || 0, h = parseFloat(cs.height) || 0;
        return { x: host.left + host.width / 2, y: host.bottom + 6 + (h || 20) / 2, w, h };
      });
      if (tipBox.w > 0 || tipBox.h > 0) {
        await page.mouse.move(tipBox.x, tipBox.y);
        await page.waitForTimeout(400);
      }
      const persistent = await page.evaluate(() => {
        const el = document.querySelector('[data-tooltip]');
        return getComputedStyle(el, '::after').opacity === '1' || !!document.querySelector('.gca-tooltip:not([hidden])');
      });
      await page.keyboard.press('Escape');
      await page.waitForTimeout(250);
      const hiddenAfterEsc = await page.evaluate(() => {
        const el = document.querySelector('[data-tooltip]');
        const js = document.querySelector('.gca-tooltip');
        const cssHidden = getComputedStyle(el, '::after').opacity !== '1';
        const jsHidden = !js || getComputedStyle(js).display === 'none' || Number(getComputedStyle(js).opacity) === 0;
        return cssHidden && jsHidden;
      });
      const ok = visibleAfterHover && persistent && hiddenAfterEsc;
      add('1.4.13 tooltips: Esc-dismissible, hoverable, persistent', ok ? 'PASS' : 'FAIL',
        'tooltip shows on hover, persists when the pointer moves onto it, and Esc hides it without moving the pointer',
        `visibleAfterHover=${visibleAfterHover} persistent=${persistent} hiddenAfterEsc=${hiddenAfterEsc}`);
    }
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 4.1.3 — status messages
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    let wsRoute = null;
    await page.routeWebSocket('**/ws/chat/*', (route) => { wsRoute = route; });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#composer-input', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(800);
    // Streaming state: an assistant turn begins -> Thinking… with role=status.
    if (wsRoute) {
      await wsRoute.send(JSON.stringify({ type: 'text', text: 'streaming probe' }));
      await page.waitForTimeout(300);
    }
    const streaming = await page.evaluate(() => {
      const meta = document.querySelector('.msg-assistant .msg-meta[role="status"]');
      return { text: meta?.textContent ?? '', role: meta?.getAttribute('role') ?? null };
    });
    // Connection lost: close the mocked socket -> _disconnected -> status line.
    const statusElLive = await page.evaluate(() => {
      const el = document.getElementById('chat-status');
      return { role: el?.getAttribute('role') ?? null, live: el?.getAttribute('aria-live') ?? null };
    });
    // Toast: make the search endpoint fail, then type a search.
    await page.route('**/api/search*', (r) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"probe failure"}' }));
    await page.fill('#conv-search', 'probe');
    await page.waitForTimeout(700);
    const toast = await page.evaluate(() => {
      const t = document.querySelector('.error-toast');
      return t ? { role: t.getAttribute('role') ?? null, live: t.getAttribute('aria-live') ?? null, text: t.textContent.slice(0, 60) } : null;
    });
    const okStreaming = streaming.text.includes('Thinking') && (streaming.role === 'status');
    const okStatusLine = statusElLive.role === 'status' || statusElLive.live === 'polite';
    const okToast = toast && (toast.role === 'alert' || toast.role === 'status' || toast.live);
    const ok = okStreaming && okStatusLine && okToast;
    add('4.1.3 status: toasts, streaming, status line exposed via role/status/alert/aria-live', ok ? 'PASS' : 'FAIL',
      'streaming badge role=status; #chat-status role=status/aria-live; .error-toast role=alert/status/aria-live',
      `streaming=${JSON.stringify(streaming)} statusLine=${JSON.stringify(statusElLive)} toast=${JSON.stringify(toast)}`);
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 3.3.4 — delete asks for confirmation (route-driven, no real DELETE)
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    let deleteCalls = 0;
    await page.route('**/api/conversations/c1', async (r) => {
      if (r.request().method() === 'DELETE') {
        deleteCalls += 1;
        await r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      } else {
        await r.continue();
      }
    });
    const del = await page.$('.conv-delete');
    if (!del) {
      add('3.3.4 delete asks for confirmation', 'INCONCLUSIVE', 'clicking delete shows a confirm dialog before any DELETE', 'no .conv-delete control rendered');
    } else {
      await del.click();
      await page.waitForTimeout(300);
      const confirmShown = await page.evaluate(() => {
        const msg = document.querySelector('.modal-confirm-msg');
        return !!msg && /Delete/i.test(msg.textContent || '');
      });
      if (!confirmShown) {
        add('3.3.4 delete asks for confirmation', 'FAIL',
          'clicking delete shows a confirm dialog before any DELETE',
          `no confirmation dialog appeared before delete (deleteCalls=${deleteCalls})`);
      } else {
        const beforeCancel = deleteCalls;
        await page.click('.modal-cancel');
        await page.waitForTimeout(200);
        const cancelledNoDelete = deleteCalls === beforeCancel;
        // Now confirm the flow: delete -> confirm -> DELETE fires once.
        await page.click('.conv-delete');
        await page.waitForTimeout(200);
        await page.click('.modal-confirm.danger');
        await page.waitForTimeout(500);
        const ok = confirmShown && cancelledNoDelete && deleteCalls === 1;
        add('3.3.4 delete asks for confirmation', ok ? 'PASS' : 'FAIL',
          'clicking delete shows a confirm dialog; cancel sends no DELETE; confirm sends exactly one',
          `confirmShown=${confirmShown} cancelledNoDelete=${cancelledNoDelete} deleteCalls=${deleteCalls}`);
      }
    }
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// 2.4.2 + 3.1.1 + 2.4.1 — title, lang, skip link / landmarks
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    const r = await page.evaluate(() => ({
      title: document.title,
      lang: document.documentElement.lang,
      skipLink: !!document.querySelector('a[href="#main-content"], a[href="#chat-area"], .skip-link'),
      main: !!document.querySelector('main, [role="main"]'),
      nav: !!document.querySelector('nav, [role="navigation"]'),
      h1: !!document.querySelector('h1'),
    }));
    const ok = r.title.length > 0 && r.lang === 'en' && r.skipLink && r.main && r.nav && r.h1;
    add('2.4.2+3.1.1+2.4.1 basics: title, lang, skip link, landmarks, h1', ok ? 'PASS' : 'FAIL',
      '<title> non-empty; <html lang="en">; a skip link; main + nav landmarks; an h1',
      JSON.stringify(r));
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// P2-L A-dialog — every .modal-box exposes dialog role, modality, and a name
// ===========================================================================
{
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    // 1) delete confirmation (showConfirm — no title, named by aria-label).
    const del = await page.$('.conv-delete');
    if (!del) {
      add('A-dialog: modal boxes are named dialogs', 'INCONCLUSIVE',
        'delete-confirm and a second .modal-box both expose role=dialog, aria-modal=true, and a non-empty accessible name',
        'no .conv-delete control rendered');
    } else {
      await del.click();
      await page.waitForSelector('.modal-confirm-msg', { state: 'visible', timeout: 5000 }).catch(() => {});
      const delShape = await page.evaluate(() => {
        const box = document.querySelector('.modal-confirm-msg')?.closest('.modal-box') ?? null;
        return box
          ? { role: box.getAttribute('role'), ariaModal: box.getAttribute('aria-modal'), label: box.getAttribute('aria-label') }
          : null;
      });
      const delNamed = await page.getByRole('dialog', { name: /Delete/ }).count().catch(() => 0);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);

      // 2) new-project modal (showModal — named by h3#modal-title via aria-labelledby).
      await page.click('#new-project-btn');
      await page.waitForSelector('.modal-box', { state: 'visible', timeout: 5000 }).catch(() => {});
      const projShape = await page.evaluate(() => {
        const box = document.querySelector('.modal-box');
        return box
          ? { role: box.getAttribute('role'), ariaModal: box.getAttribute('aria-modal'), labelledby: box.getAttribute('aria-labelledby') }
          : null;
      });
      const projNamed = await page.getByRole('dialog', { name: /New Project/ }).count().catch(() => 0);
      const ok = delShape?.role === 'dialog' && delShape?.ariaModal === 'true' && delNamed > 0
              && projShape?.role === 'dialog' && projShape?.ariaModal === 'true' && projNamed > 0;
      add('A-dialog: modal boxes are named dialogs', ok ? 'PASS' : 'FAIL',
        'delete-confirm and a second .modal-box both expose role=dialog, aria-modal=true, and a non-empty accessible name',
        `delete=${JSON.stringify(delShape)} deleteDialogs=${delNamed} project=${JSON.stringify(projShape)} projectDialogs=${projNamed}`);
    }
  } finally {
    await ctx.close();
  }
}

// ===========================================================================
// P2-L A-reply — finished replies are announced once in a dedicated live region
// ===========================================================================
async function replyCase(label, streamText, expectText) {
  const { ctx, page } = await newPage();
  try {
    await bootPage(page);
    // Stub the first-turn auto-title POST (composer.js:477-485 fires it on the
    // stubbed done frame) so this read-only harness never writes a real title.
    await page.route('**/api/conversations/*/auto-title', (r) =>
      r.request().method() === 'POST'
        ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"title":"stub"}' })
        : r.continue());
    let wsRoute = null;
    await page.routeWebSocket('**/ws/chat/*', (route) => { wsRoute = route; });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#composer-input', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(800);
    if (!wsRoute) {
      add(label, 'INCONCLUSIVE', 'live region empty while text frames stream, then the announced text after done',
        'no WebSocket route captured');
      return;
    }
    await wsRoute.send(JSON.stringify({ type: 'text', text: streamText }));
    await page.waitForTimeout(300);
    const during = await page.evaluate(() => document.getElementById('reply-announce')?.textContent ?? null);
    await wsRoute.send(JSON.stringify({ type: 'done' }));
    await page.waitForTimeout(400);
    const after = await page.evaluate(() => document.getElementById('reply-announce')?.textContent ?? null);
    const ok = during === '' && after === expectText;
    add(label, ok ? 'PASS' : 'FAIL',
      `live region empty while only text frames arrive; equals ${JSON.stringify(expectText)} after done`,
      `during=${JSON.stringify(during)} after=${JSON.stringify(after)}`);
  } finally {
    await ctx.close();
  }
}

await replyCase('A-reply-short: reply announced once on done, silent while streaming', 'pong', 'Claude replied: pong');
{
  const longText = 'x'.repeat(400);
  await replyCase('A-reply-long: long reply truncated to 300 chars + "… reply continues"', longText,
    `Claude replied: ${longText.slice(0, 300)}… reply continues`);
}

// ===========================================================================
await browser.close();

console.log('\n=== a11y-check ===\n');
for (const r of results) {
  console.log(`${r.state.padEnd(13)} ${r.label}`);
  console.log(`              expected: ${r.expect}`);
  console.log(`              ${r.detail}`);
  console.log();
}

if (results.length === 0) {
  console.log('RESULT: FAIL — no case executed; this run asserted nothing.');
  process.exit(1);
}

const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);

if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length} case(s) not exercised`); process.exit(2); }
console.log('\nRESULT: PASS');
process.exit(0);
