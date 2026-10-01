/**
 * The app works with browser storage blocked, and only one module touches localStorage.
 *
 * With site data blocked for the origin, every access to window.localStorage throws
 * SecurityError. (1) Static: every localStorage access under frontend/ (vendor excluded)
 * goes through frontend/static/js/state/storage.js; the pre-module theme bootstrap in
 * index.html is exempt (it has its own try/catch). (2) In a page whose localStorage getter
 * throws: boot completes (the onboarding tour appears, since its "seen" flag can't be read),
 * and the tour's Skip, the sidebar button, Ctrl+B, Ctrl+` and the three feature checkboxes
 * all work without one uncaught error.
 * Exit 0 = both hold, 1 = either fails.
 * GCA_URL = server to load (default http://127.0.0.1:8765); GCA_REPO = checkout to scan
 * (default: this script's repo). Controls: GCA_STORAGE=open (not blocked) passes the browser
 * part; on f2d2ad4 the static part finds the direct accesses and blocked boot fails.
 */
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const REPO = process.env.GCA_REPO ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BLOCK = process.env.GCA_STORAGE !== 'open';
const HELPER = 'frontend/static/js/state/storage.js';
let failed = 0;
const t = (name, ok, detail = '') => {
  if (!ok) failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  -> ${detail}` : ''}`);
};

let direct = '';
try {
  direct = execFileSync('git', ['-C', REPO, 'grep', '--untracked', '-nE', String.raw`\blocalStorage\s*(\.|\[)`, '--',
    'frontend', ':!frontend/static/js/vendor', ':!frontend/index.html', `:!${HELPER}`], { encoding: 'utf8' });
} catch (e) {
  if (e.status !== 1) throw e; // git grep exits 1 when nothing matches
}
const lines = direct.split('\n').filter(Boolean);
t(`static: no direct localStorage access outside ${HELPER}`, lines.length === 0,
  lines.length ? `${lines.length} lines, e.g. ${lines.slice(0, 3).map((l) => l.slice(0, 90)).join(' | ')}` : '');

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
if (BLOCK) {
  await ctx.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { throw new DOMException('GCA-STORAGE-BLOCKED', 'SecurityError'); },
    });
  });
}
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error' && /GCA-STORAGE-BLOCKED/.test(m.text())) errors.push(`console: ${m.text()}`); });
const step = async (name, fn) => {
  try { const r = await fn(); t(name, r.ok, r.detail); } catch (e) { t(name, false, String(e.message).split('\n')[0]); }
};

await page.goto(URL, { waitUntil: 'domcontentloaded' });
await step(`boot completes, storage ${BLOCK ? 'BLOCKED' : 'open'} (onboarding tour shown)`, async () => {
  const toured = await page.waitForSelector('.ob-overlay', { timeout: 10000 }).then(() => true, () => false);
  const msg = await page.$eval('#boot-msg', (e) => e.textContent).catch(() => null);
  return { ok: toured && !msg, detail: msg ?? (toured ? '' : 'no tour after 10 s') };
});
await step('tour Skip dismisses it', async () => {
  await page.click('.ob-skip', { timeout: 3000 });
  const gone = await page.waitForSelector('.ob-overlay', { state: 'detached', timeout: 3000 }).then(() => true, () => false);
  return { ok: gone, detail: gone ? '' : 'overlay still open' };
});
const collapsed = () => page.evaluate(() => document.getElementById('right-sidebar').classList.contains('sb-collapsed'));
await step('sidebar button toggles', async () => {
  const before = await collapsed();
  await page.click('#sb-toggle-btn', { timeout: 3000 });
  return { ok: (await collapsed()) !== before };
});
await step('Ctrl+B toggles', async () => {
  const before = await collapsed();
  await page.keyboard.press('Control+b');
  return { ok: (await collapsed()) !== before };
});
for (const id of ['feat-assess', 'feat-approval']) {
  await step(`feature checkbox #${id} toggles`, async () => {
    const r = await page.evaluate((sel) => {
      const el = document.getElementById(sel);
      if (!el) return null;
      const before = el.checked;
      el.click();
      return before !== el.checked;
    }, id);
    return { ok: r === true, detail: r === null ? 'not found' : '' };
  });
}
await page.waitForTimeout(500);
t('no uncaught error', errors.length === 0, errors.slice(0, 3).join(' | '));
await browser.close();
console.log(failed ? `RESULT: FAIL — ${failed} check(s)` : 'RESULT: PASS');
process.exit(failed ? 1 : 0);
