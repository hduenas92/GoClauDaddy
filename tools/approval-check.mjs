/**
 * approval-check.mjs — WCAG 2.2.1 harness for the tool-approval countdown and
 * the "Need more time" extension (P2-H H1).
 *
 * Drives the real approval modal against a MOCKED WebSocket and a faked clock,
 * so nothing reaches the backend and the 60 s countdown takes milliseconds to
 * exercise. Route interception only: the real data dir is never read or written.
 *
 * Cases:
 *   1. the countdown is visible and starts at the server's remaining seconds
 *   2. the "Need more time" button appears once 20 s remain, keyboard-operable
 *   3. pressing it sends {"type":"approval_extend"} over the WS
 *   4. the server's approval_extended reply re-syncs the displayed time upward
 *   5. the 20 s mark is announced once via the polite aria-live region
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const APP_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

const results = [];
const add = (label, state, expect, detail) => results.push({ label, state, expect, detail });

const PROJ = {
  id: 'p1', name: 'Approval Fixture Project', working_dir: null, system_prompt: null,
  created_at: '2026-09-25T00:00:00Z',
};
const CONV = {
  id: 'c1', project_id: null, name: 'Approval Fixture Chat', session_id: null,
  model: 'claude-sonnet-4-6', permission_mode: null, system_prompt: null,
  thinking_budget: null, max_tokens: null, status: 'idle', source: 'web',
  cost_usd: 0.5,
  created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:05:00Z',
  started_at: null, completed_at: null,
};
const MESSAGES = [];
const TEMPLATES = [
  { id: 't1', title: 'Report writer', category: 'report', description: 'A report template', body: 'Write a report about {{topic}}', is_builtin: true },
];

async function stubBoot(page) {
  await page.route('**/*', async (r) => {
    const u = new globalThis.URL(r.request().url());
    const m = r.request().method();
    const p = u.pathname;
    if (p === '/api/projects' && m === 'GET') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([PROJ]) });
    }
    if (p === '/api/flow-templates' && m === 'GET') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TEMPLATES) });
    }
    if (p === '/api/conversations') {
      if (m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CONV]) });
      if (m === 'POST') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONV) });
    }
    if (p === '/api/conversations/c1') {
      if (m === 'GET') {
        return r.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({ conversation: CONV, messages: MESSAGES, model_correction: null }),
        });
      }
    }
    if (p === '/api/conversations/c1/stats') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ step_count: 0, tool_call_count: 0 }) });
    }
    if (p === '/api/config' && m === 'GET') {
      return r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          models: [{ value: 'claude-sonnet-4-6', label: 'Sonnet 4.6', context_window: 1000000, input_rate: 3, output_rate: 15 }],
          default_model: 'claude-sonnet-4-6',
          permission_modes: ['auto', 'acceptEdits', 'plan'],
        }),
      });
    }
    if (p === '/api/server/info') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ url: APP_URL, host: '127.0.0.1', port: 8765 }) });
    }
    if (p === '/api/server/stats') {
      return r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ chat_count: 1, message_count: 0, total_input: 0, total_output: 0, monthly_cost_usd: 0, monthly_input: 0, monthly_output: 0, budget_usd: 200 }),
      });
    }
    return r.continue();
  });
}

const browser = await chromium.launch();

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_sb_open', '0');
      localStorage.setItem('gca_feat_approval', '1');
      localStorage.setItem('gca_cost_notified', new Date().toISOString().slice(0, 10));
    } catch { /* private mode */ }
  });
  const page = await ctx.newPage();
  await page.clock.install({ time: new Date('2026-09-25T12:00:00') });

  let wsRoute = null;
  const pageFrames = [];
  await page.routeWebSocket('**/ws/chat/*', (route) => {
    wsRoute = route;
    route.onMessage((msg) => pageFrames.push(typeof msg === 'string' ? msg : msg.toString()));
  });

  await stubBoot(page);
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#composer-input', { timeout: 10000 }).catch(() => {});

  // Wait for the mocked socket to open (the boot switches to the fixture conv).
  const wsDeadline = Date.now() + 5000;
  while (!wsRoute && Date.now() < wsDeadline) await page.waitForTimeout(50);
  if (!wsRoute) {
    add('2.2.1 approval countdown + Need more time', 'INCONCLUSIVE',
      'the modal shows a live countdown, offers Need more time at 20 s, and re-syncs from the server',
      'the mocked WebSocket never opened');
  } else {
    // --- a server-side approval request arrives --------------------------------
    await wsRoute.send(JSON.stringify({
      type: 'approval_needed', tool: 'Bash', action: 'rm -rf /tmp/probe',
      timeout: 60, remaining: 60,
    }));
    const modal = await page.waitForSelector('#approval-countdown', { state: 'visible', timeout: 5000 }).catch(() => null);
    if (!modal) {
      add('2.2.1 approval countdown + Need more time', 'FAIL',
        'the modal shows a live countdown', 'no #approval-countdown rendered');
    } else {
      const readState = () => page.evaluate(() => ({
        countdown: document.getElementById('approval-countdown')?.textContent?.trim() ?? '',
        btnHidden: document.getElementById('approval-need-time-btn')?.hidden,
        announce: document.getElementById('approval-announce')?.textContent?.trim() ?? '',
        live: document.getElementById('approval-announce')?.getAttribute('aria-live') ?? null,
      }));

      const initial = await readState();
      add('2.2.1 countdown is visible and starts at the server remaining seconds',
        /Time remaining: 60 s/.test(initial.countdown) ? 'PASS' : 'FAIL',
        'the modal shows the server-sent remaining seconds',
        `countdown="${initial.countdown}" btnHidden=${initial.btnHidden}`);

      add('2.2.1 "Need more time" is hidden above 20 s',
        initial.btnHidden === true ? 'PASS' : 'FAIL',
        'the button must not be offered before the 20 s mark',
        `btnHidden=${initial.btnHidden}`);

      // --- run the fake clock forward to the 20 s mark ------------------------
      await page.clock.runFor(41000);
      const at19 = await readState();
      add('2.2.1 "Need more time" appears at 20 s remaining',
        at19.btnHidden === false ? 'PASS' : 'FAIL',
        'the button becomes visible once <= 20 s remain',
        `countdown="${at19.countdown}" btnHidden=${at19.btnHidden}`);
      add('2.2.1 the 20 s mark is announced politely (once, not per second)',
        (at19.live === 'polite' && at19.announce.length > 0) ? 'PASS' : 'FAIL',
        'a polite aria-live region announces the 20 s mark',
        `live=${at19.live} announce="${at19.announce}" countdown="${at19.countdown}"`);

      // --- keyboard reachability: focus + Enter sends the extend frame -------
      await page.focus('#approval-need-time-btn');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(300);
      const sentExtend = pageFrames.some((f) => {
        try { return JSON.parse(f).type === 'approval_extend'; } catch { return false; }
      });
      add('2.2.1 pressing "Need more time" sends approval_extend over the WS',
        sentExtend ? 'PASS' : 'FAIL',
        'activating the button sends {"type":"approval_extend"}',
        `frames from page: ${pageFrames.join(' | ') || '(none)'}`);

      // --- the server replies and the displayed time jumps --------------------
      await wsRoute.send(JSON.stringify({ type: 'approval_extended', timeout: 60, remaining: 60 }));
      await page.waitForTimeout(200);
      const after = await readState();
      add('2.2.1 the server reply re-syncs the displayed time upward',
        (/Time remaining: 60 s/.test(after.countdown) && after.btnHidden === true) ? 'PASS' : 'FAIL',
        'after approval_extended the countdown jumps back to the server-sent remaining seconds',
        `countdown="${after.countdown}" btnHidden=${after.btnHidden}`);
    }
  }

  await ctx.close();
} finally {
  await browser.close();
}

console.log('\n=== approval-check ===\n');
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
