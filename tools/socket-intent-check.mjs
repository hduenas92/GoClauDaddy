/**
 * socket-intent-check.mjs — task 2.9 (N3 + N7).
 *
 * N3: a deliberate conversation switch must be distinguishable from a genuine
 * drop. `_close` was emitted BEFORE intent was known, so any indicator built on
 * it flashed on every switch — worse than no indicator, because it teaches the
 * user to ignore the real one. `_disconnected` now fires only on an unintended
 * drop.
 *
 * N7: `stop()` had no readyState guard. Pressing Stop on a dead socket is
 * exactly what a user does when the app looks hung.
 *
 * WHY THIS TESTS THE MODULE, NOT THE UI. 2.9's whole job is to make a signal
 * available that task 2.5 will later consume. Asserting through a visible
 * indicator would make this harness depend on 2.5, and 2.5 already depends on
 * 2.9 — a circle in which neither can be proven first. The page imports
 * socket.js directly (it is served statically), so the contract is asserted
 * where it lives.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const results = [];
const add = (label, state, detail) => results.push({ label, state, detail });

const ctx = await browser.newContext();
const page = await ctx.newPage();
await page.addInitScript(() => { try { localStorage.setItem('gca_onboarded', '1'); } catch {} });
await page.goto(URL, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(800);

const convId = await page.evaluate(async () => {
  const r = await fetch('/api/conversations');
  const j = await r.json();
  const list = Array.isArray(j) ? j : (j.conversations ?? []);
  return list[0]?.id ?? null;
});

if (!convId) {
  add('all cases', 'INCONCLUSIVE', 'no conversation exists to open a socket against');
} else {
  const out = await page.evaluate(async (id) => {
    const { ChatSocket } = await import('/static/js/api/socket.js');
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const res = {};

    // --- 1. Intentional close must NOT report a disconnection. ------------
    {
      const s = new ChatSocket(id);
      let dropped = false;
      let closePayload = null;
      s.on('_disconnected', () => { dropped = true; });
      s.on('_close', (d) => { closePayload = d; });
      await s.connect();
      s.close();                       // the deliberate path, as main.js:65 does
      await sleep(700);
      res.intentional = { dropped, closePayload };
    }

    // --- 2. A genuine drop MUST report one. -------------------------------
    {
      const s = new ChatSocket(id);
      let dropped = false;
      s.on('_disconnected', () => { dropped = true; });
      await s.connect();
      s.ws.close();                    // bypasses close(), so intent stays false
      await sleep(700);
      s.close();                       // stop the reconnect timer this started
      res.genuine = { dropped };
    }

    // --- 3. stop() on a non-OPEN socket: no throw, reports refusal. -------
    {
      const s = new ChatSocket(id);
      await s.connect();
      s.close();
      await sleep(400);
      try {
        res.stopDead = { threw: false, returned: s.stop() };
      } catch (e) {
        res.stopDead = { threw: true, message: String(e && e.message) };
      }
    }

    // --- 4. stop() on an OPEN socket must still WORK. ---------------------
    // Without this, `stop() { return false; }` would satisfy case 3 while
    // breaking the Stop button entirely.
    {
      const s = new ChatSocket(id);
      await s.connect();
      try {
        res.stopLive = { threw: false, returned: s.stop() };
      } catch (e) {
        res.stopLive = { threw: true, message: String(e && e.message) };
      }
      s.close();
    }

    return res;
  }, convId);

  // 1 — assert the ABSENCE. An assertion that only checks the indicator appears
  // would pass on the flashing version, which is the bug.
  add('an intentional close reports NO disconnection',
      out.intentional.dropped === false ? 'PASS' : 'FAIL',
      out.intentional.dropped ? '_disconnected fired on a deliberate close' : '_disconnected did not fire');

  add('_close still fires, now carrying intent',
      out.intentional.closePayload && out.intentional.closePayload.intentional === true ? 'PASS' : 'FAIL',
      `_close payload: ${JSON.stringify(out.intentional.closePayload)}`);

  add('a genuine drop DOES report a disconnection',
      out.genuine.dropped === true ? 'PASS' : 'FAIL',
      out.genuine.dropped ? '_disconnected fired' : '_disconnected never fired on an unintended close');

  add('stop() on a dead socket does not throw and reports refusal',
      out.stopDead.threw === false && out.stopDead.returned === false ? 'PASS' : 'FAIL',
      JSON.stringify(out.stopDead));

  add('stop() on a live socket still sends',
      out.stopLive.threw === false && out.stopLive.returned === true ? 'PASS' : 'FAIL',
      JSON.stringify(out.stopLive));
}

await ctx.close();
await browser.close();

console.log('\n=== socket-intent-check ===\n');
for (const r of results) console.log(`${r.state.padEnd(13)} ${r.label}\n              ${r.detail}`);

if (results.length === 0) {
  console.log('\nRESULT: FAIL — no case executed; this run asserted nothing.');
  process.exit(1);
}
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`\n${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — close carries intent, drops are distinguishable, stop() is guarded both ways');
process.exit(0);
