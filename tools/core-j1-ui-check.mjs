// RESTORATION CORE J1 frozen oracle, UI half (Opus, 2026-10-14). Real isolated clone server (GCA_URL) for static files,
// /api/config and conversations; the chat WebSocket is MOCKED (page.routeWebSocket: no CLI, never :8765). Every check prints
// exactly one "PASS <id>" or "FAIL <id>" line, also on a base without the fixes (each step catches its own errors), then
// "CHECKS_REACHED <n>". Exit 0 = all pass.
//
// CONTRACT (frozen; the reference fix and the build must satisfy exactly this):
//  1 ExitPlanMode card. Frame: approval_needed {tool:'ExitPlanMode', action:'ExitPlanMode', input:{plan:<text>}, can_always:false}.
//    The dialog shows the plan text in [data-testid="approval-plan"]; the visible dialog text never contains "ExitPlanMode";
//    buttons: #approval-allow-once-btn and #approval-deny-btn only (no #approval-allow-always-btn); Allow once sends exactly one
//    {type:'approve', request_id}; the plan is rendered as text (HTML-escaped). After the plan is allowed the broker sends
//    {type:'turn_mode', mode:'acceptEdits', source:'plan_approved'}; the UI shows [data-testid="turn-mode-notice"] (text matches
//    /edits/i and /this turn/i) while the turn runs and removes it on done / stopped / error.
//  3 permission_auto_allowed frame -> the text "Allowed by a saved rule" is still visible after text + done frames.
//  4 For every mode offered by #permission-select, the header subtitle (#conv-subtitle) shows the picker option's own label.
//  5 A persisted assistant message whose tool call has no output is labelled "Interrupted" (never "Running…") after reload;
//    a tool call streamed live in the current turn still says "Running…" (control).
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

let ws = null;
const sent = [];
const sentOf = (type) => sent.filter((f) => f.type === type);
let injectHistory = null; // set per scenario: tool_calls JSON for a synthetic assistant message

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(() => { try { localStorage.setItem('gca_onboarded', '1'); } catch { /* blocked */ } });
await page.route('**/api/conversations/*', async (r) => {
  const u = new globalThis.URL(r.request().url());
  if (r.request().method() !== 'GET' || !/^\/api\/conversations\/[^/]+$/.test(u.pathname) || !injectHistory) return r.continue();
  const resp = await r.fetch();
  const j = await resp.json();
  j.messages = [
    { id: 9001, role: 'user', content: 'run it', created_at: '2026-10-14T00:00:00Z', attachments: [] },
    { id: 9002, role: 'assistant', content: 'Working on it.', created_at: '2026-10-14T00:00:01Z', attachments: [],
      tool_calls: JSON.stringify([{ id: 'toolu_int', name: 'Bash', input: { command: 'sleep 60' } }]) },
  ];
  return r.fulfill({ response: resp, body: JSON.stringify(j), contentType: 'application/json' });
});
await page.routeWebSocket('**/ws/chat/*', (route) => {
  ws = route;
  route.onMessage((m) => { try { sent.push(JSON.parse(typeof m === 'string' ? m : m.toString())); } catch { sent.push({ type: '?' }); } });
});
const boot = async () => {
  ws = null;
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#composer-input', { timeout: 15000 });
  for (let i = 0; i < 50 && !ws; i++) await page.waitForTimeout(100);
};
const push = (o) => ws.send(JSON.stringify(o));
const vis = (sel) => page.locator(sel).first().isVisible().catch(() => false);
const txt = (sel) => page.locator(sel).first().textContent({ timeout: 1500 }).then((t) => (t || '').trim()).catch(() => null);
const gone = async (sel) => { for (let i = 0; i < 20; i++) { if (!(await vis(sel))) return true; await page.waitForTimeout(100); } return false; };
const dialogText = () => page.evaluate(() => {
  const b = document.querySelector('#approval-allow-once-btn')?.closest('[role="dialog"]');
  return b ? b.innerText : null;
});
const PLAN = '1. Create p.txt in the project folder\n2. Write the word hello into it';
const planReq = (id, extra = {}) => ({ type: 'approval_needed', request_id: id, tool: 'ExitPlanMode', action: 'ExitPlanMode',
  input: { plan: PLAN }, tool_use_id: 'tu_' + id, cwd: 'C:\\work\\proj', can_always: false, always_pattern: null, timeout: 60, remaining: 60, ...extra });

await boot();
const convId = await page.evaluate(async () => (await (await fetch('/api/conversations')).json())[0]?.id);

// ---- 1. the ExitPlanMode card ----
await step(['plan_card_shows_plan', 'plan_card_no_raw_tool_name', 'plan_card_no_always', 'plan_card_one_decision', 'plan_card_escapes_html'], async () => {
  push(planReq('p1'));
  const up = await page.waitForSelector('#approval-allow-once-btn', { state: 'visible', timeout: 3000 }).then(() => true).catch(() => false);
  const planTxt = (await txt('[data-testid="approval-plan"]')) ?? '';
  ok('plan_card_shows_plan', up && planTxt.includes('Create p.txt in the project folder') && planTxt.includes('hello'), planTxt || 'no [data-testid=approval-plan]');
  const dt = (await dialogText()) ?? '';
  ok('plan_card_no_raw_tool_name', up && dt.length > 0 && !/ExitPlanMode/i.test(dt), dt.slice(0, 200));
  ok('plan_card_no_always', up && !(await vis('#approval-allow-always-btn')) && (await vis('#approval-deny-btn')), 'always visible=' + (await vis('#approval-allow-always-btn')));
  await page.click('#approval-allow-once-btn', { timeout: 2000 });
  await page.waitForTimeout(300);
  const f = sentOf('approve');
  ok('plan_card_one_decision', f.length === 1 && f[0].request_id === 'p1' && sentOf('deny').length === 0 && sentOf('approve_always').length === 0 && (await gone('#approval-allow-once-btn')), JSON.stringify(sent));
  push({ type: 'approval_resolved', request_id: 'p1', decision: 'allow', source: 'user_once' });
  // html escaping of the plan text (own card, then resolved)
  const evil = '<img src=x onerror="window.__xss=1">';
  push(planReq('p2', { input: { plan: evil } }));
  await page.waitForSelector('#approval-allow-once-btn', { state: 'visible', timeout: 3000 }).catch(() => {});
  await page.waitForTimeout(200);
  const r = await page.evaluate(() => {
    const box = document.querySelector('#approval-allow-once-btn')?.closest('[role="dialog"]');
    return { img: box?.querySelectorAll('img').length ?? -1, xss: window.__xss === 1, plan: document.querySelector('[data-testid="approval-plan"]')?.textContent ?? null };
  });
  ok('plan_card_escapes_html', r.img === 0 && !r.xss && r.plan === evil, JSON.stringify(r));
  push({ type: 'approval_resolved', request_id: 'p2', decision: 'deny', source: 'timeout' });
  await gone('#approval-allow-once-btn');
});

// ---- 1b. accept-edits scope is visible while the turn runs, gone after ----
await step(['turn_mode_notice_visible', 'turn_mode_notice_gone_after_done'], async () => {
  push({ type: 'turn_mode', mode: 'acceptEdits', source: 'plan_approved' });
  await page.waitForTimeout(300);
  const t = (await txt('[data-testid="turn-mode-notice"]')) ?? '';
  ok('turn_mode_notice_visible', (await vis('[data-testid="turn-mode-notice"]')) && /edits/i.test(t) && /this turn/i.test(t), t || 'no notice');
  push({ type: 'text', text: 'Done.' });
  push({ type: 'done' });
  await page.waitForTimeout(400);
  ok('turn_mode_notice_gone_after_done', t.length > 0 && !(await vis('[data-testid="turn-mode-notice"]')), t.length ? 'still visible after done' : 'never appeared (no vacuous pass)');
});

// ---- 3. the saved-rule notice outlives the turn ----
await step(['saved_rule_notice_after_done', 'saved_rule_notice_absent_without_rule'], async () => {
  // a turn shaped exactly like the positive one below but with no auto-allow: no notice anywhere
  push({ type: 'tool_call', id: 'toolu_n', name: 'Bash', input: { command: 'echo hi' } });
  push({ type: 'tool_result', tool_use_id: 'toolu_n', content: 'hi', is_error: false });
  push({ type: 'text', text: 'Hello' });
  push({ type: 'done' });
  await page.waitForTimeout(1200);
  const bodyTxt = await page.evaluate(() => document.body.innerText);
  const none = !/allowed by a saved rule/i.test(bodyTxt);
  ok('saved_rule_notice_absent_without_rule', none, none ? 'no notice without a rule match' : 'notice shown without a rule match');
  push({ type: 'permission_auto_allowed', request_id: 'x1', tool: 'Bash', rule_id: 1, pattern: 'npm test' });
  push({ type: 'tool_call', id: 'toolu_a', name: 'Bash', input: { command: 'npm test' } });
  push({ type: 'tool_result', tool_use_id: 'toolu_a', content: 'ok', is_error: false });
  push({ type: 'text', text: 'Tests passed.' });
  push({ type: 'done' });
  await page.waitForTimeout(1500);
  const shown = await page.evaluate(() => {
    const els = [...document.querySelectorAll('body *')].filter((e) => e.children.length === 0 && /allowed by a saved rule/i.test(e.textContent));
    return els.some((e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !e.closest('.sr-only'); });
  });
  ok('saved_rule_notice_after_done', shown, 'notice not visible after the turn landed');
});

// ---- 5. a tool row cut off by a reload ----
await step(['interrupted_row_terminal_label', 'live_tool_row_still_running'], async () => {
  await page.evaluate(async ([id]) => fetch('/api/conversations/' + id + '/settings', { method: 'PATCH', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ permission_mode: 'manual' }) }), [convId]);
  injectHistory = true;
  await boot();
  await page.waitForSelector('.tool-call-block', { timeout: 5000 });
  const st = (await txt('.tool-call-block .tool-call-status')) ?? '';
  ok('interrupted_row_terminal_label', /^Interrupted$/.test(st), 'status="' + st + '"');
  injectHistory = null;
  await boot();
  push({ type: 'tool_call', id: 'toolu_live', name: 'Bash', input: { command: 'sleep 5' } });
  await page.waitForSelector('.tool-call-block', { timeout: 3000 });
  const live = (await txt('.tool-call-block .tool-call-status')) ?? '';
  ok('live_tool_row_still_running', /^Running/.test(live), 'status="' + live + '"');
});

await browser.close();
const fails = [...results.values()].filter((v) => !v).length;
console.log(`CHECKS_REACHED ${results.size}`);
console.log(fails ? `RESULT FAIL ${fails}` : 'RESULT PASS');
process.exit(fails ? 1 : 0);
