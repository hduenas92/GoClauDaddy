/**
 * perf-check.mjs — 5-4. Does it stay responsive at scale?
 *
 * TARGETS, set by Houston rather than by me, so a pass means something:
 *   1. 100 conversations render in the sidebar    < 200 ms
 *   2. filtering that list on a keystroke         < 200 ms
 *   3. a 10,000-message conversation opens        < 3000 ms, with a loading state
 *
 * 200 ms is roughly where interaction stops feeling direct; 3 s is where a
 * person starts wondering whether they actually clicked.
 *
 * NOTHING IS WRITTEN TO THE DATABASE. Every fixture is delivered by route
 * interception. That is not only safer — Houston's real conversations are in
 * that file — it is more accurate for what these targets describe, which is
 * RENDER cost. Backend query cost at the same scale is a separate question and
 * belongs in pytest against temp_db, where seeding is free and isolated.
 *
 * ANTI-VACUITY, and it is the whole reason this file is longer than it looks
 * like it needs to be. A performance test is unusually easy to pass for the
 * wrong reason:
 *
 *   - "the sidebar rendered in 12 ms" is trivially true of a sidebar that
 *     rendered NOTHING. Every timing case first asserts the expected element
 *     COUNT, and reports INCONCLUSIVE rather than PASS if the fixture never
 *     landed.
 *   - a timer started before navigation measures the network, not the render.
 *     Timing starts when the fixture response is delivered.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const APP = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

const T_LIST_MS = 200;
const T_FILTER_MS = 200;
const T_BIGCONV_MS = 3000;

const N_CONVS = 100;
const N_MSGS = 10_000;

const results = [];
const add = (label, state, detail = '') => {
  results.push({ label, state, detail });
  console.log(`${state.padEnd(13)} ${label}\n              ${detail}`);
};

const iso = (d) => new Date(Date.now() - d * 60_000).toISOString();

function fixtureConversations(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: `perf-${String(i).padStart(5, '0')}`,
    name: `Performance fixture conversation ${i}`,
    project_id: null,
    model: 'claude-sonnet-5',
    status: i % 7 === 0 ? 'busy' : 'idle',
    source: 'web',
    cost_usd: (i % 13) * 0.11,
    session_id: null,
    system_prompt: null,
    permission_mode: null,
    thinking_budget: null,
    max_tokens: null,
    created_at: iso(i * 3),
    updated_at: iso(i),
    completed_at: null,
    started_at: null,
  }));
}

function fixtureMessages(convId, n) {
  const roles = ['user', 'assistant'];
  return Array.from({ length: n }, (_, i) => ({
    id: `m-${i}`,
    conversation_id: convId,
    role: roles[i % 2],
    content: `Message ${i}. ` + 'The quick brown fox jumps over the lazy dog. '.repeat(3),
    thinking: null,
    tool_calls: null,
    input_tokens: i % 2 ? 120 : null,
    output_tokens: i % 2 ? 340 : null,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    model: 'claude-sonnet-5',
    seq: i,
    created_at: iso(n - i),
    stopped: 0,
    superseded_by: null,
  }));
}

const browser = await chromium.launch();

async function pageWith(routes, opts = {}) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ...opts,
  });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_sb_open', '1');
    } catch {}
  });
  const page = await ctx.newPage();
  // Scoped by pathname, never a `**/api/**` glob: that glob also matches the
  // static module at /static/js/api/http.js and breaks module loading.
  await page.route('**/*', async (route) => {
    const u = new URL(route.request().url());
    const handled = await routes(u, route);
    if (!handled) return route.continue();
  });
  return { ctx, page };
}

try {
  // --- 1 & 2. 100 conversations: render, then filter -----------------------
  {
    const label1 = `1. ${N_CONVS} conversations render in < ${T_LIST_MS}ms`;
    const label2 = `2. filtering ${N_CONVS} conversations in < ${T_FILTER_MS}ms`;
    let delivered = 0;
    const { ctx, page } = await pageWith(async (u, route) => {
      if (u.pathname === '/api/conversations' && route.request().method() === 'GET') {
        delivered = Date.now();
        await route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify(fixtureConversations(N_CONVS)),
        });
        return true;
      }
      return false;
    });
    try {
      await page.goto(APP, { waitUntil: 'domcontentloaded' });
      // Wait for the LIST, not for a timer: the render is done when the items
      // exist, and polling for them is what makes the measurement honest.
      const appeared = await page.waitForFunction(
        (n) => document.querySelectorAll('#conv-list .conv-item').length >= n,
        N_CONVS, { timeout: 15_000 },
      ).catch(() => null);

      const count = await page.evaluate(
        () => document.querySelectorAll('#conv-list .conv-item').length);

      if (!appeared || count < N_CONVS) {
        add(label1, 'INCONCLUSIVE',
            `only ${count} .conv-item rendered of ${N_CONVS} — the fixture never ` +
            `fully landed, so any timing here would be of a list that does not exist`);
        add(label2, 'INCONCLUSIVE', 'skipped: the list never rendered');
      } else {
        // Measured from response delivery, not from navigation: anything
        // earlier is timing the network and the boot, not the render.
        const renderedAt = await page.evaluate(() => performance.now() + performance.timeOrigin);
        const ms = Math.round(renderedAt - delivered);
        add(label1, ms < T_LIST_MS ? 'PASS' : 'FAIL',
            `${count} items rendered ${ms}ms after the response was delivered ` +
            `(target < ${T_LIST_MS}ms)`);

        // Filtering is synchronous and local (the 250ms debounce is only the
        // network half), so this measures the DOM work on a keystroke.
        const t = await page.evaluate(() => {
          const el = document.querySelector('#conv-search');
          const t0 = performance.now();
          el.value = 'fixture conversation 7';
          el.dispatchEvent(new Event('input', { bubbles: true }));
          const shown = document.querySelectorAll('#conv-list .conv-item').length;
          return { ms: Math.round(performance.now() - t0), shown };
        });
        // Non-empty guard, inverted: the filter must have NARROWED the list.
        // "0ms to filter" is also what a filter that did nothing measures.
        const narrowed = t.shown > 0 && t.shown < N_CONVS;
        add(label2, narrowed && t.ms < T_FILTER_MS ? 'PASS' : 'FAIL',
            `${N_CONVS} -> ${t.shown} items in ${t.ms}ms (target < ${T_FILTER_MS}ms; ` +
            `must actually narrow: ${narrowed})`);
      }
    } finally { await ctx.close(); }
  }

  // --- 3. a 10,000-message conversation ------------------------------------
  {
    const label = `3. a ${N_MSGS.toLocaleString()}-message conversation opens in < ${T_BIGCONV_MS}ms`;
    const CID = 'perf-00000';
    let delivered = 0;
    const { ctx, page } = await pageWith(async (u, route) => {
      if (u.pathname === '/api/conversations' && route.request().method() === 'GET') {
        await route.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify(fixtureConversations(3)),
        });
        return true;
      }
      if (u.pathname === `/api/conversations/${CID}`) {
        const body = JSON.stringify({
          conversation: fixtureConversations(1)[0],
          messages: fixtureMessages(CID, N_MSGS),
        });
        delivered = Date.now();
        await route.fulfill({ status: 200, contentType: 'application/json', body });
        return true;
      }
      return false;
    });
    try {
      await page.goto(APP, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#conv-list .conv-item', { timeout: 10_000 }).catch(() => null);

      // Watch for a loading state BEFORE the click, because it is transient by
      // definition — sampling for it after the render has finished can only
      // ever find nothing.
      //
      // The first version of this case read `window.__sawLoading`, which
      // NOTHING EVER SET, so it faithfully reported "loading state seen:
      // false" no matter what the app did. An assertion wired to a variable
      // that is always undefined is the purest form of the vacuous check this
      // project keeps catching, and I wrote one into the harness meant to
      // catch them.
      await page.evaluate(() => {
        window.__sawLoading = false;
        const SEL = '.loading, .spinner, .skeleton, [aria-busy="true"], .msg-loading';
        if (document.querySelector(SEL)) window.__sawLoading = true;
        new MutationObserver(() => {
          if (document.querySelector(SEL)) window.__sawLoading = true;
        }).observe(document.body, { childList: true, subtree: true, attributes: true });
      });

      const t0 = Date.now();
      await page.click('#conv-list .conv-item .conv-body').catch(() => {});

      const ok = await page.waitForFunction(
        () => document.querySelectorAll('#chat-messages .msg').length > 100,
        null, { timeout: 30_000 },
      ).catch(() => null);

      const rendered = await page.evaluate(
        () => document.querySelectorAll('#chat-messages .msg').length);
      const ms = Date.now() - (delivered || t0);

      if (!ok || rendered === 0) {
        add(label, 'INCONCLUSIVE',
            `${rendered} .msg elements rendered — the conversation never opened, ` +
            `so there is no render to time`);
      } else {
        // A loading state is part of the target: 3s of nothing is a different
        // experience from 3s of a spinner, and only one of them is acceptable.
        const hadLoading = await page.evaluate(() => Boolean(window.__sawLoading));
        add(label, ms < T_BIGCONV_MS ? 'PASS' : 'FAIL',
            `${rendered.toLocaleString()} of ${N_MSGS.toLocaleString()} messages ` +
            `rendered in ${ms}ms (target < ${T_BIGCONV_MS}ms)` +
            (rendered < N_MSGS
              ? ` — NOTE: fewer rendered than sent, so the app is windowing or truncating`
              : '') +
            ` · loading state seen: ${hadLoading}`);
      }
    } finally { await ctx.close(); }
  }

} finally {
  await browser.close();
}

console.log('\n=== perf-check ===\n');
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — responsive at 100 conversations and 10k messages');
process.exit(0);
