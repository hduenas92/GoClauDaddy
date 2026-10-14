// RESTORATION CORE J2 frozen oracle, UI half (Opus, 2026-10-14). Real isolated clone server (GCA_URL) + the WP-ST replay fake CLI
// (real CLI 2.1.291 streams; ST_CTRL = scenario file, ST_SLEEP 0.05). Never :8765, never the real CLI. Every check prints exactly one
// "PASS <id>" or "FAIL <id>" line, also on a base without the fixes (each step catches its own errors), then "CHECKS_REACHED <n>".
//
// WHAT AN "ANNOUNCEMENT" IS (the recorder, proven by recorder_selftest): a MutationObserver over the whole document; a mutation inside
// (or inserting) a live region whose resulting text is non-empty and differs from that region's previous text records one
// announcement {region, politeness, text}, in order, interleaved with the WebSocket frames the page received (so "the retry loop"
// is the exact span between the first retry frame and the first non-retry frame). Live region = nearest ancestor-or-self with an
// explicit aria-live (value "off" = silent, it overrides the role) or role status / alert / log. Regions inside aria-hidden / hidden /
// inert / display:none / visibility:hidden are silent. An emptied region forgets its text (a repeated identical reply is announced
// again). A region INSERTED already holding text counts (upper bound: real screen readers may skip some insertions).
//
// CONTRACT (frozen; Houston/Kenobi ruling Q4 2026-10-13 + AUDIT 2.6; reference fix and build must satisfy exactly this):
//  SR-1 retry loop     a retry loop (frames conn_state with an attempt) produces exactly ONE announcement for the whole span:
//                      the first retry. Nothing per retry. (deadproxy, key_invalid, and recover = retries then success)
//  SR-2 settled error  the terminal error sentence is announced EXACTLY ONCE (not once in #chat-status AND once in the alert toast),
//                      and at least once (never silenced). (deadproxy, key_invalid, key_missing, generic_error)
//  SR-4 error bubble   the failed assistant bubble carries the reason text (it is not an empty "ERROR" shell).
//  SR-3 (part)         a normal turn announces its COMPLETION exactly once: of "Claude replied: <text>" and the "Done" badge, one.
//                      Guards: the reply text still reaches the user, an identical repeat reply is announced again, an idle page is quiet.
//  RULED AMENDMENT R-J2-1 (Kenobi, 2026-10-14; supersedes the NOT-encoded note below): the success path is silent apart from the reply.
//                      A normal turn produces exactly ONE announcement (the completion, 'Claude replied: ...'); 'Thinking...', 'Sending...',
//                      'Streaming...' and per-turn 'Connected' never reach a live region. CARVE-OUT: the 'Monthly Spend Estimate' toast (a budget signal,
//                      not turn chatter) still announces, once per session, and is excluded from the one-announcement count.
//                      R-J2-2: pill wording is deferred to WP-80; J2 writes no pill copy, nothing pins it.
//  (superseded) NOT encoded: a cap on the OTHER per-turn pill/badge chatter
//                      ("Thinking…", "Sending…", "Streaming…", "Connected") and pill wording. No assertion pins them either way.
import { chromium } from 'playwright';
import fs from 'node:fs';

const URL = process.env.GCA_URL, CTRL = process.env.ST_CTRL;
if (!URL || URL.includes(':8765') || !CTRL) { console.log('FAIL refusing: need an isolated GCA_URL and ST_CTRL'); process.exit(1); }
const results = new Map();
const ok = (id, cond, detail = '') => {
  if (results.has(id)) return;
  results.set(id, !!cond);
  console.log(`${cond ? 'PASS' : 'FAIL'} ${id}${detail ? ' :: ' + String(detail).slice(0, 400) : ''}`);
};
const step = async (ids, fn) => {
  try { await fn(); } catch (e) { for (const id of ids) ok(id, false, 'step error: ' + (e?.message || e)); }
  for (const id of ids) if (!results.has(id)) ok(id, false, 'not asserted');
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(() => {
  try { localStorage.setItem('gca_onboarded', '1'); } catch { /* blocked */ }
  window.__log = [];
  const NativeWS = window.WebSocket;
  const Wrapped = function (...a) {
    const w = new NativeWS(...a);
    w.addEventListener('message', (ev) => {
      try { const f = JSON.parse(ev.data); window.__log.push({ k: 'frame', type: f.type, state: f.state, attempt: f.attempt ?? null }); } catch { /* not JSON */ }
    });
    return w;
  };
  Wrapped.prototype = NativeWS.prototype;
  for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) Wrapped[k] = NativeWS[k];
  window.WebSocket = Wrapped;

  const LIVE_ROLES = new Set(['status', 'alert', 'log']);
  const isRegion = (e) => e.nodeType === 1 && (e.hasAttribute('aria-live') || LIVE_ROLES.has(e.getAttribute('role')));
  const regionOf = (n) => { for (let e = n.nodeType === 1 ? n : n.parentElement; e; e = e.parentElement) if (isRegion(e)) return e; return null; };
  const politeness = (r) => {
    const al = r.getAttribute('aria-live');
    if (al) return al === 'off' ? null : al;
    return r.getAttribute('role') === 'alert' ? 'assertive' : 'polite';
  };
  const hiddenFromAT = (e) => {
    for (let p = e; p; p = p.parentElement) {
      if (p.getAttribute('aria-hidden') === 'true' || p.hidden || p.inert) return true;
      const cs = getComputedStyle(p);
      if (cs.display === 'none' || cs.visibility === 'hidden') return true;
    }
    return false;
  };
  const seen = new WeakMap();
  const name = (e) => (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.split(/\s+/)[0] : '') || e.tagName.toLowerCase();
  const mo = new MutationObserver((muts) => {
    const regs = new Set();
    for (const m of muts) {
      const r = regionOf(m.target); if (r) regs.add(r);
      for (const a of m.addedNodes) {
        const rr = regionOf(a); if (rr) regs.add(rr);
        if (a.nodeType === 1) a.querySelectorAll('[role],[aria-live]').forEach((x) => { if (isRegion(x)) regs.add(x); });
      }
    }
    for (const r of regs) {
      const text = (r.textContent || '').trim().replace(/\s+/g, ' ');
      const prev = seen.get(r);
      seen.set(r, text);
      if (!text || prev === text) continue;
      const p = politeness(r);
      if (!p || hiddenFromAT(r)) continue;
      window.__log.push({ k: 'ann', region: name(r), assertive: p === 'assertive', text });
    }
  });
  const start = () => mo.observe(document.documentElement, { subtree: true, childList: true, characterData: true });
  if (document.documentElement) start(); else document.addEventListener('DOMContentLoaded', start);
});

const sleep = (ms) => page.waitForTimeout(ms);
const read = () => page.evaluate(() => window.__log.slice());
const reset = () => page.evaluate(() => { window.__log = []; });
const ann = (log) => log.filter((e) => e.k === 'ann');
const annText = (log) => ann(log).map((a) => `${a.assertive ? '!' : ''}${a.text.slice(0, 60)}`).join(' | ');

async function turn(scenario, msg = 'Reply with exactly: OK') {
  fs.writeFileSync(CTRL, scenario);
  await reset();
  await page.fill('#composer-input', msg);
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.getElementById('composer-send')?.hidden, null, { timeout: 3000 }).catch(() => {});
  await page.waitForFunction(() => { const s = document.getElementById('composer-send'); return s && !s.hidden; }, null, { timeout: 30000 }).catch(() => {});
  await sleep(1200); // let the reply announcement (next tick), toasts and timers land
  const bubble = await page.evaluate(() => {
    const all = [...document.querySelectorAll('#chat-messages .msg-assistant')];
    const last = all[all.length - 1];
    return last ? (last.querySelector('.msg-bubble')?.innerText ?? '') : null;
  });
  return { log: await read(), bubble };
}

const SPEND = /Monthly Spend Estimate/i, CHATTER = /\b(Thinking|Sending|Streaming|Connected)\b/;
const nonSpend = (log) => ann(log).filter((a) => !SPEND.test(a.text));
let spendSeen = 0;
const isRetry = (e) => e.k === 'frame' && e.type === 'conn_state' && e.attempt != null;
// announcements strictly between the first retry frame and the first frame that is not a retry frame (the settled state is NOT inside the loop)
function loopAnns(log) {
  const s = log.findIndex(isRetry);
  if (s < 0) return null;
  let e = log.findIndex((x, i) => i > s && x.k === 'frame' && !isRetry(x));
  if (e < 0) e = log.length;
  return { anns: ann(log.slice(s + 1, e)), retries: log.slice(s, e).filter(isRetry).length, next: log[e] ? `${log[e].type}/${log[e].state ?? ''}` : 'end-of-log' };
}
const PHRASE = {
  deadproxy: /Can't reach Claude\. Check your internet/,
  key_invalid: /rejected your API key/,
  key_missing: /No API key was found/,
  generic_error: /Claude reported an error/,
};
function afterError(log) {
  const i = log.findIndex((e) => e.k === 'frame' && e.type === 'error');
  return i < 0 ? null : ann(log.slice(i));
}

try {
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#composer-input', { timeout: 15000 });
  await sleep(800);

  // ---- recorder self-test: the instrument counts what it claims to count ----
  await step(['recorder_selftest'], async () => {
    await reset();
    await page.evaluate(() => {
      const mk = (attrs, parent = document.body) => { const d = document.createElement('div'); d.setAttribute('data-selftest', '1'); for (const [k, v] of Object.entries(attrs)) d.setAttribute(k, v); parent.appendChild(d); return d; };
      window.__st = { a: mk({ role: 'status', id: 'st-a' }), off: mk({ role: 'status', 'aria-live': 'off', id: 'st-off' }), hid: mk({ 'aria-hidden': 'true' }) };
      window.__st.inHid = mk({ role: 'status', id: 'st-hid' }, window.__st.hid);
    });
    const tick = async () => { await sleep(80); };
    const set = async (id, t) => { await page.evaluate(([i, x]) => { window.__st[i].textContent = x; }, [id, t]); await tick(); };
    await set('a', 'one');
    await set('a', 'one'); // unchanged text: no new announcement
    await set('a', ''); await set('a', 'one'); // emptied then repeated: announced again
    await set('off', 'quiet'); await set('inHid', 'hidden');
    await page.evaluate(() => { const d = document.createElement('div'); d.setAttribute('role', 'alert'); d.setAttribute('data-selftest', '1'); d.textContent = 'inserted alert'; document.body.appendChild(d); });
    await tick();
    await page.evaluate(() => { // a live region arriving INSIDE an inserted non-live container (dialogs, panels) must count too
      const c = document.createElement('div'); c.setAttribute('data-selftest', '1');
      const k = document.createElement('div'); k.setAttribute('role', 'status'); k.id = 'st-nested'; k.textContent = 'nested status';
      c.appendChild(k); document.body.appendChild(c);
    });
    await tick();
    const a = ann(await read());
    const byRegion = (r) => a.filter((x) => x.region === r).length;
    const good = byRegion('#st-a') === 2 && byRegion('#st-off') === 0 && byRegion('#st-hid') === 0 && a.filter((x) => x.text === 'inserted alert' && x.assertive).length === 1 && byRegion('#st-nested') === 1;
    await page.evaluate(() => document.querySelectorAll('[data-selftest]').forEach((e) => e.remove()));
    ok('recorder_selftest', good, annText({ length: 0, filter: () => a }) + ` | counts a=${byRegion('#st-a')} off=${byRegion('#st-off')} hid=${byRegion('#st-hid')} nested=${byRegion('#st-nested')}`);
  });

  // ---- SR-3 part: a normal turn, twice (the second proves a repeated identical reply is announced again) ----
  let okTurn = null;
  await step(['ok_completion_once', 'ok_reply_text_announced', 'ok_quiet_when_idle', 'ok_single_announcement', 'ok_no_turn_chatter'], async () => {
    okTurn = await turn('ok');
    const done = ann(okTurn.log).filter((a) => /Claude replied/i.test(a.text) || /\bDone\b/.test(a.text));
    ok('ok_completion_once', done.length === 1, `completion announcements=${done.length}: ${annText(okTurn.log)}`);
    ok('ok_reply_text_announced', ann(okTurn.log).some((a) => /\bOK\b/.test(a.text)), annText(okTurn.log));
    const quiet1 = nonSpend(okTurn.log); spendSeen += ann(okTurn.log).length - quiet1.length;
    ok('ok_single_announcement', quiet1.length === 1 && /Claude replied/i.test(quiet1[0].text), `announcements excluding the spend toast=${quiet1.length}: ${annText({ filter: () => quiet1 })}`);
    ok('ok_no_turn_chatter', !quiet1.some((a) => CHATTER.test(a.text)), annText({ filter: () => quiet1 }));
    await reset(); await sleep(1500);
    const idle = ann(await read());
    ok('ok_quiet_when_idle', idle.length === 0, annText({ filter: () => idle }));
  });
  await step(['ok_completion_once_repeat', 'ok_repeat_reply_announced', 'ok_single_announcement_repeat', 'spend_toast_once_per_session'], async () => {
    const t2 = await turn('ok');
    const done = ann(t2.log).filter((a) => /Claude replied/i.test(a.text) || /\bDone\b/.test(a.text));
    ok('ok_completion_once_repeat', done.length === 1, `completion announcements=${done.length}: ${annText(t2.log)}`);
    ok('ok_repeat_reply_announced', ann(t2.log).some((a) => /\bOK\b/.test(a.text)), annText(t2.log));
    const quiet2 = nonSpend(t2.log); spendSeen += ann(t2.log).length - quiet2.length;
    ok('ok_single_announcement_repeat', quiet2.length === 1 && /Claude replied/i.test(quiet2[0].text) && !quiet2.some((a) => CHATTER.test(a.text)), `announcements excluding the spend toast=${quiet2.length}: ${annText({ filter: () => quiet2 })}`);
    ok('spend_toast_once_per_session', spendSeen === 1, `spend-estimate announcements across two turns=${spendSeen}`);
  });

  // ---- SR-1: retry loops ----
  for (const [sc, id] of [['deadproxy', 'retry_loop_deadproxy_one'], ['key_invalid', 'retry_loop_keyinvalid_one'], ['recover', 'retry_loop_recover_one']]) {
    await step([id], async () => {
      const t = await turn(sc);
      const w = loopAnns(t.log);
      if (!w) return ok(id, false, 'no retry frames reached the page: ' + JSON.stringify(t.log.filter((e) => e.k === 'frame').map((e) => e.type)));
      ok(id, w.retries >= 2 && w.anns.length === 1, `retry frames=${w.retries} (then ${w.next}), announcements in the loop=${w.anns.length}: ${annText({ filter: () => w.anns })}`);
      if (sc !== 'recover') results.set('_' + sc, t);
    });
  }

  // ---- SR-2 / SR-4: terminal errors ----
  const errTurns = { deadproxy: results.get('_deadproxy'), key_invalid: results.get('_key_invalid') };
  for (const sc of ['key_missing', 'generic_error']) errTurns[sc] = await turn(sc).catch(() => null);
  const ids = ['error_once_deadproxy', 'error_once_keyinvalid', 'error_once_keymissing', 'error_once_generic'];
  const map = { deadproxy: ids[0], key_invalid: ids[1], key_missing: ids[2], generic_error: ids[3] };
  await step([...ids, 'error_announced_at_least_once', 'error_bubble_has_reason'], async () => {
    let allPresent = true; const absent = []; const emptyB = [];
    for (const [sc, id] of Object.entries(map)) {
      const t = errTurns[sc];
      if (!t) { ok(id, false, 'scenario did not run'); allPresent = false; absent.push(sc); emptyB.push(sc); continue; }
      const after = afterError(t.log);
      if (!after) { ok(id, false, 'no error frame reached the page'); allPresent = false; absent.push(sc); }
      else {
        const hits = after.filter((a) => PHRASE[sc].test(a.text));
        ok(id, hits.length === 1, `announcements carrying the error sentence=${hits.length}: ${annText({ filter: () => after })}`);
        if (hits.length < 1) { allPresent = false; absent.push(sc); }
      }
      if (!PHRASE[sc].test(t.bubble ?? '')) emptyB.push(sc + `(${JSON.stringify((t.bubble ?? '').slice(0, 40))})`);
    }
    ok('error_announced_at_least_once', allPresent, 'silent: ' + absent.join(','));
    ok('error_bubble_has_reason', emptyB.length === 0, 'bubble lacks the reason in: ' + emptyB.join(', '));
  });
} catch (e) {
  console.log('FAIL harness_error :: ' + (e?.message || e));
}
console.log(`CHECKS_REACHED ${[...results.keys()].filter((k) => !k.startsWith('_')).length}`);
await browser.close();
process.exit([...results.entries()].some(([k, v]) => !k.startsWith('_') && !v) ? 1 : 0);
