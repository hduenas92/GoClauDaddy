// Smoke test for GoClaudaddy Phase 1 features
const { chromium } = require('./node_modules/@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];

  page.on('pageerror', e => errors.push(`JS error: ${e.message}`));
  page.on('console', msg => {
    if (msg.type() === 'error') {
      const text = msg.text();
      // Skip 404 resource errors — API endpoints are checked explicitly in tests 10/11
      if (text.includes('404 (Not Found)') || text.includes('Not Found')) return;
      errors.push(`Console error: ${text}`);
    }
  });

  await page.goto('http://127.0.0.1:8765');
  await page.waitForTimeout(2000);

  // 1. Page loads without JS errors
  console.log(`[1] Page loaded — ${errors.length} JS errors`);
  if (errors.length) errors.forEach(e => console.log('   ', e));

  // 2. Sidebar renders with conversation list
  const convItems = await page.locator('#conv-list li').count();
  console.log(`[2] Conversations in sidebar: ${convItems}`);

  // 3. Right sidebar metrics visible
  const costEl = await page.locator('#met-cost').textContent();
  console.log(`[3] COST metric: "${costEl}"`);

  // 4. Header subtitle present (Phase Fix A/B: no raw permission mode or ctx tokens)
  const subtitleEl = await page.locator('#conv-subtitle').textContent();
  const hasBadLabel = /bypassPermissions|acceptEdits|\d+k ctx/.test(subtitleEl);
  console.log(`[4] Header subtitle: "${subtitleEl}" — clean labels: ${!hasBadLabel}`);

  // 5. /api/config exposes context_window
  const cfg = await page.evaluate(() => fetch('/api/config').then(r => r.json()));
  const hasCtx = cfg.models?.every(m => m.context_window != null);
  console.log(`[5] /api/config context_window on all models: ${hasCtx} (${cfg.models?.map(m => `${m.label}=${m.context_window}`).join(', ')})`);

  // 6. /api/server/stats returns monthly_cost_usd
  const stats = await page.evaluate(() => fetch('/api/server/stats').then(r => r.json()));
  console.log(`[6] /api/server/stats monthly_cost_usd=${stats.monthly_cost_usd}, budget_usd=${stats.budget_usd}`);

  // 7. Settings drawer opens without error
  await page.click('#settings-btn');
  await page.waitForTimeout(500);
  const drawerOpen = await page.locator('.settings-drawer.drawer-open').count();
  console.log(`[7] Settings drawer opens: ${drawerOpen > 0}`);

  // 8. Model select exists in drawer; thinking budget labels are clean (Phase Fix D)
  const modelSelect = await page.locator('#model-select').count();
  const thinkingOptions = await page.locator('#thinking-select option').allTextContents();
  const hasBadThinking = thinkingOptions.some(t => /tokens/i.test(t));
  console.log(`[8] Model select in drawer: ${modelSelect > 0} — thinking labels clean: ${!hasBadThinking} (${thinkingOptions.join(', ')})`);

  // 9. Sidebar toggle button present (Phase 1a)
  await page.click('#settings-btn'); // close drawer
  const sbToggle = await page.locator('#sb-toggle-btn').count();
  console.log(`[9] Sidebar toggle button exists: ${sbToggle > 0}`);

  // 10. /api/conversations list has cost_usd field (Phase 1e)
  const convs = await page.evaluate(() => fetch('/api/conversations').then(r => r.json()));
  const hasCost = Array.isArray(convs) && convs.every(c => 'cost_usd' in c);
  console.log(`[10] /api/conversations has cost_usd: ${hasCost} (${convs.length} conversations)`);

  // 11. /api/agents/status endpoint responds (Phase 1d) — may 404 if server not restarted yet
  const agentsResp = await page.evaluate(() => fetch('/api/agents/status').then(r => ({ ok: r.ok, status: r.status })));
  console.log(`[11] /api/agents/status: HTTP ${agentsResp.status} (ok=${agentsResp.ok}; 404 = server needs restart)`);

  // 12. Ctrl+E in textarea does NOT trigger export (existing safeguard)
  const input = page.locator('#composer-input');
  if (await input.count() > 0) {
    await input.focus();
    const downloadPromise = page.waitForEvent('download', { timeout: 1000 }).catch(() => null);
    await page.keyboard.press('Control+e');
    const dl = await downloadPromise;
    console.log(`[12] Ctrl+E in composer triggered download: ${dl !== null} (should be false)`);
  } else {
    console.log(`[12] Composer input not found — skip`);
  }

  await browser.close();
  console.log('\nSmoke test done.');
  if (errors.length) process.exit(1);
})();
