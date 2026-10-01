/**
 * connection-ux-check.mjs — task 2.5 (4-I5 + 4-I6 + N2 + N10).
 *
 * Four things a user must never have to guess at:
 *   1. the socket died while IDLE            -> say so
 *   2. I switched conversation on purpose    -> say NOTHING
 *   3. my message did not leave              -> say so, and give it back
 *   4. after that refusal the composer WORKS -> Send visible, Stop hidden, and
 *      a second attempt is actually accepted
 *
 * Assertion 4 is the one that matters. `streaming` stuck true makes send()
 * early-return forever, so a permanently wedged composer passes every check
 * that only looks at pixels. This asserts BEHAVIOUR: a second send must produce
 * a second refusal rather than silence, which is only possible if the flag was
 * actually cleared.
 *
 * HOW THE SOCKET IS REACHED. main.js keeps it in a module closure, so the page
 * cannot hand it over. An init script wraps window.WebSocket and records every
 * instance. Calling .close() on the RAW socket — not on the ChatSocket wrapper —
 * is what makes the close unintended, which is precisely the case 2.9 taught the
 * app to distinguish.
 *
 * DATA. Cases 3-5 send real messages (5 runs a real turn). They go only into a
 * conversation this harness creates (A; B is the target of case 2's switch):
 * the app boots into the most recently updated conversation, so A is bumped
 * before every case, and a case that boots anywhere else sends nothing
 * (INCONCLUSIVE). Both are deleted at the end (case 6), after A's last turn
 * finishes. Before this, every run appended to the user's newest conversation.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const INDICATOR = /connection lost|reconnect/i;
const NAME_A = 'connection-ux-check A (temporary)';
const NAME_B = 'connection-ux-check B (temporary)';

async function api(path, { method = 'GET', body } = {}) {
  const r = await fetch(URL + path, {
    method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} -> HTTP ${r.status}`);
  return r.json();
}
const baseline = (await api('/api/conversations')).length;
const OWN_B = (await api('/api/conversations', { method: 'POST', body: { name: NAME_B } })).id;
const OWN_A = (await api('/api/conversations', { method: 'POST', body: { name: NAME_A } })).id;
const bumpA = () => api(`/api/conversations/${OWN_A}/rename`, { method: 'PATCH', body: { name: NAME_A } });
const NOT_OWN = (booted) => `booted into ${booted ?? 'nothing'}, not the harness's own conversation — nothing sent`;

const browser = await chromium.launch();
const results = [];
const add = (label, state, detail) => results.push({ label, state, detail });

async function freshPage() {
  await bumpA(); // newest => the app boots into it
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_feat_assess', '0');
    } catch {}
    const Orig = window.WebSocket;
    const wrapped = function (...args) {
      const ws = new Orig(...args);
      (window.__sockets = window.__sockets || []).push(ws);
      return ws;
    };
    wrapped.prototype = Orig.prototype;
    for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) wrapped[k] = Orig[k];
    window.WebSocket = wrapped;
  });
  const page = await ctx.newPage();
  const opened = [];
  page.on('request', (r) => {
    const m = new globalThis.URL(r.url()).pathname.match(/^\/api\/conversations\/([0-9a-f-]{36})$/);
    if (m && r.method() === 'GET') opened.push(m[1]);
  });
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  return { ctx, page, booted: opened[0] ?? null };
}

const statusText = (page) =>
  page.evaluate(() => document.getElementById('chat-status')?.textContent ?? '');

const killSocket = (page) =>
  page.evaluate(() => {
    const list = window.__sockets || [];
    const ws = list[list.length - 1];
    if (!ws) return false;
    ws.close();          // raw close -> the wrapper never sets _intentionalClose
    return true;
  });

try {
  // --- 1. Dropped while IDLE ------------------------------------------------
  {
    const label = '1. a socket dropped while IDLE shows an indicator';
    const { ctx, page } = await freshPage();
    try {
      if (!(await killSocket(page))) {
        add(label, 'INCONCLUSIVE', 'no WebSocket was captured; the app may not have connected');
      } else {
        await page.waitForFunction(
          () => /connection lost|reconnect/i.test(document.getElementById('chat-status')?.textContent ?? ''),
          { timeout: 2000 },
        ).catch(() => {});
        const txt = await statusText(page);
        add(label, INDICATOR.test(txt) ? 'PASS' : 'FAIL', `#chat-status: ${JSON.stringify(txt)}`);
      }
    } finally { await ctx.close(); }
  }

  // --- 2. Intentional switch must stay SILENT -------------------------------
  // Asserts an ABSENCE. A test that only checked the indicator appears would pass
  // on the version that flashes it on every switch — the bug 2.9 fixed.
  {
    const label = '2. an intentional conversation switch shows NO indicator';
    const { ctx, page, booted } = await freshPage();
    try {
      const rowB = page.locator('.conv-item', { hasText: NAME_B }).locator('.conv-body');
      const nB = await rowB.count();
      if (booted !== OWN_A) {
        add(label, 'INCONCLUSIVE', NOT_OWN(booted));
      } else if (nB !== 1) {
        add(label, 'INCONCLUSIVE', `need the harness's own second conversation to switch to; ${nB} rows match`);
      } else {
        // Sampling #chat-status once, after the switch, CANNOT detect a flash:
        // the incoming conversation's render clears it, so a naive indicator that
        // fired and vanished reads as silence. Record every value it takes.
        await page.evaluate(() => {
          window.__statusLog = [];
          const el = document.getElementById('chat-status');
          if (!el) return;
          window.__statusLog.push(el.textContent ?? '');
          new MutationObserver(() => window.__statusLog.push(el.textContent ?? ''))
            .observe(el, { childList: true, characterData: true, subtree: true });
        });
        await rowB.click();
        await page.waitForTimeout(2200);
        const seen = await page.evaluate(() => window.__statusLog || []);
        const flashed = seen.filter((t) => /connection lost|reconnect/i.test(t));
        add(label, flashed.length ? 'FAIL' : 'PASS',
            flashed.length
              ? `indicator flashed on a deliberate switch: ${JSON.stringify(flashed[0])} (${seen.length} status changes observed)`
              : `never appeared across ${seen.length} observed status change(s)`);
      }
    } finally { await ctx.close(); }
  }

  // --- 3 & 4. Refused send --------------------------------------------------
  {
    const { ctx, page, booted } = await freshPage();
    try {
      const before = await page.evaluate(() => document.querySelectorAll('.msg').length);
      if (booted !== OWN_A) {
        add('3. a refused send is reported and the bubble is undone', 'INCONCLUSIVE', NOT_OWN(booted));
        add('4. the composer still works after a refusal', 'INCONCLUSIVE', NOT_OWN(booted));
      } else if (!(await killSocket(page))) {
        add('3. a refused send is reported and the bubble is undone', 'INCONCLUSIVE', 'no WebSocket captured');
        add('4. the composer still works after a refusal', 'INCONCLUSIVE', 'no WebSocket captured');
      } else {
        await page.waitForTimeout(400);
        await page.fill('#composer-input', 'this message must not silently vanish');
        await page.click('#composer-send');
        await page.waitForTimeout(1200);

        const s1 = await page.evaluate(() => ({
          toast: document.querySelector('.error-toast')?.innerText?.replace(/\s+/g, ' ').trim() ?? '',
          msgs: document.querySelectorAll('.msg').length,
          inputValue: document.getElementById('composer-input')?.value ?? '',
          sendHidden: document.getElementById('composer-send')?.hidden ?? null,
          stopHidden: document.getElementById('composer-stop')?.hidden ?? null,
        }));

        const reported = s1.toast.length > 0;
        const undone = s1.msgs === before;
        add('3. a refused send is reported and the bubble is undone',
            reported && undone ? 'PASS' : 'FAIL',
            `toast=${JSON.stringify(s1.toast.slice(0, 80))} msgs ${before}->${s1.msgs} ` +
            `input=${JSON.stringify(s1.inputValue.slice(0, 40))}`);

        // 4 — pixels AND behaviour. A second attempt must reach the refusal again;
        // if `streaming` were still true, send() would early-return and produce
        // nothing at all, which is the wedged state this exists to catch.
        await page.evaluate(() => document.querySelector('.error-toast')?.remove());

        // Do NOT kill the socket again here, however tempting it is for
        // determinism. chat_pane's `_disconnected` handler independently calls
        // setStreaming(false), so a second drop RESCUES the very state this
        // assertion is trying to find missing — measured: with
        // setStreaming(false) deleted from _undoFailedSend, the kill-again
        // version still passed. The assertion was unable to fail.
        //
        // Instead wait for the wrapper's own reconnect, so the socket is OPEN
        // again and a second send must visibly land. If `streaming` was left
        // true, send() early-returns and NOTHING happens — which is the wedge.
        const before2 = await page.evaluate(() => document.querySelectorAll('.msg').length);
        const reconnected = await page.waitForFunction(() => {
          const l = window.__sockets || [];
          return l.some((w) => w.readyState === 1);
        }, { timeout: 8000 }).then(() => true).catch(() => false);
        await page.fill('#composer-input', 'second attempt');
        await page.click('#composer-send');
        await page.waitForTimeout(1200);
        const s2 = await page.evaluate(() => ({
          toast: document.querySelector('.error-toast')?.innerText?.trim() ?? '',
          sendHidden: document.getElementById('composer-send')?.hidden ?? null,
          stopHidden: document.getElementById('composer-stop')?.hidden ?? null,
        }));

        const uiOk = s1.sendHidden === false && s1.stopHidden === true;
        // Either outcome proves `streaming` was cleared: a refusal reported, or a
        // message actually appended. Only SILENCE means send() early-returned,
        // which is the wedged composer.
        const msgsNow = await page.evaluate(() => document.querySelectorAll('.msg').length);
        const acceptsMore = s2.toast.length > 0 || msgsNow > before2;
        add('4. the composer still works after a refusal',
            !reconnected ? 'INCONCLUSIVE' : (uiOk && acceptsMore ? 'PASS' : 'FAIL'),
            `after refusal: sendHidden=${s1.sendHidden} stopHidden=${s1.stopHidden}; ` +
            `second attempt ${acceptsMore ? 'was processed (streaming cleared)' : 'produced NOTHING (composer is wedged)'}`);
      }
    } finally { await ctx.close(); }
  }

  // --- 5. N1: a turn that dies with its socket must not strand the composer ---
  // Needs a REAL streaming turn: the defect is that `done` never arrives, and
  // `done` is the only thing that restores the composer. If the turn finishes
  // before we can cut the socket, the case reports INCONCLUSIVE rather than
  // passing — a turn that completed normally proves nothing about this path.
  {
    const label = '5. a turn killed mid-stream restores the composer';
    const { ctx, page, booted } = await freshPage();
    try {
      if (booted !== OWN_A) {
        add(label, 'INCONCLUSIVE', NOT_OWN(booted));
        throw new Error('__handled__');
      }
      await page.fill('#composer-input', 'Count slowly from 1 to 20, one number per line.');
      await page.click('#composer-send');

      const streaming = await page.waitForFunction(
        () => document.getElementById('composer-stop')?.hidden === false,
        { timeout: 15000 },
      ).then(() => true).catch(() => false);

      if (!streaming) {
        add(label, 'INCONCLUSIVE', 'Stop never became visible — no turn was caught mid-stream');
      } else {
        await killSocket(page);
        const restored = await page.waitForFunction(
          () => document.getElementById('composer-send')?.hidden === false &&
                document.getElementById('composer-stop')?.hidden === true,
          { timeout: 4000 },
        ).then(() => true).catch(() => false);

        // Bail out BEFORE touching the composer if it never came back. Clicking a
        // hidden #composer-send makes Playwright wait 30s for visibility and then
        // THROW, which crashes the harness instead of reporting a failure — an
        // ambiguous crash reads as "not proven" rather than as the clear FAIL this
        // is. That is exactly the stranded state the task exists to prevent, so
        // name it.
        if (!restored) {
          add(label, 'FAIL',
              'Send never returned / Stop never hid after the drop — the composer is stranded, ' +
              'which is N1 exactly: no `done` can arrive for a turn that died with its socket');
          await ctx.close();
          throw new Error('__handled__');
        }

        // The composer looking right is not enough: `streaming` stuck true makes
        // send() early-return forever while Send sits there looking usable.
        // A subsequent attempt must actually be processed.
        const before = await page.evaluate(() => document.querySelectorAll('.msg').length);
        await page.evaluate(() => document.querySelector('.error-toast')?.remove());
        await page.waitForFunction(() => (window.__sockets || []).some((w) => w.readyState === 1),
          { timeout: 8000 }).catch(() => {});
        await page.fill('#composer-input', 'after the drop');
        await page.click('#composer-send');
        await page.waitForTimeout(1500);
        const after = await page.evaluate(() => ({
          msgs: document.querySelectorAll('.msg').length,
          toast: document.querySelector('.error-toast')?.innerText?.trim() ?? '',
        }));
        const accepted = after.msgs > before || after.toast.length > 0;

        add(label, restored && accepted ? 'PASS' : 'FAIL',
            `buttons restored=${restored}; subsequent send ${accepted ? 'accepted' : 'SILENTLY DROPPED (composer wedged)'}`);
      }
    } catch (e) {
      if (e?.message !== '__handled__') {
        add(label, 'FAIL', `case threw before it could assert: ${e?.message || e}`);
      }
    } finally {
      await ctx.close().catch(() => {});
    }
  }
} finally {
  // --- 6. Leave nothing behind. Case 5's turn keeps running headless after its
  // socket is cut; deleting A under it would make that turn write into nothing.
  const problems = [];
  for (const t0 = Date.now(); Date.now() - t0 < 120000;) {
    const busy = await api(`/api/conversations/${OWN_A}`).then((r) => r.conversation.status === 'busy', () => false);
    if (!busy) break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  for (const id of [OWN_A, OWN_B]) {
    await api(`/api/conversations/${id}`, { method: 'DELETE' }).catch((e) => problems.push(e.message));
  }
  const list = await api('/api/conversations').catch(() => null);
  const left = list ? list.filter((c) => c.id === OWN_A || c.id === OWN_B).length : -1;
  add('6. the harness leaves no data behind', left === 0 && list.length === baseline ? 'PASS' : 'FAIL',
      `own conversations left: ${left}; conversations ${baseline} before -> ${list?.length ?? '?'} after` +
      (problems.length ? `; ${problems.join('; ')}` : ''));
}

await browser.close();

console.log('\n=== connection-ux-check ===\n');
for (const r of results) console.log(`${r.state.padEnd(13)} ${r.label}\n              ${r.detail}`);

if (results.length === 0) { console.log('\nRESULT: FAIL — nothing ran.'); process.exit(1); }
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`\n${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — drops are visible, switches are silent, refusals are recoverable');
process.exit(0);
