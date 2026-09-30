/**
 * Right sidebar starts collapsed on narrow windows without losing the user's choice.
 *
 * Below 1280 px the sidebar must start collapsed even if the user last left it
 * open; at >= 1280 px the saved choice (gca_sb_open) is honoured; the saved
 * value itself must never be rewritten just by loading the page narrow.
 * Exit 0 = every case passes, 1 = any case fails.
 */
import { chromium } from 'playwright';

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const cases = [
  { name: 'narrow 1024, saved open  -> collapsed', width: 1024, saved: '1', collapsed: true },
  { name: 'narrow 1279, saved open  -> collapsed', width: 1279, saved: '1', collapsed: true },
  { name: 'wide 1280, saved open    -> open', width: 1280, saved: '1', collapsed: false },
  { name: 'wide 1440, saved open    -> open', width: 1440, saved: '1', collapsed: false },
  { name: 'wide 1440, saved closed  -> collapsed', width: 1440, saved: '0', collapsed: true },
  { name: 'wide 1440, nothing saved -> collapsed', width: 1440, saved: null, collapsed: true },
];

const browser = await chromium.launch();
let failed = 0;
for (const c of cases) {
  const ctx = await browser.newContext({ viewport: { width: c.width, height: 900 } });
  const page = await ctx.newPage();
  await page.addInitScript((saved) => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      if (saved === null) localStorage.removeItem('gca_sb_open');
      else localStorage.setItem('gca_sb_open', saved);
    } catch {}
  }, c.saved);
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#right-sidebar', { state: 'attached', timeout: 8000 });
  await page.waitForTimeout(300);
  const got = await page.evaluate(() => ({
    collapsed: document.getElementById('right-sidebar').classList.contains('sb-collapsed'),
    saved: localStorage.getItem('gca_sb_open'),
  }));
  const ok = got.collapsed === c.collapsed && got.saved === c.saved;
  if (!ok) failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${c.name}  -> collapsed=${got.collapsed} saved=${JSON.stringify(got.saved)}`);
  await ctx.close();
}
await browser.close();
console.log(failed ? `RESULT: FAIL — ${failed} of ${cases.length}` : `RESULT: PASS — ${cases.length} cases`);
process.exit(failed ? 1 : 0);
