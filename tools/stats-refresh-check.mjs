/**
 * stats-refresh-check.mjs — 4-D5. A finished turn must refresh the stats panel.
 *
 * WHAT WAS WRONG. right_sidebar.js subscribed on `state.lastEventType === "done"`
 * to call _fetchStats(). Nothing in the frontend or the backend has ever written
 * that field — measured: two references in the whole codebase, both reads, zero
 * writes. So the condition was permanently false and the refresh never ran once.
 *
 * It was filed as Tier 4 clutter, i.e. "delete it". Deleting it would have been
 * wrong. The 60s interval beside it calls itself a "fallback poll in case a
 * 'done' event is missed", so with the primary path inert that fallback was the
 * ONLY mechanism: COST and ALL CHATS could be stale for up to a minute after a
 * turn ended. Same shape as v15 in 4-P1 — a primary path that never ran, a
 * fallback quietly carrying the whole load, and a comment asserting otherwise.
 *
 * HOW THIS IS DRIVEN, and why there is no CLI turn. The store is an ES module,
 * so `import()` in the page returns THE SAME singleton the sidebar subscribed
 * to. Setting `streaming` true then false reproduces exactly the transition a
 * real turn produces, with no model call, no tokens and no flakiness. The
 * assertion is on the resulting NETWORK REQUEST, not on rendered text: a number
 * that happens to be unchanged would make a text assertion pass while the
 * refresh never fired.
 *
 * The 60s poll is deliberately far outside the measurement window, so a request
 * seen here can only be the turn-end refresh.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const APP = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const WINDOW_MS = 2500;   // must be << the 60s fallback poll

const results = [];
const add = (label, state, detail = '') => {
  results.push({ label, state, detail });
  console.log(`${state.padEnd(13)} ${label}\n              ${detail}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => {
  try {
    localStorage.setItem('gca_onboarded', '1');
    localStorage.setItem('gca_sb_open', '1');
    // Stop the daily cost toast from stealing focus or covering controls.
    localStorage.setItem('gca_cost_notified', new Date().toISOString().slice(0, 10));
  } catch {}
});
const page = await ctx.newPage();

let statsHits = 0;
page.on('request', (r) => {
  if (new URL(r.url()).pathname === '/api/server/stats') statsHits += 1;
});

await page.goto(APP, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(2500);   // boot, including the one _fetchStats() on mount

// The store must actually be reachable and must actually hold `streaming`.
// Without this, a driver that silently did nothing would make the assertion
// below fail for the wrong reason, or — worse — a driver that could not import
// would look like a missing refresh.
const probe = await page.evaluate(async () => {
  try {
    const m = await import('/static/js/state/store.js');
    return {
      ok: typeof m.setState === 'function' && typeof m.getState === 'function',
      hasStreaming: 'streaming' in m.getState(),
    };
  } catch (e) {
    return { ok: false, error: String(e).slice(0, 120) };
  }
});

if (!probe.ok) {
  add('0. the store module is drivable from the page', 'INCONCLUSIVE',
      `import failed or exports missing: ${JSON.stringify(probe)}`);
} else {
  add('0. the store module is drivable from the page', 'PASS',
      `setState/getState present, streaming field present: ${probe.hasStreaming}`);

  // --- 1. a turn ENDING refreshes the stats -------------------------------
  {
    const label = '1. streaming true->false refreshes the stats panel';
    const before = statsHits;
    await page.evaluate(async () => {
      const m = await import('/static/js/state/store.js');
      m.setState({ streaming: true });
    });
    await page.waitForTimeout(300);
    const afterStart = statsHits;

    await page.evaluate(async () => {
      const m = await import('/static/js/state/store.js');
      m.setState({ streaming: false });
    });
    await page.waitForTimeout(WINDOW_MS);
    const afterEnd = statsHits;

    add(label, afterEnd > afterStart ? 'PASS' : 'FAIL',
        `/api/server/stats calls: boot->${before}, after start=${afterStart}, ` +
        `after end=${afterEnd} (needs +1 within ${WINDOW_MS}ms; the fallback poll is 60s away)`);
  }

  // --- 2. the false-positive guard ----------------------------------------
  // Without this, a _fetchStats() wired to fire on EVERY store change would
  // satisfy case 1 while being obviously wrong.
  {
    const label = '2. an unrelated store change does NOT refresh the stats';
    const before = statsHits;
    await page.evaluate(async () => {
      const m = await import('/static/js/state/store.js');
      m.setState({ __unrelated: Date.now() });
    });
    await page.waitForTimeout(WINDOW_MS);
    add(label, statsHits === before ? 'PASS' : 'FAIL',
        `/api/server/stats calls ${before} -> ${statsHits} (must be unchanged)`);
  }

  // --- 3. starting a turn does not refresh either --------------------------
  {
    const label = '3. a turn STARTING does not refresh the stats';
    await page.evaluate(async () => {
      const m = await import('/static/js/state/store.js');
      m.setState({ streaming: false });
    });
    await page.waitForTimeout(400);
    const before = statsHits;
    await page.evaluate(async () => {
      const m = await import('/static/js/state/store.js');
      m.setState({ streaming: true });
    });
    await page.waitForTimeout(WINDOW_MS);
    add(label, statsHits === before ? 'PASS' : 'FAIL',
        `/api/server/stats calls ${before} -> ${statsHits} (a turn beginning bills nothing yet)`);
  }
}

await browser.close();

console.log('\n=== stats-refresh-check ===\n');
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — a finished turn refreshes the stats, and nothing else does');
process.exit(0);
