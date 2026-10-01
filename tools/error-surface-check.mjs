/**
 * error-surface-check.mjs — task 2.4 (4-I4): every non-boot failure must reach
 * the user through ONE surface, with text that says what failed and what to do.
 *
 * Before 2.4 the app reported failures five different ways: five native alert()
 * dialogs, a 2-second CSS flash with no words, and two catches that swallowed
 * the error entirely. A beginner cannot tell a swallowed failure from their own
 * mistake, so they conclude the tool is broken and stop.
 *
 * METHOD. Each case forces a REAL failure through Playwright route interception
 * and then asserts the shared `.error-toast` appears carrying actionable text.
 * Every case gets its OWN browser context: a shared context leaks localStorage
 * feature flags and a toast from a previous case, which would let a later
 * assertion pass on an earlier case's DOM.
 *
 * WHAT THIS DELIBERATELY DOES NOT ASSERT. Element counts (§2.2). Each case is a
 * per-path invariant — this toast, this text — or a zero-violation sweep.
 *
 * Usage:  node tools/error-surface-check.mjs          (app must be on :8765)
 * Exit:   0 PASS · 1 FAIL · 2 INCONCLUSIVE (a path could not be exercised)
 */
import { chromium } from 'playwright';

// Navigation uses domcontentloaded, never networkidle. networkidle is
// documented by Playwright as discouraged and inherently racy, and it hung in a
// clean Linux container against the /api/server/logs SSE stream. It was also
// redundant here: every navigation below is followed by an explicit wait, and
// that is what actually establishes readiness.

const APP_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const TOAST = '.error-toast';
// The URL the PRODUCT actually loads — terminal.js:13. P2-D moved xterm from
// cdnjs (which now 404s) to jsdelivr; this harness used to intercept the old
// cdnjs URL, so case 4 blocked a script the app no longer fetches and the
// "xterm unreachable" path was never exercised. Intercept the real URL.
const XTERM_JS = 'https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js';

const browser = await chromium.launch();
const results = [];

const pass = (name, detail) => results.push({ name, state: 'PASS', detail });
const fail = (name, detail) => results.push({ name, state: 'FAIL', detail });
const skip = (name, detail) => results.push({ name, state: 'INCONCLUSIVE', detail });

/**
 * Fresh context with the onboarding tour suppressed (its overlay swallows every
 * click below) and the assessment gate pinned off so the send path is
 * deterministic rather than sitting behind a 9s heuristic race.
 */
async function freshPage({ terminal = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((wantTerminal) => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_feat_assess', '0');
      if (wantTerminal) localStorage.setItem('gca_feat_terminal', '1');
    } catch {}
  }, terminal);
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  return { ctx, page, pageErrors };
}

/** The assertion every case shares: a toast, visible, carrying real words. */
async function expectToast(page, name, mustContain) {
  try {
    await page.waitForSelector(TOAST, { state: 'visible', timeout: 6000 });
  } catch {
    fail(name, 'no .error-toast appeared within 6s');
    return false;
  }
  const text = (await page.locator(TOAST).first().innerText()).replace(/\s+/g, ' ').trim();

  // Actionable means: says what failed AND what to do. A bare "Error" or an
  // undefined message is the failure mode this task exists to remove.
  if (!text || text.length < 15) {
    fail(name, `toast text too thin to be actionable: "${text}"`);
    return false;
  }
  if (/\bundefined\b|\[object Object\]/.test(text)) {
    fail(name, `toast leaks a non-message: "${text}"`);
    return false;
  }
  if (mustContain && !text.toLowerCase().includes(mustContain.toLowerCase())) {
    fail(name, `toast did not mention "${mustContain}": "${text}"`);
    return false;
  }
  pass(name, `"${text.slice(0, 96)}"`);
  return true;
}

// ---------------------------------------------------------------------------
// 1. Attachment upload failure — composer.js
// ---------------------------------------------------------------------------
{
  const name = '1. attachment upload failure shows a toast';
  const { ctx, page } = await freshPage();
  await page.route('**/api/attachments**', (r) =>
    r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"disk full"}' }));
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  const input = await page.$('#composer input[type=file], input[type=file]');
  if (!input) {
    skip(name, 'no file input found in the composer');
  } else {
    await input.setInputFiles({ name: 'probe.txt', mimeType: 'text/plain', buffer: Buffer.from('x') });
    await expectToast(page, name, 'attach');
  }
  await ctx.close();
}

// ---------------------------------------------------------------------------
// 2. Project create failure — sidebar_projects.js
// ---------------------------------------------------------------------------
{
  const name = '2. project create failure shows a toast';
  const { ctx, page } = await freshPage();
  await page.route('**/api/projects', (r) =>
    r.request().method() === 'POST'
      ? r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"cannot create"}' })
      : r.continue());
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  const btn = await page.$('#new-project-btn');
  if (!btn) {
    skip(name, '#new-project-btn not found');
  } else {
    await btn.click();
    const nameField = await page.waitForSelector('input[name=name], .modal input', { timeout: 4000 }).catch(() => null);
    if (!nameField) {
      skip(name, 'project modal did not open');
    } else {
      await nameField.fill('probe-project');
      const confirm = await page.$('button:has-text("Create")');
      if (!confirm) skip(name, 'Create button not found in modal');
      else { await confirm.click(); await expectToast(page, name, 'project'); }
    }
  }
  await ctx.close();
}

// ---------------------------------------------------------------------------
// 3. Project save failure — sidebar_projects.js
// ---------------------------------------------------------------------------
{
  const name = '3. project save failure shows a toast';
  const { ctx, page } = await freshPage();
  await page.route('**/api/projects/*', (r) =>
    r.request().method() === 'PATCH'
      ? r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"cannot save"}' })
      : r.continue());
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  const edit = await page.$('.project-edit');
  if (!edit) {
    skip(name, 'no project exists to edit — create one to exercise this path');
  } else {
    await edit.click();
    const save = await page.waitForSelector('.pef-save', { timeout: 4000 }).catch(() => null);
    if (!save) skip(name, 'project edit form did not open');
    else { await save.click(); await expectToast(page, name, 'project'); }
  }
  await ctx.close();
}

// ---------------------------------------------------------------------------
// 6. Settings write failure — was a wordless 2s CSS flash
// ---------------------------------------------------------------------------
{
  const name = '6. settings change failure shows a toast (not just a flash)';
  const { ctx, page } = await freshPage();
  await page.route('**/api/conversations/*/settings', (r) =>
    r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"cannot save"}' }));
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  await page.keyboard.press('Control+Comma');
  const sel = await page.waitForSelector('#model-select', { state: 'visible', timeout: 4000 }).catch(() => null);
  if (!sel) {
    skip(name, '#model-select not reachable — no active conversation?');
  } else {
    const options = await page.$$eval('#model-select option', (o) => o.map((x) => x.value));
    const current = await page.$eval('#model-select', (s) => s.value);
    const other = options.find((v) => v && v !== current);
    if (!other) skip(name, 'only one model option available; cannot trigger a change');
    else {
      await page.selectOption('#model-select', other);
      await expectToast(page, name, "couldn't");
    }
  }
  await ctx.close();
}

// ---------------------------------------------------------------------------
// 7. Resync check failure — was a fully silent catch in socket.js
// ---------------------------------------------------------------------------
{
  const name = '7. resync check failure shows a toast (was silent)';
  const { ctx, page } = await freshPage();

  // Which conversation is the app actually on? It is not in localStorage and not
  // on a data attribute — the store keeps it in a module closure. The socket URL
  // is the one place it surfaces observably: /ws/chat/<id>. Capture it rather
  // than guessing, because seeding the pending flag for the WRONG conversation
  // makes checkResync take the no-op branch and the case passes vacuously.
  let convId = null;
  page.on('websocket', (ws) => {
    const m = /\/ws\/chat\/([0-9a-fA-F-]{8,})/.exec(ws.url());
    if (m && !convId) convId = m[1];
  });
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);

  // Fallback: ask the API directly if no socket opened (e.g. zero conversations).
  if (!convId) {
    convId = await page.evaluate(async () => {
      try {
        const r = await fetch('/api/conversations');
        const j = await r.json();
        const list = Array.isArray(j) ? j : (j.conversations ?? []);
        return list[0]?.id ?? null;
      } catch { return null; }
    });
  }
  if (!convId) {
    skip(name, 'could not determine the active conversation id to seed a pending turn');
  } else {
    // The silent catch only runs when this client believes it left a turn
    // unfinished, so seed that belief, then make the status lookup fail.
    await page.evaluate((id) => localStorage.setItem(`gca_pending_${id}`, '1'), convId);

    // Failing EVERY GET of this conversation breaks boot instead of the resync
    // check: switchToConversation() awaits selectConversation(id) — the same URL
    // — outside any try, so an early failure kills the sequence before
    // socket.checkResync() at main.js:95 is ever reached. No toast, no test.
    // The same URL is fetched at least twice per switch, so let the first
    // through (boot survives) and fail the next (checkResync's lookup).
    let gets = 0;
    let failedAt = 0;
    await page.route(`**/api/conversations/${convId}`, (r) => {
      if (r.request().method() !== 'GET') return r.continue();
      gets += 1;
      if (gets === 1) return r.continue();
      failedAt = gets;
      return r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"boom"}' });
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    const ok = await expectToast(page, name, 'confirm');

    // A failure here is ambiguous between "the toast is missing" and "the path
    // was never reached", so report which. Same reason regen-diag.mjs exists.
    if (!ok) {
      const last = results[results.length - 1];
      last.detail += `  [diagnostic: ${gets} GET(s) of this conversation intercepted, ` +
        (failedAt ? `failed #${failedAt}` : 'none failed — checkResync may not have run') + ']';
    }
  }
  await ctx.close();
}

// ---------------------------------------------------------------------------
// 8. Regenerate failure — chat_pane.js regen click + composer.js _undoFailedSend
// ---------------------------------------------------------------------------
{
  const name = '8. regenerate failure shows a toast and the composer recovers';

  // The regenerate control is only painted when a live turn ends with status
  // "done" (chat_pane.js:526-561). A real turn spends tokens, so the WS is
  // stubbed: when the page sends {type:"send"} the stub answers with one text
  // frame and a done frame, which paints the assistant row and its regen button
  // without any model call. The done frame also fires the composer's first-turn
  // auto-title (composer.js:477-485); that POST is fulfilled below so this case
  // never writes a real title into the live DB.
  const convRes = await fetch(`${APP_URL}/api/conversations`).then(r => r.json()).catch(() => null);
  const convList = Array.isArray(convRes) ? convRes : (convRes?.conversations ?? []);
  if (convList.length === 0) {
    // Boot creates a conversation (a DB write) when the list is empty — never
    // do that from a read-only harness.
    skip(name, 'no conversation exists — boot would create one (a write) to exercise this path');
  } else {
    const { ctx, page } = await freshPage();
    let wsRoute = null;
    let killWs = false;
    await page.routeWebSocket(/\/ws\/chat\//, (ws) => {
      wsRoute = ws;
      // After the socket is killed below, every reconnect attempt must also die
      // closed, or a fast reconnect could make socket.send() succeed again and
      // this case would silently stop exercising the failure.
      if (killWs) { ws.close(); return; }
      ws.onMessage((msg) => {
        let j;
        try { j = JSON.parse(String(msg)); } catch { return; }
        if (j.type === 'send') {
          ws.send(JSON.stringify({ type: 'text', text: 'stub reply' }));
          ws.send(JSON.stringify({ type: 'done' }));
        }
      });
    });
    await page.route('**/api/conversations/*/auto-title', (r) =>
      r.request().method() === 'POST'
        ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"title":"stub"}' })
        : r.continue());

    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1200);
    const composerInput = await page.$('#composer-input');
    if (!composerInput) {
      skip(name, 'composer never mounted');
    } else {
      await page.fill('#composer-input', 'regenerate probe');
      await page.click('#composer-send');
      await page.waitForSelector('.regen-btn', { state: 'visible', timeout: 5000 }).catch(() => null);
      if ((await page.locator('.regen-btn').count()) === 0) {
        skip(name, 'regenerate control never appeared after the stubbed done turn');
      } else {
        // Kill the socket and pin the precondition: the page must have OBSERVED
        // the drop before the regen click, otherwise socket.send() succeeds and
        // the failure path is never exercised.
        killWs = true;
        await wsRoute.close();
        await page.waitForFunction(() =>
          (document.getElementById('chat-status')?.textContent || '').includes('Connection lost'),
          null, { timeout: 4000 }).catch(() => null);
        const status = await page.$eval('#chat-status', (el) => el.textContent).catch(() => '');
        if (!status.includes('Connection lost')) {
          skip(name, `socket drop was not observed (status="${status}") — cannot force the failure`);
        } else {
          await page.click('.regen-btn');
          const toastText = await page.waitForSelector(TOAST, { state: 'visible', timeout: 6000 })
            .then(() => page.locator(TOAST).first().innerText())
            .then((t) => t.replace(/\s+/g, ' ').trim())
            .catch(() => null);
          const rec = await page.evaluate(() => ({
            sendHidden: document.getElementById('composer-send')?.hidden,
            stopHidden: document.getElementById('composer-stop')?.hidden,
            inputValue: document.getElementById('composer-input')?.value,
            regenCount: document.querySelectorAll('.regen-btn').length,
            regenDisabled: document.querySelector('.regen-btn')?.disabled ?? null,
          }));

          // Same thresholds as expectToast (6s, >=15 chars, no undefined /
          // [object Object], mustContain), plus the NaN check this task adds.
          if (!toastText) {
            fail(name, 'no .error-toast appeared within 6s after a regenerate on a dead socket');
          } else if (toastText.length < 15) {
            fail(name, `toast text too thin to be actionable: "${toastText}"`);
          } else if (/\bundefined\b|\[object Object\]|\bNaN\b/.test(toastText)) {
            fail(name, `toast leaks a non-message: "${toastText}"`);
          } else if (!toastText.toLowerCase().includes('try again')) {
            fail(name, `toast did not mention "try again": "${toastText}"`);
          } else if (rec.sendHidden === true) {
            fail(name, 'composer send button stayed hidden after the failure');
          } else if (rec.stopHidden === false) {
            fail(name, 'composer stop button stayed visible after the failure');
          } else if (!rec.inputValue || rec.inputValue.trim() === '') {
            fail(name, 'composer input was not restored after the failure');
          } else if (rec.regenCount < 1) {
            fail(name, 'regenerate control disappeared after the failure');
          } else if (rec.regenDisabled) {
            fail(name, 'regenerate control is disabled after the failure');
          } else {
            pass(name, `"${toastText.slice(0, 96)}" · composer usable, regen enabled`);
          }
        }
      }
    }
    await ctx.close();
  }
}

// ---------------------------------------------------------------------------
await browser.close();

console.log('\n=== error-surface-check ===\n');
for (const r of results) {
  console.log(`${r.state.padEnd(13)} ${r.name}`);
  console.log(`              ${r.detail}`);
}

// Non-empty-set guard (§2.2). If nothing ran, this harness proves nothing, and
// "0 failures" must not read as success.
if (results.length === 0) {
  console.log('\nRESULT: FAIL — no case executed; this run asserted nothing.');
  process.exit(1);
}

const failed = results.filter((r) => r.state === 'FAIL');
const skipped = results.filter((r) => r.state === 'INCONCLUSIVE');
const passed = results.filter((r) => r.state === 'PASS');

console.log(`\n${passed.length} passed · ${failed.length} failed · ${skipped.length} inconclusive · ${results.length} case(s) run`);

if (failed.length) { console.log(`\nRESULT: FAIL — ${failed.length}`); process.exit(1); }
if (skipped.length) { console.log(`\nRESULT: INCONCLUSIVE — ${skipped.length} path(s) not exercised`); process.exit(2); }
console.log('\nRESULT: PASS — every exercised failure path reaches the shared surface with actionable text');
process.exit(0);
