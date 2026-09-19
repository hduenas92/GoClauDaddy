/**
 * focus-check.mjs — focus ownership, accessible names, and keyboard reachability.
 *
 * ORIGINAL SCOPE (cases 1-2), unchanged:
 *   1. The composer must autofocus so a new conversation is typeable with no click.
 *   2. The onboarding tour must own focus while it is open, or typing during the
 *      tour lands in the textarea hidden behind it.
 *
 * EXTENDED for task 2.10, because task 2.7's acceptance is unmeetable until this
 * instrument can measure it. The previous version had two cases and asserted
 * none of: accessible names, focus traps, or .tp-card activation.
 *
 * WHY THESE ASSERTIONS ARE EXPECTED TO FAIL ON ARRIVAL. The focus traps do not
 * exist yet — building them IS task 2.7. Watching the trap assertions fail
 * BEFORE the trap is written is the evidence that they are capable of failing.
 * An assertion first observed in its passing state is not evidence of anything.
 * A red result here is this task's deliverable.
 *
 * NO ELEMENT-COUNT ASSERTIONS (§2.2). Counts drift between runs on a live app
 * holding real data, and no count assertion can distinguish drift from a
 * regression. Everything below is a per-element invariant or a zero-violation
 * sweep. The one count that IS checked is a non-empty-set guard, which fails
 * when the set is empty rather than passing over nothing.
 *
 * Uses an isolated browser context per case — sharing one leaks `gca_onboarded`
 * between cases and makes assertions vacuously pass.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE (a case could not be exercised)
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

const add = (label, state, expect, detail) => results.push({ label, state, expect, detail });

// Anything a keyboard user can land on. `[tabindex="-1"]` is deliberately
// excluded: it is focusable by script but not by Tab, so it is not part of the
// sequence a trap has to contain.
const FOCUSABLE =
  'button, a[href], input:not([type=hidden]), select, textarea, [tabindex]:not([tabindex="-1"])';

async function newPage({ onboarded = true } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript((seen) => {
    try {
      if (seen) localStorage.setItem('gca_onboarded', '1');
      else localStorage.removeItem('gca_onboarded');
    } catch {}
  }, onboarded);
  const page = await ctx.newPage();
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  return { ctx, page };
}

// ---------------------------------------------------------------------------
// Case 1 — returning user, no tour. Composer must autofocus.
// ---------------------------------------------------------------------------
{
  const { ctx, page } = await newPage({ onboarded: true });
  const activeId = await page.evaluate(() => document.activeElement?.id ?? '');
  add('composer autofocuses for a returning user',
      activeId === 'composer-input' ? 'PASS' : 'FAIL',
      'composer-input has focus',
      `active: ${activeId || '(none)'}`);
  await ctx.close();
}

// ---------------------------------------------------------------------------
// Case 2 — first-time user. The tour must own focus, not the composer.
// ---------------------------------------------------------------------------
{
  const { ctx, page } = await newPage({ onboarded: false });
  const r = await page.evaluate(() => ({
    present: !!document.querySelector('.ob-overlay'),
    owns: !!document.activeElement?.closest?.('.ob-overlay'),
    activeId: document.activeElement?.id ?? '',
  }));
  add('onboarding tour owns focus while open',
      !r.present ? 'INCONCLUSIVE' : (r.owns ? 'PASS' : 'FAIL'),
      'focus is inside .ob-overlay, not the composer',
      r.present ? `owns: ${r.owns}, active: ${r.activeId || '(unnamed)'}` : 'tour did not render');
  await ctx.close();
}

// ---------------------------------------------------------------------------
// Overlay battery — names, non-empty set, and the tab trap.
// ---------------------------------------------------------------------------

/**
 * @param {string} label      human name for the overlay
 * @param {string} selector   the overlay's root selector
 * @param {(page) => Promise<void>} open  drives the UI to open it
 * @param {boolean} onboarded seed state before opening
 */
async function overlayBattery(label, selector, open, { onboarded = true } = {}) {
  const { ctx, page } = await newPage({ onboarded });
  try {
    await open(page);
    const appeared = await page.waitForSelector(selector, { state: 'visible', timeout: 5000 }).catch(() => null);
    if (!appeared) {
      add(`${label}: accessible names`, 'INCONCLUSIVE', 'every control has a name', 'overlay never opened');
      add(`${label}: tab trap`, 'INCONCLUSIVE', 'Tab from the last control stays inside', 'overlay never opened');
      return;
    }

    // --- Non-empty-set guard, FIRST (§2.2). A name sweep over zero controls
    // reports a clean pass having measured nothing. That exact failure mode has
    // already cost this project a session, so it fails loudly instead.
    const unnamed = await page.evaluate(({ sel, foc }) => {
      const root = document.querySelector(sel);
      if (!root) return { total: 0, offenders: [] };
      const els = [...root.querySelectorAll(foc)];
      const name = (el) =>
        (el.getAttribute('aria-label') || el.getAttribute('title') ||
         el.textContent?.trim() || el.getAttribute('placeholder') || '').trim();

      // A name made only of symbols is NOT an accessible name. `<button>✕</button>`
      // announces as "multiplication X" or as nothing at all, which tells a screen
      // reader user less than silence would. The first version of this sweep
      // accepted any non-empty string and therefore passed `.tp-close` and
      // `.shortcuts-close` — the two controls the plan explicitly records as
      // unnamed. An instrument that disagrees with a known defect is wrong.
      const hasWords = (s) => /[\p{L}\p{N}]/u.test(s);

      const describe = (el) =>
        `<${el.tagName.toLowerCase()}>${el.className ? '.' + String(el.className).split(' ')[0] : ''}`;

      return {
        total: els.length,
        offenders: els.filter((el) => !hasWords(name(el))).map((el) => {
          const n = name(el);
          return describe(el) + (n ? ` (symbol-only: "${n}")` : ' (no name at all)');
        }),
      };
    }, { sel: selector, foc: FOCUSABLE });

    if (unnamed.total === 0) {
      add(`${label}: accessible names`, 'FAIL',
          'the overlay exposes at least one focusable control',
          'ZERO focusable controls found — a name sweep here would pass vacuously');
    } else {
      add(`${label}: accessible names`,
          unnamed.offenders.length === 0 ? 'PASS' : 'FAIL',
          'every focusable control has a computed accessible name',
          unnamed.offenders.length === 0
            ? `${unnamed.total} control(s) swept, all named`
            : `${unnamed.offenders.length} of ${unnamed.total} unnamed: ${unnamed.offenders.join(', ')}`);
    }

    // --- Tab trap. Focus the LAST focusable in the overlay, press Tab, and
    // assert focus is still inside. Without a trap, Tab walks out into the page
    // behind — where a keyboard user is now operating controls they cannot see,
    // under a modal they believe has their attention.
    if (unnamed.total > 0) {
      await page.evaluate(({ sel, foc }) => {
        const root = document.querySelector(sel);
        const els = [...root.querySelectorAll(foc)];
        els[els.length - 1]?.focus();
      }, { sel: selector, foc: FOCUSABLE });

      const before = await page.evaluate((sel) => !!document.activeElement?.closest?.(sel), selector);
      await page.keyboard.press('Tab');
      await page.waitForTimeout(150);
      const after = await page.evaluate((sel) => ({
        inside: !!document.activeElement?.closest?.(sel),
        where: document.activeElement?.id
          ? '#' + document.activeElement.id
          : (document.activeElement?.className
              ? '.' + String(document.activeElement.className).split(' ')[0]
              : document.activeElement?.tagName?.toLowerCase() ?? '(none)'),
      }), selector);

      if (!before) {
        add(`${label}: tab trap`, 'INCONCLUSIVE',
            'Tab from the last control keeps focus inside the overlay',
            'could not place focus inside the overlay to begin with');
      } else {
        add(`${label}: tab trap`, after.inside ? 'PASS' : 'FAIL',
            'Tab from the last control keeps focus inside the overlay',
            after.inside ? 'focus stayed inside' : `focus escaped to ${after.where}`);
      }
    }
  } finally {
    await ctx.close();
  }
}

await overlayBattery('modal (.modal-overlay)', '.modal-overlay', async (page) => {
  const btn = await page.$('#new-project-btn');
  if (btn) await btn.click();
});

await overlayBattery('onboarding tour (.ob-overlay)', '.ob-overlay', async () => {
  // Rendered by boot when gca_onboarded is unset — nothing to click.
}, { onboarded: false });

await overlayBattery('template picker (.tp-overlay)', '.tp-overlay', async (page) => {
  await page.keyboard.press('Control+Shift+T');
});

// The shortcuts overlay is built into index.html and toggled by "?" — the plan
// records its close button (main.js:145) as unnamed, so it must be swept too.
// It is `hidden` rather than absent, which is why the battery waits for
// visibility rather than mere presence.
await overlayBattery('shortcuts (#shortcuts-overlay)', '#shortcuts-overlay:not([hidden])', async (page) => {
  const btn = await page.$('#shortcuts-btn');
  if (btn) await btn.click();
});

// ---------------------------------------------------------------------------
// The directory field's Browse button (task 4-D6). showModal gained an element,
// so this instrument has to know about it.
//
// The overlay battery above already sweeps it for a name and for the tab trap,
// because it is a <button> inside .modal-overlay. What the battery cannot see
// is ORDER: a Browse button that renders before its input, or outside the
// label's row, still passes every sweep while sending the keyboard user past
// the control they were about to fill in. Order is the property that is
// specific to this field, so it is asserted specifically.
//
// THIS CASE NEVER CLICKS OR ACTIVATES BROWSE. focus-check does not intercept
// routes, so an activation here would hit the real endpoint and open a native
// folder dialog on whatever machine is running the server — a window Playwright
// cannot see or dismiss, which would hang this harness until a human closed it.
// Focus only. Activation belongs to dir-picker-check.mjs, where every route is
// intercepted.
// ---------------------------------------------------------------------------
{
  const { ctx, page } = await newPage({ onboarded: true });
  try {
    const btn = await page.$('#new-project-btn');
    if (btn) await btn.click();
    const appeared = await page.waitForSelector('.modal-overlay .modal-dir-field', { state: 'visible', timeout: 5000 })
      .catch(() => null);
    if (!appeared) {
      add('modal directory field: Browse follows its input in the tab order', 'INCONCLUSIVE',
          'Tab from the directory input lands on the Browse button',
          'the create modal rendered no .modal-dir-field');
    } else {
      const shape = await page.evaluate(() => {
        const field = document.querySelector('.modal-overlay .modal-dir-field');
        return {
          inputs: field.querySelectorAll('.modal-input').length,
          buttons: field.querySelectorAll('.modal-browse').length,
          // A Browse button nested INSIDE the <label> becomes part of that
          // label's activation target, so clicking it can focus the input
          // instead of opening the dialog.
          insideLabel: !!field.querySelector('label .modal-browse'),
        };
      });
      if (shape.inputs !== 1 || shape.buttons !== 1) {
        add('modal directory field: Browse follows its input in the tab order', 'FAIL',
            'exactly one input and one Browse button in the field',
            `found ${shape.inputs} input(s) and ${shape.buttons} Browse button(s) — ` +
            'a tab-order assertion over the wrong number of controls proves nothing');
      } else {
        await page.focus('.modal-overlay .modal-dir-field .modal-input');
        await page.keyboard.press('Tab');
        await page.waitForTimeout(150);
        const landed = await page.evaluate(() => ({
          onBrowse: document.activeElement?.classList?.contains('modal-browse') ?? false,
          inside: !!document.activeElement?.closest?.('.modal-overlay'),
          where: document.activeElement?.className || document.activeElement?.tagName || '(none)',
        }));
        add('modal directory field: Browse follows its input in the tab order',
            landed.onBrowse && landed.inside && !shape.insideLabel ? 'PASS' : 'FAIL',
            'Tab from the directory input lands on the Browse button, inside the overlay',
            `landedOn=${JSON.stringify(landed.where)} insideOverlay=${landed.inside} ` +
            `nestedInsideLabel=${shape.insideLabel} (nesting must be false)`);
      }
    }
  } finally {
    await ctx.close();
  }
}

// ---------------------------------------------------------------------------
// .tp-card activation — it is role="button" tabindex="0" with no keydown, so a
// keyboard user can focus a control that looks actionable and does nothing.
// Enter AND Space are both required: a native <button> fires on both, and
// role="button" is a promise to behave like one.
// ---------------------------------------------------------------------------
for (const key of ['Enter', 'Space']) {
  const { ctx, page } = await newPage({ onboarded: true });
  try {
    await page.keyboard.press('Control+Shift+T');
    const ok = await page.waitForSelector('.tp-overlay', { state: 'visible', timeout: 5000 }).catch(() => null);
    if (!ok) {
      add(`.tp-card responds to ${key}`, 'INCONCLUSIVE', 'activating a card selects its template', 'template picker did not open');
      continue;
    }
    const card = await page.$('.tp-card');
    if (!card) {
      add(`.tp-card responds to ${key}`, 'INCONCLUSIVE', 'activating a card selects its template', 'no .tp-card rendered — no templates exist');
      continue;
    }
    await card.focus();
    await page.keyboard.press(key);
    await page.waitForTimeout(400);

    // Selecting a template closes the picker and puts text in the composer.
    // Either signal is sufficient; requiring both would couple this assertion
    // to onSelect's current implementation rather than to the behaviour.
    const state = await page.evaluate(() => ({
      overlayGone: !document.querySelector('.tp-overlay'),
      composerText: (document.getElementById('composer-input')?.value ?? '').length,
    }));
    const activated = state.overlayGone || state.composerText > 0;
    add(`.tp-card responds to ${key}`, activated ? 'PASS' : 'FAIL',
        'activating a focused card selects its template',
        `overlay closed: ${state.overlayGone}, composer chars: ${state.composerText}`);
  } finally {
    await ctx.close();
  }
}

// ---------------------------------------------------------------------------
await browser.close();

console.log('\n=== focus-check ===\n');
for (const r of results) {
  console.log(`${r.state.padEnd(13)} ${r.label}`);
  console.log(`              expected: ${r.expect}`);
  console.log(`              ${r.detail}`);
  console.log();
}

if (results.length === 0) {
  console.log('RESULT: FAIL — no case executed; this run asserted nothing.');
  process.exit(1);
}

const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);

if (bad.length) {
  console.log(`\nRESULT: FAIL — ${bad.length}`);
  console.log('If the failures are tab traps, that is EXPECTED until task 2.7 builds them.');
  console.log('Seeing them fail first is what proves these assertions can fail at all.');
  process.exit(1);
}
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length} case(s) not exercised`); process.exit(2); }
console.log('\nRESULT: PASS');
process.exit(0);
