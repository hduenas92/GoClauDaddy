/**
 * shortcut-guard-check.mjs — task 2.6 (4-I7): global shortcuts must not fire
 * through an open overlay, and the two TOGGLES must still close their own.
 *
 * Before 2.6 only the Escape branch checked overlay state. Ctrl+Shift+N,
 * Ctrl+Shift+T, Ctrl+`, Ctrl+B and Ctrl+E all fired regardless — stacking a
 * second overlay on the first, or switching conversation mid-rename.
 *
 * WHY THE OBVIOUS FIX IS WRONG, and why this harness has 19 assertions and not
 * 15. Ctrl+, and ? are toggles: each CLOSES its own surface. A single guard
 * above the branch table makes the settings drawer and the shortcuts overlay
 * openable and then un-closable by the same key — two new dead ends traded for
 * one bug. A build that passes all 15 inert-assertions and fails the 2 toggle
 * assertions is the broken fix, and must read as FAIL, not as 88% good.
 *
 * 5 guarded combos x 3 overlays = 15, plus 2 toggle-closes, plus 2 Caps Lock
 * cases (Ctrl+B and Ctrl+K) = 19.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

// Navigation uses domcontentloaded, never networkidle. networkidle is
// documented by Playwright as discouraged and inherently racy, and it hung in a
// clean Linux container against the /api/server/logs SSE stream. It was also
// redundant here: every navigation below is followed by an explicit wait, and
// that is what actually establishes readiness.

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const results = [];
const add = (label, state, detail) => results.push({ label, state, detail });

async function newPage() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_feat_assess', '0');
      localStorage.setItem('gca_feat_terminal', '1'); // Ctrl+` is flag-gated
    } catch {}
  });
  const page = await ctx.newPage();
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  return { ctx, page };
}

// How to OPEN each overlay, and how to confirm it is actually open. Confirming
// matters: pressing a combo against an overlay that never opened would assert
// nothing and report a pass.
const OVERLAYS = {
  shortcuts: {
    open: async (page) => { const b = await page.$('#shortcuts-btn'); if (b) await b.click(); },
    isOpen: (page) => page.evaluate(() => {
      const el = document.getElementById('shortcuts-overlay');
      return !!el && !el.hidden;
    }),
  },
  drawer: {
    // Ctrl+, is gated on !inInput, and the composer autofocuses on load, so a
    // user who is not mid-sentence blurs first. Without this the drawer would
    // never open and every drawer case below would be INCONCLUSIVE.
    open: async (page) => {
      await page.evaluate(() => document.activeElement?.blur?.());
      await page.keyboard.press('Control+Comma');
    },
    isOpen: (page) => page.evaluate(() =>
      !!document.querySelector('.drawer-open')),
  },
  modal: {
    open: async (page) => { const b = await page.$('#new-project-btn'); if (b) await b.click(); },
    isOpen: (page) => page.evaluate(() => !!document.querySelector('.modal-overlay')),
  },
};

// Each guarded combo, with an OBSERVABLE effect. "Inert" means the effect did
// not happen. Effects are read as before/after deltas inside one page session,
// never as standing counts (2.2).
const COMBOS = {
  'Ctrl+Shift+N': {
    key: 'Control+Shift+N',
    probe: (page) => page.evaluate(() => document.querySelectorAll('.conv-body').length),
    changed: (a, b) => a !== b,
    describe: 'created a conversation',
  },
  'Ctrl+`': {
    key: 'Control+`',
    probe: (page) => page.evaluate(() => document.querySelectorAll('dialog').length),
    changed: (a, b) => b > a,
    describe: 'opened the terminal dialog',
  },
  'Ctrl+Shift+T': {
    key: 'Control+Shift+T',
    probe: (page) => page.evaluate(() => !!document.querySelector('.tp-overlay')),
    changed: (a, b) => !a && b,
    describe: 'opened the template picker',
  },
  'Ctrl+B': {
    key: 'Control+B',
    probe: (page) => page.evaluate(() =>
      document.getElementById('right-sidebar')?.className ?? ''),
    changed: (a, b) => a !== b,
    describe: 'toggled the right sidebar',
  },
  'Ctrl+E': {
    key: 'Control+E',
    probe: async () => 'no-download',
    changed: (_a, b) => b === 'download-fired',
    describe: 'started an export download',
  },
};

// --- Caps Lock: a browser with Caps Lock on reports e.key as the UPPERCASE
// character and getModifierState('CapsLock') === true. Playwright's
// keyboard.press() cannot express the Caps Lock modifier, so synthesize the
// exact keydown a real browser delivers: uppercase key, cancelable, dispatched
// at the element that owns the listener (document for the global shortcuts,
// the composer textarea for Ctrl+K). Returns what the handler did.
async function pressWithCapsLock(page, { key, ctrlKey = true, metaKey = false, shiftKey = false, selector = null }) {
  return page.evaluate(({ key, ctrlKey, metaKey, shiftKey, selector }) => {
    const target = selector ? document.querySelector(selector) : document;
    if (!target) return { targetFound: false, defaultPrevented: false, capsLock: false };
    const ev = new KeyboardEvent('keydown', {
      key,
      code: `Key${key.toUpperCase()}`,
      ctrlKey,
      metaKey,
      shiftKey,
      bubbles: true,
      cancelable: true,
      modifierCapsLock: true,
    });
    // Some engines ignore modifierCapsLock in the init dict. Caps Lock is ON in
    // this scenario, so guarantee what getModifierState must report.
    if (!ev.getModifierState('CapsLock')) {
      const real = ev.getModifierState.bind(ev);
      Object.defineProperty(ev, 'getModifierState', {
        value: (m) => (m === 'CapsLock' ? true : real(m)),
      });
    }
    target.dispatchEvent(ev);
    return {
      targetFound: true,
      defaultPrevented: ev.defaultPrevented,
      capsLock: ev.getModifierState('CapsLock'),
    };
  }, { key, ctrlKey, metaKey, shiftKey, selector });
}

for (const [oName, o] of Object.entries(OVERLAYS)) {
  for (const [cName, c] of Object.entries(COMBOS)) {
    const label = `${cName} is inert with the ${oName} overlay open`;
    const { ctx, page } = await newPage();
    try {
      let downloaded = false;
      page.on('download', () => { downloaded = true; });

      await o.open(page);
      await page.waitForTimeout(600);
      if (!(await o.isOpen(page))) {
        add(label, 'INCONCLUSIVE', `the ${oName} overlay did not open; nothing was asserted`);
        continue;
      }

      const before = await c.probe(page);
      await page.keyboard.press(c.key);
      await page.waitForTimeout(900);
      const after = cName === 'Ctrl+E'
        ? (downloaded ? 'download-fired' : 'no-download')
        : await c.probe(page);

      const fired = c.changed(before, after);
      // The overlay must ALSO still be open — a combo that dismissed it counts
      // as having fired, even if its own effect did not land.
      const stillOpen = await o.isOpen(page);

      if (fired) add(label, 'FAIL', `${c.describe} anyway (${JSON.stringify(before)} -> ${JSON.stringify(after)})`);
      else if (!stillOpen) add(label, 'FAIL', `the ${oName} overlay was dismissed by the combo`);
      else add(label, 'PASS', `no effect; overlay still open`);
    } finally {
      await ctx.close();
    }
  }
}

// --- The two toggles must STILL close their own surface. -------------------
{
  const label = 'Ctrl+, still closes an open settings drawer';
  const { ctx, page } = await newPage();
  try {
    // Ctrl+, is gated on !inInput and the composer autofocuses on load, so
    // blur first or the toggle can never open its own surface.
    await page.evaluate(() => document.activeElement?.blur?.());
    await page.waitForTimeout(150);
    await page.keyboard.press('Control+Comma');
    await page.waitForTimeout(600);
    const opened = await OVERLAYS.drawer.isOpen(page);
    if (!opened) add(label, 'INCONCLUSIVE', 'the drawer never opened');
    else {
      await page.keyboard.press('Control+Comma');
      await page.waitForTimeout(600);
      const closed = !(await OVERLAYS.drawer.isOpen(page));
      add(label, closed ? 'PASS' : 'FAIL',
          closed ? 'opened then closed' : 'drawer is open and Ctrl+, will not close it — DEAD END');
    }
  } finally { await ctx.close(); }
}
{
  const label = '? still closes an open shortcuts overlay';
  const { ctx, page } = await newPage();
  try {
    // The composer autofocuses on load, and the "?" branch is correctly gated on
    // !inInput — so pressing it with the textarea focused types a literal "?"
    // rather than opening anything. That is right behaviour, not a bug. Blur
    // first, the way a user who is not mid-sentence would.
    await page.evaluate(() => document.activeElement?.blur?.());
    await page.waitForTimeout(150);
    await page.keyboard.press('?');
    await page.waitForTimeout(600);
    const opened = await OVERLAYS.shortcuts.isOpen(page);
    if (!opened) add(label, 'INCONCLUSIVE', 'the shortcuts overlay never opened');
    else {
      await page.keyboard.press('?');
      await page.waitForTimeout(600);
      const closed = !(await OVERLAYS.shortcuts.isOpen(page));
      add(label, closed ? 'PASS' : 'FAIL',
          closed ? 'opened then closed' : 'overlay is open and ? will not close it — DEAD END');
    }
  } finally { await ctx.close(); }
}

// --- Caps Lock regression: with Caps Lock ON the browser reports e.key as the
// UPPERCASE letter, and these shortcuts were compared case-sensitively, so they
// silently died. Both must still fire. ----------------------------------------
{
  const label = 'Ctrl+B still fires with Caps Lock on (uppercase e.key)';
  const { ctx, page } = await newPage();
  try {
    // Ctrl+B is gated on !inInput and the composer autofocuses on load.
    await page.evaluate(() => document.activeElement?.blur?.());
    const before = await page.evaluate(() =>
      document.getElementById('right-sidebar')?.className ?? '');
    const dispatch = await pressWithCapsLock(page, { key: 'B', ctrlKey: true });
    await page.waitForTimeout(600);
    const after = await page.evaluate(() =>
      document.getElementById('right-sidebar')?.className ?? '');
    if (!dispatch.targetFound) add(label, 'INCONCLUSIVE', 'the right sidebar was not found');
    else if (dispatch.defaultPrevented && before !== after) add(label, 'PASS', `keydown was handled and the sidebar toggled (${JSON.stringify(before)} -> ${JSON.stringify(after)})`);
    else add(label, 'FAIL', `the shortcut did not fire with an uppercase e.key (defaultPrevented=${dispatch.defaultPrevented}, capsLock=${dispatch.capsLock}, ${JSON.stringify(before)} -> ${JSON.stringify(after)})`);
  } finally { await ctx.close(); }
}
{
  const label = 'Ctrl+K still fires with Caps Lock on (uppercase e.key)';
  const { ctx, page } = await newPage();
  try {
    const dispatch = await pressWithCapsLock(page, { key: 'K', ctrlKey: true, selector: '#composer-input' });
    await page.waitForTimeout(600);
    const suggestOpen = await page.evaluate(() => !!document.querySelector('.suggest-dropdown'));
    if (!dispatch.targetFound) add(label, 'INCONCLUSIVE', 'the composer input was not found');
    else if (dispatch.defaultPrevented) add(label, 'PASS', suggestOpen ? 'keydown was handled and the template autosuggest opened' : 'keydown was handled (preventDefault)');
    else add(label, 'FAIL', `the shortcut did not fire with an uppercase e.key (defaultPrevented=${dispatch.defaultPrevented}, capsLock=${dispatch.capsLock})`);
  } finally { await ctx.close(); }
}

await browser.close();

console.log('\n=== shortcut-guard-check ===\n');
for (const r of results) console.log(`${r.state.padEnd(13)} ${r.label}\n              ${r.detail}`);

if (results.length !== 19) {
  console.log(`\nRESULT: FAIL — expected 19 assertions, ran ${results.length}. The inventory and the harness disagree.`);
  process.exit(1);
}
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`\n${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · 19 assertions`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — guarded combos inert under every overlay; both toggles still close their own; Caps Lock letters fire');
process.exit(0);
