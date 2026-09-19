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
const XTERM_JS = 'https://cdnjs.cloudflare.com/ajax/libs/xterm/5.3.0/xterm.min.js';

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
// 4. Terminal open failure — xterm.js cannot load
// ---------------------------------------------------------------------------
{
  const name = '4. terminal open failure (xterm unreachable) shows a toast';
  const { ctx, page } = await freshPage({ terminal: true });
  await page.route(XTERM_JS, (r) => r.abort());
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  await page.keyboard.press('Control+`');
  await expectToast(page, name, 'terminal');
  await ctx.close();
}

// ---------------------------------------------------------------------------
// 5. Terminal start failure — session endpoint refuses
// ---------------------------------------------------------------------------
{
  const name = '5. terminal session failure shows a toast';
  const { ctx, page } = await freshPage({ terminal: true });
  // Let xterm "load" so we reach the session call, then fail the session.
  await page.route(XTERM_JS, (r) =>
    r.fulfill({ status: 200, contentType: 'application/javascript', body: 'window.Terminal=function(){this.open=function(){};this.write=function(){};this.onData=function(){};};' }));
  await page.route('**/api/terminal', (r) =>
    r.fulfill({ status: 500, contentType: 'text/plain', body: 'no pty available' }));
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);
  await page.keyboard.press('Control+`');
  await expectToast(page, name, 'terminal');
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
console.log('\nNOT COVERED, stated rather than silently omitted:');
console.log('  chat_pane.js regenerate-failure toast. Reaching it needs composerEl.send()');
console.log('  to throw, which requires a non-OPEN socket at the moment of a regenerate');
console.log('  click. That is task 2.9 territory (socket.stop()/send guards) and cannot be');
console.log('  forced deterministically until those land. Cover it with 2.9, not here.');

if (failed.length) { console.log(`\nRESULT: FAIL — ${failed.length}`); process.exit(1); }
if (skipped.length) { console.log(`\nRESULT: INCONCLUSIVE — ${skipped.length} path(s) not exercised`); process.exit(2); }
console.log('\nRESULT: PASS — every exercised failure path reaches the shared surface with actionable text');
process.exit(0);
