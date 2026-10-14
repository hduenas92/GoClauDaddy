// WP-AP J4 frozen oracle (Opus, 2026-10-13): the approval card, the 4-option permission-mode picker with the CLI's
// effective-mode line, and the Always-allow rules list in Settings. Real isolated clone server (GCA_URL) for static files,
// /api/config and conversations; the chat WebSocket is MOCKED (page.routeWebSocket: no CLI, no :8765) and the rules REST is
// route-intercepted. Every check prints exactly one "PASS <id>" or "FAIL <id>" line, also on a base without J4 (each step
// catches its own errors), then "CHECKS_REACHED <n>". Exit 0 = all pass.
import { chromium } from 'playwright';

const URL = process.env.GCA_URL;
if (!URL || URL.includes(':8765')) { console.log('FAIL refusing: need an isolated GCA_URL'); process.exit(1); }
const results = new Map();
const ok = (id, cond, detail = '') => {
  if (results.has(id)) return;
  results.set(id, !!cond);
  console.log(`${cond ? 'PASS' : 'FAIL'} ${id}${detail ? ' :: ' + String(detail).slice(0, 300) : ''}`);
};
const step = async (ids, fn) => {
  try { await fn(); } catch (e) { for (const id of ids) ok(id, false, 'step error: ' + (e?.message || e)); }
  for (const id of ids) if (!results.has(id)) ok(id, false, 'not asserted');
};

let RULES = [];
const deletes = [], patches = [];
let ws = null;
const sent = [];
const sentOf = (type) => sent.filter((f) => f.type === type);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(() => {
  try { localStorage.setItem('gca_onboarded', '1'); localStorage.removeItem('gca_feat_approval'); } catch { /* blocked */ }
});
await page.route('**/api/permissions/rules**', async (r) => {
  const m = r.request().method();
  const p = new globalThis.URL(r.request().url()).pathname;
  if (m === 'GET' && p === '/api/permissions/rules') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RULES) });
  const mm = /^\/api\/permissions\/rules\/([^/]+)$/.exec(p);
  if (m === 'DELETE' && mm) {
    const id = decodeURIComponent(mm[1]);
    deletes.push(id);
    RULES = RULES.filter((x) => x.id !== id);
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ revoked: true }) });
  }
  return r.continue();
});
await page.route('**/api/conversations/*/settings', async (r) => {
  if (r.request().method() === 'PATCH') { try { patches.push(JSON.parse(r.request().postData() || '{}')); } catch { patches.push(null); } }
  return r.continue();
});
await page.routeWebSocket('**/ws/chat/*', (route) => {
  ws = route;
  route.onMessage((m) => { try { sent.push(JSON.parse(typeof m === 'string' ? m : m.toString())); } catch { sent.push({ type: '?' }); } });
});

const push = (o) => ws.send(JSON.stringify(o));
const vis = (sel) => page.locator(sel).first().isVisible().catch(() => false);
const txt = (sel) => page.locator(sel).first().textContent({ timeout: 1500 }).then((t) => (t || '').trim()).catch(() => null);
const req = (id, extra = {}) => ({ type: 'approval_needed', request_id: id, tool: 'Bash', action: 'npm test', input: { command: 'npm test' },
  tool_use_id: 'tu_' + id, cwd: 'C:\\work\\proj', can_always: true, always_pattern: 'npm test', timeout: 60, remaining: 60, ...extra });
const cardUp = () => page.waitForSelector('#approval-allow-once-btn', { state: 'visible', timeout: 3000 }).then(() => true).catch(() => false);
const gone = async (sel) => { for (let i = 0; i < 20; i++) { if (!(await vis(sel))) return true; await page.waitForTimeout(100); } return false; };

// ---- boot: a chat with no permission mode ----
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForSelector('#composer-input', { timeout: 15000 });
for (let i = 0; i < 50 && !ws; i++) await page.waitForTimeout(100);
const convId = await page.evaluate(async () => (await (await fetch('/api/conversations')).json())[0]?.id);
const conv0 = await page.evaluate(async (id) => (await (await fetch('/api/conversations/' + id)).json()).conversation, convId);
if (conv0 && conv0.permission_mode !== null) {
  await page.evaluate(async (id) => fetch('/api/conversations/' + id + '/settings', { method: 'PATCH', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ clear_permission_mode: true }) }), convId);
  ws = null;
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#composer-input', { timeout: 15000 });
  for (let i = 0; i < 50 && !ws; i++) await page.waitForTimeout(100);
}
patches.length = 0;
await page.waitForTimeout(500);

// ---- A. the approval card ----
await step(['card_shows_without_flag', 'card_tool', 'card_command', 'card_cwd', 'card_three_buttons', 'no_auto_deny'], async () => {
  push(req('r1'));
  const up = await cardUp();
  ok('card_shows_without_flag', up, 'gca_feat_approval unset; card must still appear');
  ok('card_tool', /Bash/.test((await txt('[data-testid="approval-tool"]')) ?? ''), await txt('[data-testid="approval-tool"]'));
  ok('card_command', (await txt('[data-testid="approval-command"]')) === 'npm test', await txt('[data-testid="approval-command"]'));
  ok('card_cwd', ((await txt('[data-testid="approval-cwd"]')) ?? '').includes('C:\\work\\proj'), await txt('[data-testid="approval-cwd"]'));
  const three = (await vis('#approval-allow-once-btn')) && (await vis('#approval-allow-always-btn')) && (await vis('#approval-deny-btn'));
  ok('card_three_buttons', three);
  await page.waitForTimeout(500);
  ok('no_auto_deny', sentOf('deny').length === 0 && sentOf('approve').length === 0, JSON.stringify(sent));
});
await step(['allow_once_frame', 'allow_once_closes'], async () => {
  await page.click('#approval-allow-once-btn', { timeout: 2000 });
  await page.waitForTimeout(300);
  const f = sentOf('approve');
  ok('allow_once_frame', f.length === 1 && f[0].request_id === 'r1', JSON.stringify(f));
  ok('allow_once_closes', await gone('#approval-allow-once-btn'));
});
await step(['allow_always_frame'], async () => {
  push(req('r2'));
  await cardUp();
  await page.click('#approval-allow-always-btn', { timeout: 2000 });
  await page.waitForTimeout(300);
  const f = sentOf('approve_always');
  ok('allow_always_frame', f.length === 1 && f[0].request_id === 'r2' && (await gone('#approval-allow-always-btn')), JSON.stringify(f));
});
await step(['deny_frame'], async () => {
  push(req('r3'));
  await cardUp();
  await page.click('#approval-deny-btn', { timeout: 2000 });
  await page.waitForTimeout(300);
  const f = sentOf('deny');
  ok('deny_frame', f.length === 1 && f[0].request_id === 'r3' && (await gone('#approval-deny-btn')), JSON.stringify(f));
});
await step(['no_always_when_cannot'], async () => {
  push(req('r4', { can_always: false, always_pattern: null, tool: 'Write', action: 'Write', input: { file_path: 'a.txt' } }));
  const up = await cardUp();
  ok('no_always_when_cannot', up && !(await vis('#approval-allow-always-btn')), 'can_always=false');
  push({ type: 'approval_resolved', request_id: 'r4', decision: 'deny', source: 'timeout' });
  await gone('#approval-allow-once-btn');
});
await step(['resolved_dismisses', 'resolved_other_id_keeps'], async () => {
  push(req('r5'));
  await cardUp();
  push({ type: 'approval_resolved', request_id: 'zzz', decision: 'allow', source: 'user_once' });
  await page.waitForTimeout(500);
  ok('resolved_other_id_keeps', await vis('#approval-allow-once-btn'), 'a resolve for another request must not close this card');
  const before = sent.length;
  push({ type: 'approval_resolved', request_id: 'r5', decision: 'deny', source: 'timeout' });
  ok('resolved_dismisses', (await gone('#approval-allow-once-btn')) && sent.length === before, JSON.stringify(sent.slice(before)));
});
await step(['card_escapes_html'], async () => {
  const evil = '<img src=x onerror="window.__xss=1">';
  push(req('r6', { action: evil, input: { command: evil }, cwd: '<i>c</i>' }));
  await cardUp();
  await page.waitForTimeout(300);
  const r = await page.evaluate(() => {
    const box = document.querySelector('[data-testid="approval-tool"]')?.closest('[role="dialog"]');
    return { img: box?.querySelectorAll('img').length ?? -1, i: box?.querySelectorAll('i').length ?? -1, xss: window.__xss === 1,
      cmd: document.querySelector('[data-testid="approval-command"]')?.textContent ?? null };
  });
  ok('card_escapes_html', r.img === 0 && r.i === 0 && !r.xss && r.cmd === evil, JSON.stringify(r));
  push({ type: 'approval_resolved', request_id: 'r6', decision: 'deny', source: 'timeout' });
  await gone('#approval-allow-once-btn');
});
await step(['extend_carries_request_id'], async () => {
  push(req('r7', { remaining: 15 }));
  await cardUp();
  await page.waitForSelector('#approval-need-time-btn', { state: 'visible', timeout: 3000 });
  await page.click('#approval-need-time-btn', { timeout: 2000 });
  await page.waitForTimeout(300);
  const f = sentOf('approval_extend');
  ok('extend_carries_request_id', f.length >= 1 && f[f.length - 1].request_id === 'r7', JSON.stringify(f));
  push({ type: 'approval_resolved', request_id: 'r7', decision: 'deny', source: 'timeout' });
  await gone('#approval-allow-once-btn');
});

// ---- B. the permission-mode picker + the CLI-reported effective mode ----
const openDrawer = async () => {
  if (!(await page.evaluate(() => document.getElementById('settings-drawer')?.classList.contains('drawer-open')
    ?? !!document.querySelector('.drawer-open #permission-select')))) await page.click('#settings-btn');
  await page.waitForSelector('#permission-select', { state: 'visible', timeout: 3000 });
};
const reopenDrawer = async () => {
  await page.click('#settings-btn');
  await page.waitForTimeout(300);
  await openDrawer();
  await page.waitForTimeout(500);
};
await step(['select_four_options', 'select_no_blank', 'select_null_is_manual', 'feat_approval_gone'], async () => {
  await openDrawer();
  const s = await page.evaluate(() => {
    const el = document.getElementById('permission-select');
    return { values: [...el.options].map((o) => o.value), value: el.value, feat: document.querySelectorAll('#feat-approval').length };
  });
  ok('select_four_options', JSON.stringify(s.values) === JSON.stringify(['manual', 'acceptEdits', 'plan', 'bypassPermissions']), JSON.stringify(s.values));
  ok('select_no_blank', !s.values.includes(''), JSON.stringify(s.values));
  ok('select_null_is_manual', s.value === 'manual', s.value);
  ok('feat_approval_gone', s.feat === 0, 'count=' + s.feat);
});
await step(['select_change_patches'], async () => {
  await page.selectOption('#permission-select', 'plan');
  await page.waitForTimeout(600);
  const last = patches[patches.length - 1];
  ok('select_change_patches', !!last && last.permission_mode === 'plan' && !('clear_permission_mode' in last && last.clear_permission_mode), JSON.stringify(patches));
});
let okText = null;
await step(['effective_ok'], async () => {
  push({ type: 'permission_mode', requested: 'plan', actual: 'plan', ok: true });
  await page.waitForTimeout(400);
  okText = await txt('#permission-effective');
  ok('effective_ok', !!okText && okText.includes('Plan only'), okText);
});
await step(['effective_mismatch'], async () => {
  push({ type: 'permission_mode', requested: 'acceptEdits', actual: 'default', ok: false });
  await page.waitForTimeout(400);
  const t = await txt('#permission-effective');
  ok('effective_mismatch', !!t && t.includes('default') && t.includes('Auto-approve edits') && t !== okText, t);
});

// ---- C. the Always-allow rules list ----
await step(['rules_empty'], async () => {
  RULES = [];
  await reopenDrawer();
  const n = await page.locator('[data-testid="always-rule"]').count();
  ok('rules_empty', (await vis('[data-testid="always-rules-empty"]')) && n === 0, 'items=' + n);
});
await step(['rules_listed', 'rules_escape_html'], async () => {
  RULES = [
    { id: 'rule-1', tool_name: 'Bash', pattern: 'npm test', cwd: 'C:\\work\\proj', created_at: '2026-10-13T00:00:00Z' },
    { id: 'rule-2', tool_name: 'Bash', pattern: '<b>x</b>', cwd: 'C:\\w2', created_at: '2026-10-13T00:00:01Z' },
  ];
  await reopenDrawer();
  const r = await page.evaluate(() => ({
    items: document.querySelectorAll('[data-testid="always-rule"]').length,
    patterns: [...document.querySelectorAll('[data-testid="always-rule-pattern"]')].map((e) => e.textContent.trim()),
    cwds: [...document.querySelectorAll('[data-testid="always-rule-cwd"]')].map((e) => e.textContent.trim()),
    revokes: document.querySelectorAll('[data-testid="always-rule-revoke"]').length,
    bold: document.querySelector('[data-testid="always-rules"]')?.querySelectorAll('b').length ?? -1,
  }));
  const emptyShown = await vis('[data-testid="always-rules-empty"]');
  ok('rules_listed', r.items === 2 && r.revokes === 2 && r.patterns[0] === 'npm test' && r.cwds[0].includes('C:\\work\\proj') && !emptyShown,
    JSON.stringify(r) + ' emptyShown=' + emptyShown);
  ok('rules_escape_html', r.bold === 0 && r.patterns[1] === '<b>x</b>', JSON.stringify(r));
});
await step(['revoke_calls_delete', 'revoke_rerenders'], async () => {
  await page.locator('[data-testid="always-rule-revoke"]').first().click({ timeout: 2000 });
  await page.waitForTimeout(800);
  ok('revoke_calls_delete', deletes.length === 1 && deletes[0] === 'rule-1', JSON.stringify(deletes));
  const n = await page.locator('[data-testid="always-rule"]').count();
  const left = await txt('[data-testid="always-rule-pattern"]');
  ok('revoke_rerenders', n === 1 && left === '<b>x</b>', `items=${n} first=${left}`);
});

await browser.close();
const fails = [...results.values()].filter((v) => !v).length;
console.log(`CHECKS_REACHED ${results.size}`);
console.log(fails ? `RESULT FAIL ${fails}` : 'RESULT PASS');
process.exit(fails ? 1 : 0);
