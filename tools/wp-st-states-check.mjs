// WP-ST J2 frozen oracle (Opus, 2026-10-13): connection-state display, empty Send, header chrome (D6), driven end to end:
// real clone server + the WP-ST replay fake CLI (real CLI 2.1.291 streams). GCA_URL = isolated clone server; ST_CTRL = the
// scenario control file the fake reads; ST_CALLS = the file the fake appends one line to per invocation.
// Exit 0 = all pass, 1 = any fail. Never point this at :8765.
import { chromium } from 'playwright';
import fs from 'node:fs';

const URL = process.env.GCA_URL, CTRL = process.env.ST_CTRL, CALLS = process.env.ST_CALLS;
if (!URL || URL.includes(':8765') || !CTRL || !CALLS) { console.log('FAIL refusing: need isolated GCA_URL, ST_CTRL, ST_CALLS'); process.exit(1); }
const PORT = new globalThis.URL(URL).port;
const HELP = 'https://caas.open-webui.godaddy.com/apiKeys';
const RAW = ['Not logged in', '/login', 'LiteLLM', 'Virtual Key', 'inva****', 'ECONNREFUSED', 'API Error'];
const FINAL = ['connected', 'key_invalid', 'key_missing', 'url_missing', 'gateway_unreachable', 'error'];
let fails = 0;
const ok = (cond, name, detail = '') => { console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`); if (!cond) fails++; };
const calls = () => (fs.existsSync(CALLS) ? fs.readFileSync(CALLS, 'utf8').split('\n').filter(Boolean).length : 0);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(() => {
  try { localStorage.setItem('gca_onboarded', '1'); localStorage.setItem('gca_sb_open', '1'); localStorage.setItem('gca_ui_mode', 'advanced'); } catch { /* blocked storage */ }
  window.__ws = [];
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { try { window.__ws.push(JSON.parse(d).type); } catch { window.__ws.push('?'); } return send.call(this, d); };
  window.__st = [];
  setInterval(() => {
    const el = document.getElementById('conn-state');
    const s = el ? `${el.getAttribute('data-state')}|${el.textContent.trim()}` : 'none|';
    if (window.__st[window.__st.length - 1] !== s) window.__st.push(s);
  }, 20);
});

try {
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#composer-input', { timeout: 15000 });
  await page.waitForTimeout(800);

  // ---- state pill exists, in the header, accessible, starts Ready ----
  const pill = await page.evaluate(() => {
    const el = document.getElementById('conn-state');
    return el && { inHeader: !!el.closest('#chat-header'), role: el.getAttribute('role'), live: el.getAttribute('aria-live'),
      state: el.getAttribute('data-state'), text: el.textContent.trim() };
  });
  ok(pill && pill.inHeader, '#conn-state is inside #chat-header', JSON.stringify(pill));
  ok(pill && pill.role === 'status' && pill.live === 'polite', '#conn-state is a polite status region', JSON.stringify(pill));
  ok(pill && pill.state === 'ready' && pill.text === 'Ready', 'initial state Ready', JSON.stringify(pill));

  // ---- D6: no localhost / port / CLI / daemon in the header or the SERVER panel ----
  const chrome = await page.evaluate(() => ({ header: document.getElementById('chat-header')?.innerText ?? '',
    server: document.getElementById('rsb-panel-server')?.innerText ?? null }));
  const bad = (t) => [/localhost/i, /127\.0\.0\.1/, new RegExp(PORT), /8765/, /\bCLI\b/, /daemon/i].filter((r) => r.test(t)).map(String);
  ok(bad(chrome.header).length === 0, 'header shows no localhost/port/CLI/daemon', bad(chrome.header).join(' ') + ' :: ' + chrome.header.slice(0, 200));
  ok(chrome.server !== null && bad(chrome.server).length === 0, 'SERVER panel shows no address/port', String(chrome.server).slice(0, 200));
  ok(chrome.server !== null && /online/i.test(chrome.server), 'SERVER panel still shows Online', String(chrome.server).slice(0, 120));

  // ---- empty Send: visibly + accessibly disabled, sends nothing, spawns nothing ----
  const sendState = () => page.evaluate(() => {
    const b = document.getElementById('composer-send');
    const hintId = b?.getAttribute('aria-describedby') || '';
    const hint = hintId.split(/\s+/).map((i) => document.getElementById(i)?.textContent ?? '').join(' ');
    return { aria: b?.getAttribute('aria-disabled'), opacity: b ? parseFloat(getComputedStyle(b).opacity) : 1, hint };
  });
  await page.waitForTimeout(600);
  let s0 = await sendState();
  ok(s0.aria === 'true', 'empty composer: Send has aria-disabled="true"', JSON.stringify(s0));
  ok(s0.opacity <= 0.6, 'empty composer: Send is visibly dimmed (opacity <= 0.6)', String(s0.opacity));
  ok(/type a message/i.test(s0.hint), 'empty composer: Send is described by a "Type a message" hint', s0.hint);
  const c0 = calls();
  await page.click('#composer-send', { force: true, timeout: 3000 }).catch(() => {});  // a user can still click a dimmed button
  await page.focus('#composer-input');
  await page.keyboard.press('Enter');
  await page.fill('#composer-input', '   ');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(1000);
  const s1 = await sendState();
  ok(s1.aria === 'true', 'whitespace-only composer: Send stays aria-disabled', JSON.stringify(s1));
  ok(!(await page.evaluate(() => window.__ws.includes('send'))), 'empty/whitespace Send: no send frame on the WebSocket');
  ok(calls() === c0, 'empty/whitespace Send: the CLI was not invoked', `${c0} -> ${calls()}`);
  ok((await page.evaluate(() => document.getElementById('conn-state')?.getAttribute('data-state'))) === 'ready', 'empty Send leaves the state Ready');
  await page.fill('#composer-input', 'x');
  await page.waitForTimeout(600);  // let any opacity transition settle before sampling
  const s2 = await sendState();
  ok(s2.aria !== 'true' && s2.opacity > 0.9, 'text typed: Send is enabled', JSON.stringify(s2));
  await page.fill('#composer-input', '');
  ok((await sendState()).aria === 'true', 'cleared again: Send disabled again');

  // ---- one turn per scenario ----
  async function turn(scenario) {
    fs.writeFileSync(CTRL, scenario);
    await page.evaluate(() => { window.__st = []; });
    const before = calls();
    await page.fill('#composer-input', 'Reply with exactly: OK');
    await page.keyboard.press('Enter');
    // the turn has started once Send is swapped for Stop; it has ended once Send is back (and, when the pill exists, the
    // pill shows a final state). A base without the pill still finishes each turn quickly, so every check is reached.
    await page.waitForFunction(() => document.getElementById('composer-send')?.hidden, null, { timeout: 3000 }).catch(() => {});
    await page.waitForFunction((fin) => {
      const el = document.getElementById('conn-state');
      const send = document.getElementById('composer-send');
      return send && !send.hidden && (!el || fin.includes(el.getAttribute('data-state')));
    }, FINAL, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(400);
    const r = await page.evaluate(() => ({ st: window.__st.slice(), area: document.getElementById('chat-area')?.innerText ?? '',
      status: document.getElementById('chat-status')?.innerText ?? '',
      links: [...document.querySelectorAll('#chat-area a')].map((a) => ({ href: a.href, target: a.target, rel: a.rel, text: a.textContent.trim() })) }));
    r.spawned = calls() - before;
    r.states = r.st.map((x) => x.split('|')[0]);
    r.last = r.st[r.st.length - 1] || '';
    return r;
  }
  const attempts = (r, state) => r.st.filter((x) => x.startsWith(state + '|')).map((x) => (x.match(/attempt (\d+) of (\d+)/) || [])[1]).filter(Boolean).map(Number);
  const noRaw = (r, name) => { const hit = RAW.filter((x) => r.area.includes(x)); ok(hit.length === 0, `${name}: no raw CLI error text in the chat`, hit.join(', ')); };
  const help = (r, name) => ok(r.links.some((l) => l.href === HELP && l.target === '_blank' && /noopener/.test(l.rel)), `${name}: chat offers the CaaS API keys link (new tab, noopener)`, JSON.stringify(r.links));
  const inc = (a) => a.length > 0 && a.every((v, i) => i === 0 || v > a[i - 1]);

  let r = await turn('ok');
  ok(r.spawned === 1, 'ok: one CLI invocation', String(r.spawned));
  ok(r.states.includes('sending') && r.states.includes('streaming'), 'ok: Sending then Streaming observed', r.st.join(' > '));
  ok(r.states.indexOf('sending') < r.states.indexOf('streaming'), 'ok: Sending comes before Streaming', r.st.join(' > '));
  ok(r.last === 'connected|Connected', 'ok: ends Connected', r.last);
  ok(/Streaming/.test(r.st.find((x) => x.startsWith('streaming|')) || '') && /Sending/.test(r.st.find((x) => x.startsWith('sending|')) || ''), 'ok: Sending/Streaming labels', r.st.join(' > '));

  // ---- D6 Advanced: server address + CLI version moved there ----
  await page.click('#settings-btn');
  await page.waitForSelector('#adv-server-address', { state: 'attached', timeout: 3000 }).catch(() => {});
  await page.waitForFunction(() => { const a = document.getElementById('adv-server-address'), v = document.getElementById('adv-cli-version');
    return a && v && /\d/.test(a.textContent) && /\d/.test(v.textContent); }, null, { timeout: 5000 }).catch(() => {});
  const adv = await page.evaluate(() => {
    const sec = document.getElementById('advanced-settings-section');
    const a = document.getElementById('adv-server-address'), v = document.getElementById('adv-cli-version');
    return { inAdv: !!(sec && a && v && sec.contains(a) && sec.contains(v)), addr: a?.textContent.trim() ?? '', ver: v?.textContent.trim() ?? '' };
  });
  ok(adv.inAdv, 'Advanced section holds #adv-server-address and #adv-cli-version', JSON.stringify(adv));
  const reported = await page.evaluate(() => fetch('/api/server/info').then((r) => r.json()).then((i) => i.url).catch(() => null));
  ok(!!reported && adv.addr === reported, 'Advanced shows the server address the server reports', `${adv.addr} vs ${reported}`);
  ok(adv.ver.includes('2.1.291'), 'Advanced shows the CLI version the CLI reported', adv.ver);
  // J7: the card is modal (its overlay intercepts clicks), so Escape closes it;
  // the base build's drawer still closes with a second click on the trigger.
  if (await page.evaluate(() => !!document.getElementById('settings-card'))) await page.keyboard.press('Escape');
  else await page.click('#settings-btn');
  await page.waitForTimeout(400);

  r = await turn('deadproxy');
  const ra = attempts(r, 'retrying');
  ok(ra[0] === 1, 'dead proxy: state changes to Retrying at the FIRST api_retry (attempt 1)', r.st.slice(0, 6).join(' > '));
  ok(inc(ra) && ra.length >= 8 && ra[ra.length - 1] === 10, 'dead proxy: attempt count increments 1..10', ra.join(','));
  ok(r.st.some((x) => x === 'retrying|Retrying (attempt 1 of 10)'), 'dead proxy: label "Retrying (attempt 1 of 10)"', r.st.slice(0, 5).join(' > '));
  ok(!r.states.includes('streaming'), 'dead proxy: never Streaming', r.st.join(' > '));
  ok(r.last === "gateway_unreachable|Can't reach Claude", "dead proxy: ends \"Can't reach Claude\"", r.last);
  ok(/vpn/i.test(r.status), 'dead proxy: message mentions the VPN', r.status);
  noRaw(r, 'dead proxy');

  r = await turn('key_invalid');
  const ka = attempts(r, 'key_invalid');
  const firstReal = r.states.slice(r.states.indexOf('sending') + 1)[0];  // first state after this turn's Sending
  ok(firstReal === 'key_invalid' && ka[0] === 1, 'key invalid: labelled at the first 401 (attempt 1)', r.st.slice(0, 5).join(' > '));
  ok(inc(ka) && ka.length >= 8, 'key invalid: attempt count increments while the CLI retries', ka.join(','));
  ok(!r.states.includes('retrying') && !r.states.includes('streaming'), 'key invalid: never Retrying/Streaming', r.st.join(' > '));
  ok(r.last === 'key_invalid|Key invalid', 'key invalid: ends "Key invalid"', r.last);
  noRaw(r, 'key invalid'); help(r, 'key invalid');

  r = await turn('key_missing');
  ok(r.last === 'key_missing|API key missing', 'key missing: ends "API key missing"', r.last);
  ok(/key/i.test(r.status), 'key missing: message says the key is missing', r.status);
  noRaw(r, 'key missing'); help(r, 'key missing');

  r = await turn('url_missing');
  ok(r.last === 'url_missing|Gateway address missing', 'url missing: ends "Gateway address missing"', r.last);
  noRaw(r, 'url missing'); help(r, 'url missing');

  r = await turn('recover');
  ok(attempts(r, 'retrying')[0] === 1 && r.states.includes('streaming') && r.last === 'connected|Connected', 'recovery: Retrying clears to Streaming then Connected', r.st.join(' > '));
  r = await turn('recover401');
  ok(r.states.includes('key_invalid') && r.states.includes('streaming') && r.last === 'connected|Connected', 'recovery after a 401: Key invalid clears with the flow', r.st.join(' > '));

  r = await turn('generic_error');
  ok(r.last.startsWith('error|') && r.last.length > 'error|'.length, 'unclassified failure: generic error state with a label', r.last);
  noRaw(r, 'generic error');

  r = await turn('ok');
  ok(r.last === 'connected|Connected', 'after failures a good turn returns to Connected', r.last);
} catch (e) {
  ok(false, 'harness exception', String(e && e.stack || e).slice(0, 500));
} finally {
  await browser.close();
}
console.log(fails ? `RESULT FAIL ${fails}` : 'RESULT PASS');
process.exit(fails ? 1 : 0);
