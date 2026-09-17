/**
 * focus-check.mjs — focus ownership on load.
 *
 * Two behaviors that interact and were both wrong:
 *   1. The composer must autofocus so a new conversation is typeable with no click.
 *   2. The onboarding tour must own focus while it is open, or typing during the
 *      tour lands in the textarea hidden behind it.
 *
 * Uses an isolated browser context per case — sharing one leaks `gca_onboarded`
 * between cases and makes the second assertion vacuously pass.
 */
import { chromium } from 'playwright';

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const results = [];

async function probe(label, { onboarded }) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  if (onboarded) {
    await page.addInitScript(() => { try { localStorage.setItem('gca_onboarded', '1'); } catch {} });
  }
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);
  const r = await page.evaluate(() => {
    const ae = document.activeElement;
    return {
      activeId: ae?.id ?? '',
      activeClass: typeof ae?.className === 'string' ? ae.className : '',
      activeTag: ae?.tagName ?? '',
      overlayPresent: !!document.querySelector('.ob-overlay'),
      overlayOwnsFocus: !!(ae && ae.closest && ae.closest('.ob-overlay')),
    };
  });
  await ctx.close();
  return { label, ...r, errors };
}

// Case 1 — returning user, no tour. Composer must autofocus.
const seen = await probe('returning user (tour dismissed)', { onboarded: true });
results.push({
  ...seen,
  pass: seen.activeId === 'composer-input',
  expect: 'composer-input has focus',
});

// Case 2 — first-time user, tour shows. Tour must own focus, NOT the composer.
const fresh = await probe('first-time user (tour shows)', { onboarded: false });
results.push({
  ...fresh,
  pass: fresh.overlayPresent ? fresh.overlayOwnsFocus : null,   // null = inconclusive
  expect: 'focus is inside .ob-overlay, not the composer',
});

let bad = 0, inconclusive = 0;
for (const r of results) {
  const verdict = r.pass === null ? 'INCONCLUSIVE' : r.pass ? 'PASS' : 'FAIL';
  if (r.pass === false) bad++;
  if (r.pass === null) inconclusive++;
  const active = r.activeId ? `#${r.activeId}` : (r.activeClass ? `.${r.activeClass.split(' ')[0]}` : r.activeTag || '(none)');
  console.log(`${verdict.padEnd(12)} ${r.label}`);
  console.log(`             expected: ${r.expect}`);
  console.log(`             active: ${active}   overlay: ${r.overlayPresent}   overlayOwnsFocus: ${r.overlayOwnsFocus}`);
  if (r.errors.length) console.log(`             pageerrors: ${r.errors.length} — ${r.errors[0]}`);
  console.log();
}

await browser.close();
if (bad) { console.log(`RESULT: FAIL — ${bad}`); process.exit(1); }
if (inconclusive) { console.log(`RESULT: INCONCLUSIVE — ${inconclusive} case(s) could not be exercised`); process.exit(2); }
console.log('RESULT: PASS — composer autofocuses, tour keeps focus while open');
process.exit(0);
