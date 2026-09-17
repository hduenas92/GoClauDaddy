/**
 * resync-check.mjs — reconnect resync (task 2-F): busy indicator + "completed
 * while you were away" marker.
 *
 * Tests the real ChatSocket module imported in the page, not a re-implementation.
 * A test that re-implements the logic it is testing passes even when the
 * production code is deleted — that has already happened once on this project
 * (the stderr filter), so the module under test is always the real import.
 *
 * The two ordering properties below are the ones that cannot be observed by
 * driving the UI, because both failure modes are silent:
 *   - checkResync() must not emit before its listeners exist.
 *   - send() must not leave a pending flag when the send itself throws.
 */
import { chromium } from 'playwright';

const APP_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const CID = process.env.GCA_CID ?? 'eb399968-5e69-4dc7-8c0c-c713b73c6953';

const results = [];
const t = (label, ok, detail = '') => {
  results.push({ label, ok, detail });
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? `  -> ${detail}` : ''}`);
};

const browser = await chromium.launch();

/** Fresh isolated context per case: localStorage leaks between cases otherwise,
 *  which is how an earlier focus test on this project silently became vacuous. */
async function withPage(fn, { status = 'idle', pending = false } = {}) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.addInitScript(
    ([cid, pend]) => {
      try {
        localStorage.setItem('gca_onboarded', '1');
        if (pend) localStorage.setItem(`gca_pending_${cid}`, '1');
        else localStorage.removeItem(`gca_pending_${cid}`);
      } catch {}
    },
    [CID, pending],
  );
  // Force the conversation's reported status by PATCHING the real response, not
  // by fabricating one. GET /api/conversations/{id} returns {conversation,
  // messages} and the app renders history from that same payload — a stub
  // carrying only `conversation` silently leaves the transcript empty, so
  // "no marker" then measures a broken boot rather than the feature. Fetching
  // through and overriding one field keeps every other field real.
  //
  // Scoped by pathname, not a `**/api/**` glob — that glob also matches the
  // static module at /static/js/api/socket.js and breaks module loading.
  await page.route('**/*', async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p !== `/api/conversations/${CID}`) return route.continue();
    const real = await route.fetch();
    const body = await real.json();
    body.conversation.status = status;
    return route.fulfill({ response: real, json: body });
  });
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  try {
    return await fn(page);
  } finally {
    await ctx.close();
  }
}

console.log(`=== resync-check  ${APP_URL}  conv=${CID} ===\n`);

// ---------------------------------------------------------------------------
// A. Ordering: connect() must NOT emit. The emit is the caller's to trigger,
//    after it has bound listeners. Previously checkResync() fired inside the
//    `open` handler and only reached a listener because the awaited REST
//    round-trip happened to outlast a synchronous line of JS in main.js.
// ---------------------------------------------------------------------------
console.log('A. checkResync is caller-controlled, not fired by connect():');
{
  const r = await withPage(
    (page) =>
      page.evaluate(async (cid) => {
        const { ChatSocket } = await import('/static/js/api/socket.js');
        const s = new ChatSocket(cid);
        const seen = [];
        s.on('_resync_running', () => seen.push('running'));
        s.on('_resync_done', () => seen.push('done'));
        await s.connect();
        // Wait longer than any REST round-trip could take. If connect() were
        // still firing resync internally, this window would catch it.
        await new Promise((res) => setTimeout(res, 800));
        const afterConnect = [...seen];
        await s.checkResync();
        await new Promise((res) => setTimeout(res, 300));
        return { afterConnect, afterExplicitCall: [...seen] };
      }, CID),
    { status: 'busy' },
  );
  t('connect() alone emits nothing', r.afterConnect.length === 0, JSON.stringify(r.afterConnect));
  t('explicit checkResync() emits', r.afterExplicitCall.includes('running'),
    JSON.stringify(r.afterExplicitCall));
}

// ---------------------------------------------------------------------------
// B. send() must not orphan the pending flag when the send throws.
// ---------------------------------------------------------------------------
console.log('\nB. send() flags only a send that actually happened:');
{
  // THROWAWAY CONVERSATION, not CID. Case b1 performs a REAL send over a REAL
  // socket, which reaches the backend and spawns a real `claude` turn. Pointing
  // it at an existing conversation writes junk into the user's actual history —
  // it did exactly that, 18 rows of "hello" plus persisted API errors, because
  // this file is named as an acceptance check in several agent briefs and every
  // run added more. The conversation is created here and deleted in the finally
  // below, so the real data is never touched.
  const mk = await fetch(`${APP_URL}/api/conversations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'resync-check throwaway (safe to delete)' }),
  });
  const scratch = (await mk.json()).id;
  if (!scratch) throw new Error('could not create a throwaway conversation');
  console.log(`  (using throwaway conversation ${scratch}, deleted afterwards)`);

  try {
  const r = await withPage((page) =>
    page.evaluate(async (cid) => {
      const { ChatSocket } = await import('/static/js/api/socket.js');
      const key = `gca_pending_${cid}`;

      // b1: healthy send sets the flag.
      const ok = new ChatSocket(cid);
      await ok.connect();
      localStorage.removeItem(key);
      ok.send('hello');
      const flagAfterGoodSend = localStorage.getItem(key);
      ok.close?.();

      // b2: send on a closed socket throws, and must leave no flag behind.
      const dead = new ChatSocket(cid);
      await dead.connect();
      dead.ws.close();
      await new Promise((res) => setTimeout(res, 300));
      localStorage.removeItem(key);
      let threw = false;
      try { dead.send('never arrives'); } catch { threw = true; }
      return {
        flagAfterGoodSend,
        readyState: dead.ws.readyState,
        threw,
        flagAfterFailedSend: localStorage.getItem(key),
      };
    }, scratch),
  );
  t('healthy send sets the pending flag', r.flagAfterGoodSend === '1', String(r.flagAfterGoodSend));
  // Guarded by an explicit readyState check, NOT by ws.send() throwing: it does
  // not throw on a closed socket, it discards silently. Measured, then fixed.
  t('send on a closed socket is refused', r.threw,
    `readyState=${r.readyState} threw=${r.threw}`);
  t('a throwing send leaves NO pending flag', r.flagAfterFailedSend === null,
    `flag=${JSON.stringify(r.flagAfterFailedSend)}`);
  } finally {
    // Cascades to its messages. Runs even if an assertion above threw, so a
    // failed run cannot leave scratch conversations accumulating.
    const del = await fetch(`${APP_URL}/api/conversations/${scratch}`, { method: 'DELETE' });
    console.log(`  (throwaway ${scratch} deleted: ${del.ok ? 'ok' : 'FAILED — remove it by hand'})`);
  }
}

// ---------------------------------------------------------------------------
// C. The three behaviours, driven through the real app boot.
// ---------------------------------------------------------------------------
const marker = '.msg-assistant .msg-meta';
const readUi = async (page) => {
  await page.waitForTimeout(1800); // boot + bindSocket + checkResync round-trip
  return page.evaluate((sel) => ({
    status: document.querySelector('#chat-status')?.textContent?.trim() ?? '',
    markers: [...document.querySelectorAll(sel)]
      .filter((m) => m.textContent.includes('Completed while you were away')).length,
  }), marker);
};

console.log('\nC. Behaviour through a real app boot:');
{
  const busy = await withPage(readUi, { status: 'busy', pending: true });
  t('busy on return -> in-progress notice', /still responding/i.test(busy.status),
    JSON.stringify(busy.status));
  t('busy on return -> no completed marker', busy.markers === 0, `markers=${busy.markers}`);

  const done = await withPage(readUi, { status: 'idle', pending: true });
  t('idle + unseen turn -> completed marker', done.markers === 1, `markers=${done.markers}`);

  // The false-positive guard. Identical to the case above in every respect
  // except the flag — so if this also produced a marker, the marker would be
  // driven by "conversation has a trailing assistant message", which is true of
  // every finished conversation, and the feature would be meaningless.
  const plain = await withPage(readUi, { status: 'idle', pending: false });
  t('idle + no pending flag -> NO marker (false-positive guard)', plain.markers === 0,
    `markers=${plain.markers}`);
  t('idle + no pending flag -> no in-progress notice', !/still responding/i.test(plain.status),
    JSON.stringify(plain.status));
}

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log();
if (failed.length) {
  console.log(`RESULT: FAIL — ${failed.length} of ${results.length}`);
  for (const f of failed) console.log(`  - ${f.label}  (${f.detail})`);
  process.exit(1);
}
console.log(`RESULT: PASS — ${results.length}/${results.length}`);
process.exit(0);
