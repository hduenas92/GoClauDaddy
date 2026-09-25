/**
 * error-visibility-check.mjs — P2-B (WRITE job): make failure paths visible.
 *
 * Cases:
 *   E1  a non-JSON stdout line reaches the chat as a NON-FATAL notice, the
 *       stream continues, and the garbage never enters the reply bubble
 *   E2  a WebSocket refused at boot shows a visible "connection failed" message
 *       with a Retry control, and a successful Retry mounts the composer with
 *       no page reload and no unhandled promise rejection
 *   E3  each of the five right_sidebar.js fetches checks r.ok; on failure its
 *       section shows an explicit unavailable state (never `undefined`/`NaN`)
 *       and the next successful poll recovers it
 *   E4  the SERVER panel shows an explicit offline state when /api/server/info
 *       fails instead of sticking on "Connecting…", and recovers
 *   E5  when the context window for the current model is unknown, CTX shows
 *       "—" instead of a number computed against a guessed 200k default
 *   E6  a disk-full upload error is worded as disk-full and does NOT suggest
 *       "a smaller file"; a size error still does
 *
 * METHOD. Same shape as error-surface-check.mjs: every case gets its own
 * browser context, REST is stub-routed so nothing is written to the real
 * database, and WebSocket behaviour is driven with page.routeWebSocket
 * (Playwright 1.63.0, where routeWebSocket exists — verified at runtime).
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE (a path could not be exercised)
 */
import { chromium } from 'playwright';

const APP_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

const results = [];
const pass = (name, detail) => results.push({ name, state: 'PASS', detail });
const fail = (name, detail) => results.push({ name, state: 'FAIL', detail });
const skip = (name, detail) => results.push({ name, state: 'INCONCLUSIVE', detail });

const browser = await chromium.launch();

async function freshPage({ teams = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(({ teams }) => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_feat_assess', '0');
      localStorage.setItem('gca_sb_open', '1');
      localStorage.setItem('gca_cost_notified', new Date().toISOString().slice(0, 10));
      if (teams) localStorage.setItem('gca_feat_teams', '1');
      else localStorage.removeItem('gca_feat_teams');
    } catch {}
    window.__unhandled = [];
    window.addEventListener('unhandledrejection', (e) => {
      window.__unhandled.push(String((e.reason && e.reason.message) || e.reason));
    });
  }, { teams });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  return { ctx, page, pageErrors };
}

async function unhandled(page) {
  return page.evaluate(() => window.__unhandled || []);
}

// ---------------------------------------------------------------------------
// Hermetic boot stubs: one fake conversation, no projects, no templates.
// POST /api/conversations is intercepted so even a zero-conversation boot
// cannot write a row to the real database.
// ---------------------------------------------------------------------------
const CONV = {
  id: 'c1', project_id: null, name: 'Stub Chat', session_id: null,
  model: 'claude-sonnet-4-6', permission_mode: null, system_prompt: null,
  thinking_budget: null, max_tokens: null, status: 'idle', source: 'web',
  created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:00:00Z',
  started_at: null, completed_at: null,
};

const MSG_WITH_CTX = {
  id: 'm1', conversation_id: 'c1', role: 'assistant', content: 'stub reply',
  thinking: null, input_tokens: 150000, output_tokens: 12,
  model: 'claude-sonnet-4-6', cache_read_tokens: 0, cache_creation_tokens: 0,
  seq: 1, created_at: '2026-09-25T00:00:00Z', tool_calls: null, stopped: false,
};

const CONFIG_BODY = {
  models: [
    { value: 'claude-sonnet-4-6', label: 'Sonnet 4.6', context_window: 1000000, input_rate: 3, output_rate: 15 },
    { value: 'claude-opus-4-5', label: 'Opus 4.5', context_window: 1000000, input_rate: 5, output_rate: 25 },
    { value: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5', context_window: 200000, input_rate: 1, output_rate: 5 },
  ],
  default_model: 'claude-sonnet-4-6',
  permission_modes: ['auto'],
};

async function stubBoot(page, { messages = [] } = {}) {
  await page.route('**/api/projects', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : r.continue());
  await page.route('**/api/flow-templates', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : r.continue());
  await page.route('**/api/conversations', (r) => {
    const m = r.request().method();
    if (m === 'GET') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CONV]) });
    if (m === 'POST') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONV) });
    return r.continue();
  });
  await page.route('**/api/conversations/c1', (r) => {
    const m = r.request().method();
    if (m === 'GET') {
      return r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ conversation: CONV, messages, model_correction: null }),
      });
    }
    return r.continue();
  });
  await page.route('**/api/conversations/c1/stats', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ step_count: 0, tool_call_count: 0 }) }));
}

// ---------------------------------------------------------------------------
// E1 — non-JSON stdout becomes a visible NON-FATAL notice
// ---------------------------------------------------------------------------
{
  const name = 'E1. non-JSON stdout line shows a non-fatal notice and never enters the reply';
  const { ctx, page, pageErrors } = await freshPage();
  try {
    await stubBoot(page);
    let wsRoute = null;
    let routeFired = 0;
    await page.routeWebSocket('**/ws/chat/*', (route) => {
      routeFired += 1;
      wsRoute = route; // mocking mode: Playwright opens the page socket for us
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    const input = await page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
    if (!input) {
      skip(name, 'composer never mounted — cannot inject WS frames');
    } else if (!wsRoute) {
      skip(name, 'WebSocket route never intercepted — cannot inject WS frames');
    } else {
      await wsRoute.send(JSON.stringify({ type: 'notice', text: 'BANNER-GARBAGE-42' }));
      await wsRoute.send(JSON.stringify({ type: 'text', text: 'real reply' }));
      await wsRoute.send(JSON.stringify({ type: 'done' }));
      await page.waitForSelector('.chat-notice', { timeout: 4000 }).catch(() => {});
      const s = await page.evaluate(() => {
        const notice = document.querySelector('.chat-notice')?.textContent ?? '';
        const bubble = [...document.querySelectorAll('.msg-assistant .text-block')]
          .map((e) => e.textContent).join('\n');
        return {
          notice,
          bubble,
          sendHidden: document.getElementById('composer-send')?.hidden ?? null,
          stopHidden: document.getElementById('composer-stop')?.hidden ?? null,
          status: document.getElementById('chat-status')?.textContent ?? '',
        };
      });
      const rejects = await unhandled(page);
      const ok = s.notice.includes('BANNER-GARBAGE-42')
        && s.bubble.includes('real reply')
        && !s.bubble.includes('BANNER-GARBAGE-42')
        && s.sendHidden === false
        && s.stopHidden === true
        && !/error/i.test(s.status)
        && rejects.length === 0
        && pageErrors.length === 0;
      if (!ok) {
        fail(name, JSON.stringify({ s, rejects, pageErrors }).slice(0, 400));
      } else {
        pass(name, `notice shown, reply bubble clean, composer restored (routeFired=${routeFired})`);
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// E2 — WebSocket refused at boot
// ---------------------------------------------------------------------------
{
  const name = 'E2. refused WebSocket at boot shows connection-failed message + working Retry';
  const { ctx, page, pageErrors } = await freshPage();
  try {
    await stubBoot(page);
    let refuse = true;
    let routeFired = 0;
    await page.routeWebSocket('**/ws/chat/*', async (route) => {
      routeFired += 1;
      if (refuse) await route.close();
      // else: mocking mode auto-opens the socket in the page
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    const s1 = await page.evaluate(() => ({
      errText: document.getElementById('chat-connection-error')?.innerText?.replace(/\s+/g, ' ').trim() ?? '',
      retry: !!document.getElementById('chat-retry-btn'),
      composer: !!document.getElementById('composer-input'),
    }));
    const rejects = await unhandled(page);
    if (routeFired === 0) {
      skip(name, 'the page never attempted a WebSocket — refusal path not exercised');
    } else if (!s1.errText && !s1.retry) {
      fail(name, `no connection-failed message or Retry after WS refusal: ${JSON.stringify({ s1, rejects, pageErrors })}`);
    } else if (rejects.length > 0 || pageErrors.length > 0) {
      fail(name, `visible UI appeared but an unhandled rejection/pageerror leaked: ${JSON.stringify({ rejects, pageErrors })}`);
    } else {
      refuse = false;
      await page.evaluate(() => { window.__marker = 42; });
      await page.click('#chat-retry-btn');
      const composer = await page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
      const s2 = await page.evaluate(() => ({
        marker: window.__marker,
        errGone: !document.getElementById('chat-connection-error'),
      }));
      const ok = composer && s2.marker === 42 && s2.errGone;
      if (!ok) {
        fail(name, `retry did not mount a working composer without reload: ${JSON.stringify({ composer: !!composer, s2 })}`);
      } else {
        pass(name, 'message + Retry shown; Retry mounted the composer without a page reload');
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// E5 + E3(config) — /api/config failure: CTX must show "—", then recover
// ---------------------------------------------------------------------------
{
  const name = 'E5+E3. /api/config failure shows CTX "—" (no guessed 200k) and the next poll recovers';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page, { messages: [MSG_WITH_CTX] });
    let configCalls = 0;
    await page.route('**/api/config', (r) => {
      configCalls += 1;
      if (configCalls === 1) {
        return r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' });
      }
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONFIG_BODY) });
    });
    await page.clock.install();
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#met-ctx', { timeout: 8000 });
    await page.waitForTimeout(400);
    // setModel() (main.js) resets the metrics AFTER the store subscription has
    // already computed CTX once, so push one more state change to make the
    // sidebar recompute CTX against whatever ctxMax it currently holds. That
    // is what turns the red run into the wrong 200k number.
    await page.evaluate(async () => {
      const m = await import('/static/js/state/store.js');
      const s = m.getState();
      m.setState({ messages: [...(s.messages || [])] });
    });
    await page.waitForTimeout(200);
    const before = await page.evaluate(() => document.getElementById('met-ctx')?.textContent ?? '');
    if (before !== '—') {
      fail(name, `CTX on config failure must be "—", got "${before}" (defect: number computed against guessed 200k)`);
    } else {
      await page.clock.fastForward(60_000); // next fallback poll
      await page.waitForTimeout(600);
      const after = await page.evaluate(() => document.getElementById('met-ctx')?.textContent ?? '');
      const ok = after.includes('%') && !/undefined|NaN/.test(after);
      if (!ok) fail(name, `CTX did not recover after the next successful config poll: "${after}" (configCalls=${configCalls})`);
      else pass(name, `config failure -> "—"; recovered to "${after}" on the next poll (configCalls=${configCalls})`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// E4 — SERVER panel offline state
// ---------------------------------------------------------------------------
{
  const name = 'E4. /api/server/info failure shows an explicit offline state and recovers';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    let infoCalls = 0;
    await page.route('**/api/server/info', (r) => {
      infoCalls += 1;
      if (infoCalls === 1) {
        return r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' });
      }
      return r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ url: 'http://127.0.0.1:8765', host: '127.0.0.1', port: 8765 }),
      });
    });
    await page.clock.install();
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#rsb-server-body', { timeout: 8000 });
    await page.waitForTimeout(400);
    const before = await page.evaluate(() => document.getElementById('rsb-server-body')?.innerText?.replace(/\s+/g, ' ').trim() ?? '');
    if (!/offline|unreachable/i.test(before)) {
      fail(name, `SERVER panel must say offline/unreachable, got "${before}" (defect: stuck on "Connecting…")`);
    } else {
      await page.clock.fastForward(60_000);
      await page.waitForTimeout(600);
      const after = await page.evaluate(() => document.getElementById('rsb-server-body')?.innerText?.replace(/\s+/g, ' ').trim() ?? '');
      const ok = /online/i.test(after) && !/undefined|NaN/.test(after);
      if (!ok) fail(name, `SERVER panel did not recover on the next success: "${after}" (infoCalls=${infoCalls})`);
      else pass(name, `offline state shown, then recovered to "${after}" (infoCalls=${infoCalls})`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// E3(stats) — /api/server/stats failure: "—", never "undefined chats"; recovers
// ---------------------------------------------------------------------------
{
  const name = 'E3. /api/server/stats failure shows "—" (never undefined/NaN) and recovers';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    let statsCalls = 0;
    await page.route('**/api/server/stats', (r) => {
      statsCalls += 1;
      // Two stats fetches happen at boot (mount + setConversation); fail both
      // so the failure state is actually on screen before we sample it.
      if (statsCalls <= 2) {
        return r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' });
      }
      return r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          chat_count: 3, message_count: 5, total_input: 100, total_output: 200,
          monthly_cost_usd: 1.5, monthly_input: 100, monthly_output: 200, budget_usd: 200,
        }),
      });
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#met-all', { timeout: 8000 });
    await page.waitForTimeout(500);
    const before = await page.evaluate(() => ({
      all: document.getElementById('met-all')?.textContent ?? '',
      cost: document.getElementById('met-cost')?.textContent ?? '',
    }));
    const clean = before.all === '—' && before.cost === '—'
      && !/undefined|NaN/.test(before.all + before.cost);
    if (!clean) {
      fail(name, `ALL CHATS/COST must be "—" on failure, got ${JSON.stringify(before)} (defect: "undefined chats"/NaN)`);
    } else {
      // Turn-end refresh (real product path) is the next successful poll.
      await page.evaluate(async () => {
        const m = await import('/static/js/state/store.js');
        m.setState({ streaming: true });
      });
      await page.waitForTimeout(200);
      await page.evaluate(async () => {
        const m = await import('/static/js/state/store.js');
        m.setState({ streaming: false });
      });
      await page.waitForTimeout(800);
      const after = await page.evaluate(() => ({
        all: document.getElementById('met-all')?.textContent ?? '',
        cost: document.getElementById('met-cost')?.textContent ?? '',
      }));
      const ok = /3 chats/.test(after.all) && /\$/.test(after.cost) && !/undefined|NaN/.test(after.all + after.cost);
      if (!ok) fail(name, `stats did not recover on the next poll: ${JSON.stringify(after)} (statsCalls=${statsCalls})`);
      else pass(name, `failure -> "—"; recovered to ${JSON.stringify(after)} (statsCalls=${statsCalls})`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// E3(agents) — /api/agents/status failure shows Unavailable, recovers on poll
// ---------------------------------------------------------------------------
{
  const name = 'E3. /api/agents/status failure shows Unavailable and recovers on the next poll';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    let agentsCalls = 0;
    await page.route('**/api/agents/status', (r) => {
      agentsCalls += 1;
      if (agentsCalls === 1) {
        return r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' });
      }
      return r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#rsb-agent-count', { state: 'attached', timeout: 8000 });
    await page.waitForTimeout(500);
    const before = await page.evaluate(() => ({
      count: document.getElementById('rsb-agent-count')?.textContent ?? '',
      body: document.getElementById('rsb-agents-body')?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
    }));
    if (!(before.count === '—' && /unavailable/i.test(before.body) && !/undefined|NaN/.test(before.count + before.body))) {
      fail(name, `agents must show "—"/Unavailable, got ${JSON.stringify(before)} (defect: undefined/NaN)`);
    } else {
      await page.waitForTimeout(2600); // 2s poll
      const after = await page.evaluate(() => ({
        count: document.getElementById('rsb-agent-count')?.textContent ?? '',
        body: document.getElementById('rsb-agents-body')?.innerText?.trim() ?? '',
      }));
      const ok = after.count === '0' && !/undefined|NaN/.test(after.count + after.body);
      if (!ok) fail(name, `agents did not recover on the next poll: ${JSON.stringify(after)} (agentsCalls=${agentsCalls})`);
      else pass(name, `failure -> Unavailable; recovered to count=${after.count} (agentsCalls=${agentsCalls})`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// E3(teams) — /api/teams failure shows Unavailable, recovers on poll
// ---------------------------------------------------------------------------
{
  const name = 'E3. /api/teams failure shows Unavailable and recovers on the next poll';
  const { ctx, page } = await freshPage({ teams: true });
  try {
    await stubBoot(page);
    let teamsCalls = 0;
    await page.route('**/api/teams', (r) => {
      teamsCalls += 1;
      if (teamsCalls === 1) {
        return r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' });
      }
      return r.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#rsb-team-count', { state: 'attached', timeout: 8000 });
    await page.waitForTimeout(500);
    const before = await page.evaluate(() => ({
      count: document.getElementById('rsb-team-count')?.textContent ?? '',
      body: document.getElementById('rsb-teams-body')?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
    }));
    if (!(before.count === '—' && /unavailable/i.test(before.body) && !/undefined|NaN/.test(before.count + before.body))) {
      fail(name, `teams must show "—"/Unavailable, got ${JSON.stringify(before)} (defect: undefined/NaN)`);
    } else {
      await page.waitForTimeout(5600); // 5s poll
      const after = await page.evaluate(() => ({
        count: document.getElementById('rsb-team-count')?.textContent ?? '',
        body: document.getElementById('rsb-teams-body')?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
      }));
      const ok = after.count === '0' && !/undefined|NaN/.test(after.count + after.body);
      if (!ok) fail(name, `teams did not recover on the next poll: ${JSON.stringify(after)} (teamsCalls=${teamsCalls})`);
      else pass(name, `failure -> Unavailable; recovered to count=${after.count} (teamsCalls=${teamsCalls})`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// E6 frontend — disk-full wording vs size wording
// ---------------------------------------------------------------------------
{
  const name = 'E6. disk-full upload error does NOT suggest a smaller file';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    await page.route('**/api/attachments**', (r) =>
      r.request().method() === 'POST'
        ? r.fulfill({ status: 507, contentType: 'application/json', body: '{"detail":"The disk is full — free up some space, then try again."}' })
        : r.continue());
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#composer-input', { timeout: 8000 });
    const input = await page.$('#composer input[type=file], input[type=file]');
    if (!input) {
      skip(name, 'no file input found in the composer');
    } else {
      await input.setInputFiles({ name: 'probe.txt', mimeType: 'text/plain', buffer: Buffer.from('x') });
      await page.waitForSelector('.error-toast', { timeout: 6000 });
      const text = (await page.locator('.error-toast').first().innerText()).replace(/\s+/g, ' ').trim();
      const ok = /disk/i.test(text) && !/smaller file/i.test(text);
      if (!ok) fail(name, `disk-full toast must say disk full and NOT suggest a smaller file: "${text}"`);
      else pass(name, `"${text.slice(0, 100)}"`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'E6. size-limit upload error still suggests a smaller file';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    await page.route('**/api/attachments**', (r) =>
      r.request().method() === 'POST'
        ? r.fulfill({ status: 400, contentType: 'application/json', body: '{"detail":"File exceeds the 25MB limit"}' })
        : r.continue());
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#composer-input', { timeout: 8000 });
    const input = await page.$('#composer input[type=file], input[type=file]');
    if (!input) {
      skip(name, 'no file input found in the composer');
    } else {
      await input.setInputFiles({ name: 'big.txt', mimeType: 'text/plain', buffer: Buffer.from('x') });
      await page.waitForSelector('.error-toast', { timeout: 6000 });
      const text = (await page.locator('.error-toast').first().innerText()).replace(/\s+/g, ' ').trim();
      const ok = /smaller file/i.test(text);
      if (!ok) fail(name, `size-limit toast must still suggest a smaller file: "${text}"`);
      else pass(name, `"${text.slice(0, 100)}"`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

await browser.close();

console.log('\n=== error-visibility-check ===\n');
for (const r of results) {
  console.log(`${r.state.padEnd(13)} ${r.name}`);
  console.log(`              ${r.detail}`);
}

// Non-empty-set guard (§2.2): if nothing ran, this harness proves nothing.
if (results.length === 0) {
  console.log('\nRESULT: FAIL — no case executed; this run asserted nothing.');
  process.exit(1);
}

const failed = results.filter((r) => r.state === 'FAIL');
const skipped = results.filter((r) => r.state === 'INCONCLUSIVE');
const passed = results.filter((r) => r.state === 'PASS');

console.log(`\n${passed.length} passed · ${failed.length} failed · ${skipped.length} inconclusive · ${results.length} case(s) run`);
console.log('\nNOT COVERED, stated rather than silently omitted:');
console.log('  Persistence of the E1 garbage line is asserted in pytest');
console.log('  (backend/tests/test_non_json_notice.py), not here — this harness');
console.log('  only drives the DOM/WS surface.');

if (failed.length) { console.log(`\nRESULT: FAIL — ${failed.length}`); process.exit(1); }
if (skipped.length) { console.log(`\nRESULT: INCONCLUSIVE — ${skipped.length} path(s) not exercised`); process.exit(2); }
console.log('\nRESULT: PASS — every exercised failure path is visible, recovers, and never renders undefined/NaN');
process.exit(0);
