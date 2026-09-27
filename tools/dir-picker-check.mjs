/**
 * dir-picker-check.mjs — 4-D6. The Browse button beside a working directory.
 *
 * THE RULE THIS FILE IS BUILT AROUND, and it is not negotiable:
 * EVERY case route-intercepts POST /api/projects/browse-directory. The real
 * endpoint is NEVER called from here.
 *
 * Why: that endpoint opens a native Tkinter folder dialog on the machine
 * running the server. It is an OS window, not DOM. Playwright cannot see it,
 * cannot click it, and cannot dismiss it. A single un-intercepted call would
 * hang this harness until a human walked over to the machine and clicked
 * Cancel — and on a headless box it would instead raise TclError. There is no
 * version of "let's just try it for real" that ends well.
 *
 * So what is under test is the CLIENT's half of the contract, which is where
 * all three of the interesting mistakes live:
 *
 *   - `""` is the cancel answer, and it is falsy. An unguarded
 *     `input.value = path` wipes a path the user already had.  (case 3)
 *   - the request BLOCKS, so a second click opens a second OS window.  (case 6)
 *   - the current value is the only sensible starting folder.  (case 2)
 *
 * GET /api/projects is also intercepted, with a fixture carrying a known
 * non-empty working_dir. Two reasons: the inline-edit cases then cannot
 * pass vacuously against a project that happens to have an empty directory,
 * and nothing here touches the user's real rows.
 *
 * Isolated browser context per case — a shared context leaks modal state and
 * localStorage between cases and turns real assertions into vacuous ones.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const APP = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

const PICKED = 'C:\\picked\\by\\the\\dialog';
const SEEDED = 'C:\\typed\\by\\the\\user';
const FIXTURE_DIR = 'C:\\fixture\\project\\dir';
const BROWSE_PATH = '/api/projects/browse-directory';

const FIXTURE_PROJECT = {
  id: '11111111-2222-3333-4444-555555555555',
  name: 'Picker Fixture',
  working_dir: FIXTURE_DIR,
  system_prompt: null,
  created_at: '2026-01-01T00:00:00+00:00',
  updated_at: '2026-01-01T00:00:00+00:00',
};

const results = [];
const add = (label, state, detail = '') => {
  results.push({ label, state, detail });
  console.log(`${state.padEnd(13)} ${label}\n              ${detail}`);
};

/**
 * A case that THREW is a case that failed, and it must be reported as one.
 *
 * Found by the mutation battery, which is the point of running one: deleting
 * the directory input from the modal made `page.$eval` reject, the rejection
 * escaped every case block, and node died on the spot. The exit code was still
 * non-zero, so the mutation was technically "killed" — but the run printed a
 * stack trace instead of naming which cases noticed, and it took the whole
 * battery down with it. An instrument that crashes tells you something is
 * wrong without telling you what, which is most of the way to telling you
 * nothing.
 *
 * Guarded against double-reporting: if the case already recorded a verdict and
 * then threw during teardown, the verdict it reached stands.
 */
const failFromThrow = (label, err) => {
  if (results.some((r) => r.label === label)) return;
  add(label, 'FAIL',
      `the case threw before it could assert: ${String(err?.message ?? err).split('\n')[0]}`);
};

const browser = await chromium.launch();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * @param {(url: URL) => object} respond  what the browse route returns:
 *        { status?, body?, delayMs? }. Called per request.
 */
async function freshPage(respond) {
  const calls = [];                 // one entry per browse request, with its state
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_sb_open', '1');
      localStorage.setItem('gca_feat_assess', '0');
    } catch {}
  });
  const page = await ctx.newPage();

  // Scoped by pathname, never by an `**/api/**` glob: that glob also matches
  // the static module at /static/js/api/http.js and breaks module loading,
  // which then reads as "the feature is broken". Already cost this project a
  // debugging session once.
  await page.route('**/*', async (route) => {
    const req = route.request();
    const u = new URL(req.url());

    if (u.pathname === '/api/projects' && req.method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([FIXTURE_PROJECT]),
      });
    }

    if (u.pathname === BROWSE_PATH) {
      const entry = { url: u, initialDir: u.searchParams.get('initial_dir'), done: false };
      calls.push(entry);
      const plan = respond ? respond(u) : { body: { path: PICKED } };
      if (plan.delayMs) await sleep(plan.delayMs);
      entry.done = true;
      return route.fulfill({
        status: plan.status ?? 200,
        contentType: 'application/json',
        body: JSON.stringify(plan.body ?? { path: PICKED }),
      });
    }

    return route.continue();
  });

  // Record every toast as it is inserted. Sampling once at the end misses a
  // toast that appeared and was dismissed, and reads that as silence.
  await page.addInitScript(() => {
    window.__toasts = [];
    document.addEventListener('DOMContentLoaded', () => {
      new MutationObserver(() => {
        const t = document.querySelector('.error-toast');
        if (t) {
          const txt = t.innerText.trim();
          if (!window.__toasts.includes(txt)) window.__toasts.push(txt);
        }
      }).observe(document.body, { childList: true, subtree: true });
    });
  });

  await page.goto(APP, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  return { ctx, page, calls };
}

// --- openers ---------------------------------------------------------------

async function openCreateModal(page) {
  await page.click('#new-project-btn');
  const ok = await page.waitForSelector('.modal-overlay .modal-browse', { state: 'visible', timeout: 5000 })
    .catch(() => null);
  return Boolean(ok);
}

async function openEditForm(page) {
  const btn = await page.waitForSelector('.project-item .project-edit', { state: 'visible', timeout: 5000 })
    .catch(() => null);
  if (!btn) return false;
  await btn.click();
  const ok = await page.waitForSelector('.pef-browse-dir', { state: 'visible', timeout: 5000 }).catch(() => null);
  return Boolean(ok);
}

const dirValue = (page, sel) => page.$eval(sel, (el) => el.value);
const toasts = (page) => page.evaluate(() => window.__toasts || []);

try {
  // --- 1. create modal: the picked path lands in the input ----------------
  {
    const label = '1. Browse in the create modal puts the chosen path in the input';
    const { ctx, page, calls } = await freshPage(() => ({ body: { path: PICKED } }));
    try {
      if (!await openCreateModal(page)) {
        add(label, 'INCONCLUSIVE', 'the create modal never rendered a .modal-browse button');
      } else {
        const before = await dirValue(page, '.modal-dir-field .modal-input');
        await page.click('.modal-overlay .modal-browse');
        await page.waitForTimeout(600);
        const after = await dirValue(page, '.modal-dir-field .modal-input');
        // BOTH halves are required. "a request was made" says nothing about
        // the UI, and "the value changed" could come from anywhere.
        const ok = calls.length === 1 && after === PICKED && before !== PICKED;
        add(label, ok ? 'PASS' : 'FAIL',
            `requests=${calls.length} value ${JSON.stringify(before)} -> ${JSON.stringify(after)} ` +
            `(must become ${JSON.stringify(PICKED)}, and must not already have been it)`);
      }
    } catch (err) { failFromThrow(label, err); } finally { await ctx.close(); }
  }

  // --- 2. the current value is sent as initial_dir ------------------------
  {
    const label = '2. the value already in the box is sent as initial_dir';
    const { ctx, page, calls } = await freshPage(() => ({ body: { path: PICKED } }));
    try {
      if (!await openCreateModal(page)) {
        add(label, 'INCONCLUSIVE', 'the create modal never rendered a .modal-browse button');
      } else {
        await page.fill('.modal-dir-field .modal-input', SEEDED);
        await page.click('.modal-overlay .modal-browse');
        await page.waitForTimeout(600);
        const sent = calls[0]?.initialDir ?? null;
        add(label, calls.length === 1 && sent === SEEDED ? 'PASS' : 'FAIL',
            `requests=${calls.length} initial_dir=${JSON.stringify(sent)} ` +
            `(expected ${JSON.stringify(SEEDED)}; null means the query string was never built)`);
      }
    } catch (err) { failFromThrow(label, err); } finally { await ctx.close(); }
  }

  // --- 3. cancel leaves a NON-EMPTY existing value alone ------------------
  {
    const label = '3. cancelling the dialog does not blank the path already there';
    const { ctx, page, calls } = await freshPage(() => ({ body: { path: '' } }));
    try {
      if (!await openCreateModal(page)) {
        add(label, 'INCONCLUSIVE', 'the create modal never rendered a .modal-browse button');
      } else {
        await page.fill('.modal-dir-field .modal-input', SEEDED);
        const before = await dirValue(page, '.modal-dir-field .modal-input');
        // ANTI-VACUITY, and this is the whole reason the case is worth having:
        // on an empty field "the value is unchanged" is satisfied by code that
        // blanks it. The field MUST be non-empty before the cancel.
        if (!before) {
          add(label, 'INCONCLUSIVE',
              'could not seed a non-empty value; an empty field makes this case vacuous');
        } else {
          await page.click('.modal-overlay .modal-browse');
          await page.waitForTimeout(600);
          const after = await dirValue(page, '.modal-dir-field .modal-input');
          add(label, calls.length === 1 && after === before ? 'PASS' : 'FAIL',
              `requests=${calls.length} seeded ${JSON.stringify(before)} (non-empty: true) ` +
              `-> ${JSON.stringify(after)} after a {"path":""} response`);
        }
      }
    } catch (err) { failFromThrow(label, err); } finally { await ctx.close(); }
  }

  // --- 4. a failing request is reported, and keeps the value --------------
  {
    const label = '4. a 503 from the picker shows the reason and keeps the value';
    const SERVER_MSG = 'The folder picker needs a desktop session';
    const { ctx, page, calls } = await freshPage(() => ({
      status: 503,
      // FastAPI's own HTTPException shape. Asserting the message reaches the
      // toast is also what proves http.js reads `detail`, not just `error`.
      body: { detail: `${SERVER_MSG} on the machine running the server.` },
    }));
    try {
      if (!await openCreateModal(page)) {
        add(label, 'INCONCLUSIVE', 'the create modal never rendered a .modal-browse button');
      } else {
        await page.fill('.modal-dir-field .modal-input', SEEDED);
        await page.click('.modal-overlay .modal-browse');
        await page.waitForTimeout(800);
        const after = await dirValue(page, '.modal-dir-field .modal-input');
        const seen = await toasts(page);
        const said = seen.some((t) => t.includes(SERVER_MSG));
        const stillOpen = await page.evaluate(() => !!document.querySelector('.modal-overlay'));
        // Re-enabled, too: a button stuck in its pending state after a failure
        // means the user cannot retry.
        const enabled = await page.$eval('.modal-overlay .modal-browse', (b) => !b.disabled);
        add(label, calls.length === 1 && said && after === SEEDED && stillOpen && enabled ? 'PASS' : 'FAIL',
            `requests=${calls.length} toasts=${seen.length} carriedServerMessage=${said} ` +
            `value=${JSON.stringify(after)} modalStillOpen=${stillOpen} buttonReEnabled=${enabled}`);
      }
    } catch (err) { failFromThrow(label, err); } finally { await ctx.close(); }
  }

  // --- 5. the same thing works in the inline edit form --------------------
  {
    const label = '5. Browse in the inline project edit form does the same';
    const { ctx, page, calls } = await freshPage(() => ({ body: { path: PICKED } }));
    try {
      if (!await openEditForm(page)) {
        add(label, 'INCONCLUSIVE', 'the inline edit form never rendered a .pef-browse-dir button');
      } else {
        const before = await dirValue(page, '.pef-dir');
        await page.click('.pef-browse-dir');
        await page.waitForTimeout(600);
        const after = await dirValue(page, '.pef-dir');
        const sent = calls[0]?.initialDir ?? null;
        add(label, calls.length === 1 && after === PICKED && before === FIXTURE_DIR ? 'PASS' : 'FAIL',
            `requests=${calls.length} value ${JSON.stringify(before)} -> ${JSON.stringify(after)}, ` +
            `initial_dir=${JSON.stringify(sent)} (the form must start from the fixture's real dir)`);
      }
    } catch (err) { failFromThrow(label, err); } finally { await ctx.close(); }
  }

  // --- 6. double-click sends exactly ONE request --------------------------
  {
    const label = '6. a double-click opens one dialog, not two';
    const HOLD = 2500;
    const { ctx, page, calls } = await freshPage(() => ({ delayMs: HOLD, body: { path: PICKED } }));
    try {
      if (!await openCreateModal(page)) {
        add(label, 'INCONCLUSIVE', 'the create modal never rendered a .modal-browse button');
      } else {
        // Two SYNCHRONOUS clicks in one task. page.click() would wait for the
        // button to be enabled and so could never produce the overlap this
        // case exists to test.
        await page.$eval('.modal-overlay .modal-browse', (b) => { b.click(); b.click(); });
        await page.waitForTimeout(700);

        // ANTI-VACUITY: "exactly one request" is also satisfied by a handler
        // that ran twice in quick succession and got deduplicated by luck, or
        // by a second request that already came and went. The first request
        // must still be IN FLIGHT at the moment we count.
        const inFlight = calls.length > 0 && calls.every((c) => !c.done);
        const disabled = await page.$eval('.modal-overlay .modal-browse', (b) => b.disabled);
        if (!inFlight) {
          add(label, 'INCONCLUSIVE',
              `the held request was no longer pending when counted (calls=${calls.length}); ` +
              'the overlap this case needs never happened');
        } else {
          add(label, calls.length === 1 ? 'PASS' : 'FAIL',
              `requests while the first was still pending: ${calls.length} (must be 1), ` +
              `button disabled during the wait: ${disabled}`);
        }
        await page.waitForTimeout(HOLD);   // let it settle before the context closes
      }
    } catch (err) { failFromThrow(label, err); } finally { await ctx.close(); }
  }

  // --- 7. keyboard: Tab reaches Browse, Enter activates it ----------------
  {
    const label = '7. Browse is reachable by Tab and Enter activates it, not Save';
    const { ctx, page, calls } = await freshPage(() => ({ body: { path: PICKED } }));
    try {
      if (!await openCreateModal(page)) {
        add(label, 'INCONCLUSIVE', 'the create modal never rendered a .modal-browse button');
      } else {
        await page.focus('.modal-dir-field .modal-input');
        await page.keyboard.press('Tab');
        await page.waitForTimeout(150);
        const landed = await page.evaluate(() => ({
          onBrowse: document.activeElement?.classList?.contains('modal-browse') ?? false,
          inside: !!document.activeElement?.closest?.('.modal-overlay'),
          where: document.activeElement?.className || document.activeElement?.tagName || '(none)',
        }));
        if (!landed.onBrowse) {
          add(label, 'FAIL',
              `Tab from the directory input landed on ${JSON.stringify(landed.where)}, ` +
              'not the Browse button (tab order must be input -> Browse -> Cancel -> Confirm)');
        } else {
          await page.keyboard.press('Enter');
          await page.waitForTimeout(700);
          // The modal must NOT have submitted. showModal's Enter-to-confirm
          // handler preventDefault()s the keydown, which is exactly the default
          // action a <button> fires its click from — so without an explicit
          // exclusion for .modal-browse, Enter here creates the project instead
          // of opening the dialog.
          const stillOpen = await page.evaluate(() => !!document.querySelector('.modal-overlay'));
          const value = stillOpen ? await dirValue(page, '.modal-dir-field .modal-input') : '(modal closed)';
          add(label, calls.length === 1 && stillOpen && landed.inside && value === PICKED ? 'PASS' : 'FAIL',
              `focusStayedInOverlay=${landed.inside} requests=${calls.length} ` +
              `modalStillOpenAfterEnter=${stillOpen} value=${JSON.stringify(value)}`);
        }
      }
    } catch (err) { failFromThrow(label, err); } finally { await ctx.close(); }
  }
} finally {
  await browser.close();
  // Nothing to clean up: every mutating route was intercepted, so no real
  // project was created, edited or read.
}

console.log('\n=== dir-picker-check ===\n');
if (results.length === 0) {
  console.log('RESULT: FAIL — no case executed; this run asserted nothing.');
  process.exit(1);
}
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — Browse fills the box, cancel keeps it, and one click opens one dialog');
process.exit(0);
