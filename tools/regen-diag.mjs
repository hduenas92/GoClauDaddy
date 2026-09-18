/**
 * regen-diag.mjs — TEMPORARY instrumented probe for Task 2.1.
 *
 *   node tools/regen-diag.mjs
 *
 * regen-check.mjs waited on
 *     assistants > n && !busy && regen-btn
 * and timed out after 240s. Reading the code says that predicate can never be
 * true: composer.send({regenerate:true}) appends a NEW assistant row
 * synchronously, and the click handler immediately removes the superseded row,
 * so the steady state after a correct regenerate is exactly ONE assistant row.
 * The only moment the count exceeds n is the window in which the new row is
 * still status-processing — i.e. busy — so the two halves are never true at the
 * same time.
 *
 * "The predicate is wrong" and "the app never regenerated at all" produce an
 * identical timeout, so this probe does not assert anything. It records the
 * DOM timeline in-band and the server transcript either side of the click, so
 * the difference is visible instead of inferred.
 */

import { chromium } from 'playwright';

const BASE_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
// The two answers must be distinguishable, otherwise "the new answer replaced
// the old one" cannot be told apart from "nothing happened".
const PROMPT = process.env.GCA_PROMPT ?? 'Reply with a random integer between 100000 and 999999, and nothing else.';
const TURN_TIMEOUT = Number(process.env.GCA_TURN_TIMEOUT ?? 240_000);
const WATCH_MS = Number(process.env.GCA_WATCH_MS ?? 120_000);
const KEEP = process.argv.slice(2).includes('--keep');

async function api(path, method = 'GET') {
    const res = await fetch(BASE_URL + path, { method });
    if (!res.ok) throw new Error(`${method} ${path} -> HTTP ${res.status}`);
    return res.status === 204 ? null : res.json();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function convIds() {
    return (await api('/api/conversations') ?? []).map((c) => c.id);
}

async function liveMessages(id) {
    const data = await api(`/api/conversations/${id}`);
    return data.messages ?? [];
}

async function waitForNewConversation(before, timeout = 20_000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        const now = await convIds();
        if (now.length === before.length + 1) {
            const fresh = now.filter((id) => !before.includes(id));
            if (fresh.length === 1) return fresh[0];
        }
        await sleep(250);
    }
    throw new Error(`no new conversation appeared within ${timeout}ms`);
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.addInitScript(() => {
    try {
        localStorage.setItem('gca_onboarded', '1');
        localStorage.setItem('gca_feat_assess', '0');
    } catch { /* ignore */ }
});

const page = await context.newPage();
const dialogs = [];
const pageErrors = [];
const consoleErrors = [];
page.on('dialog', async (d) => {
    dialogs.push(`${d.type()}: ${d.message()}`);
    await d.dismiss().catch(() => { });
});
page.on('pageerror', (e) => pageErrors.push(String(e.message ?? e)));
page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
});

/** Sample the transcript ten times a second, in the page, numbered by hand. */
const INSTALL_WATCH = () => {
    window.__watch = [];
    window.__watchStart = performance.now();
    window.__watchTimer = setInterval(() => {
        const rows = [...document.querySelectorAll('.msg.msg-user, .msg.msg-assistant')];
        window.__watch.push({
            t: Math.round(performance.now() - window.__watchStart),
            rows: rows.length,
            assistants: rows.filter((r) => r.className.includes('msg-assistant')).length,
            processing: document.querySelectorAll('.status-processing').length,
            regen: document.querySelectorAll('.regen-btn').length,
            firstRowText: (rows[0]?.querySelector('.text-block')?.innerText ?? '').slice(0, 24),
            lastRowText: (rows[rows.length - 1]?.querySelector('.text-block')?.innerText ?? '').slice(0, 24),
        });
    }, 100);
};

const READ_WATCH = () => {
    clearInterval(window.__watchTimer);
    return window.__watch;
};

function compress(timeline) {
    // Only the rows where something changed are interesting.
    const out = [];
    let prev = null;
    const key = (s) => `${s.rows}/${s.assistants}/${s.processing}/${s.regen}/${s.lastRowText}`;
    for (const s of timeline) {
        const k = key(s);
        if (k !== prev) {
            out.push(s);
            prev = k;
        }
    }
    return out;
}

const before = await convIds();
console.log(`baseline: ${before.length} conversation(s)`);
let scratchId = null;

try {
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#new-conv-btn', { timeout: 15_000 });
    await page.evaluate(() => {
        const el = document.querySelector('#composer-input');
        if (el) el.setAttribute('data-stale', '1');
    });

    await page.click('#new-conv-btn');
    scratchId = await waitForNewConversation(before);
    console.log(`scratch conversation: ${scratchId}`);
    await page.waitForSelector('#composer-input:not([data-stale])', { timeout: 20_000 });

    // ── turn 1 ───────────────────────────────────────────────────────────────
    const t0 = Date.now();
    await page.fill('#composer-input', PROMPT);
    await page.click('#composer-send');
    await page.waitForSelector('.regen-btn', { timeout: TURN_TIMEOUT });
    console.log(`\nturn 1 completed in ${((Date.now() - t0) / 1000).toFixed(1)}s`);

    const m1 = await liveMessages(scratchId);
    const answer1 = m1.find((m) => m.role === 'assistant')?.content ?? '';
    console.log(`turn 1 live transcript: [${m1.map((m) => m.role).join(',')}]`);
    console.log(`turn 1 answer: ${JSON.stringify(answer1.slice(0, 40))}`);
    const stats1 = await api(`/api/conversations/${scratchId}/stats`);
    console.log(`turn 1 stats: step_count=${stats1.step_count} tokens_in=${stats1.tokens_in}`);

    const domBefore = await page.$$eval('.msg.msg-user, .msg.msg-assistant', (els) =>
        els.map((e) => (e.className.includes('msg-user') ? 'user' : 'assistant')));
    console.log(`DOM before click: [${domBefore.join(',')}]`);

    // ── the click under test ────────────────────────────────────────────────
    await page.evaluate(INSTALL_WATCH);
    const tClick = Date.now();
    await page.click('.regen-btn');
    console.log('\n--- regen clicked; watching ---');

    let elapsed = 0;
    const step = 2000;
    const seen = new Set();
    while (elapsed < WATCH_MS) {
        await sleep(step);
        elapsed = Date.now() - tClick;
        const snap = await page.evaluate(() => {
            const rows = [...document.querySelectorAll('.msg.msg-user, .msg.msg-assistant')];
            return {
                rows: rows.length,
                assistants: rows.filter((r) => r.className.includes('msg-assistant')).length,
                processing: document.querySelectorAll('.status-processing').length,
                regen: document.querySelectorAll('.regen-btn').length,
                lastText: (rows[rows.length - 1]?.querySelector('.text-block')?.innerText ?? '').slice(0, 24),
            };
        });
        const live = await liveMessages(scratchId);
        const line = `${(elapsed / 1000).toFixed(1)}s rows=${snap.rows} assistants=${snap.assistants} processing=${snap.processing} regen=${snap.regen} last=${JSON.stringify(snap.lastText)} live=[${live.map((m) => m.role).join(',')}]`;
        if (!seen.has(line.replace(/^[\d.]+s /, ''))) {
            console.log(line);
            seen.add(line.replace(/^[\d.]+s /, ''));
        }
        // Stop as soon as a NEW non-empty answer is live and the app is idle again.
        const liveAnswer = live.find((m) => m.role === 'assistant')?.content ?? '';
        if (snap.processing === 0 && snap.regen === 1 && liveAnswer && liveAnswer !== answer1) break;
    }
    console.log(`\nwatched for ${(elapsed / 1000).toFixed(1)}s`);

    const timeline = await page.evaluate(READ_WATCH);
    console.log('\nDOM TIMELINE (state changes only, t in ms from the click)');
    for (const s of compress(timeline)) {
        console.log(`  +${String(s.t).padStart(5)}ms  rows=${s.rows} assistants=${s.assistants} processing=${s.processing} regen=${s.regen} last=${JSON.stringify(s.lastRowText)}`);
    }
    if (timeline.length) {
        const maxAssistants = Math.max(...timeline.map((s) => s.assistants));
        console.log(`  peak assistant rows seen: ${maxAssistants}`);
    }

    // ── server truth ────────────────────────────────────────────────────────
    const m2 = await liveMessages(scratchId);
    const answer2 = m2.find((m) => m.role === 'assistant')?.content ?? '';
    console.log('\nMEASURED');
    console.log(`  live transcript after regen = [${m2.map((m) => m.role).join(',')}]`);
    console.log(`  live answer changed         = ${String(answer2 !== answer1)}`);
    console.log(`  answer1 = ${JSON.stringify(answer1.slice(0, 60))}`);
    console.log(`  answer2 = ${JSON.stringify(answer2.slice(0, 60))}`);
    const stats2 = await api(`/api/conversations/${scratchId}/stats`);
    console.log(`  stats step_count            = ${stats2.step_count} (2 => the replaced turn is still on disk, superseded)`);
    console.log(`  stats tokens_in             = ${stats2.tokens_in}`);
    const exported = await api(`/api/conversations/${scratchId}/export`);
    console.log(`  export chars                = ${(exported.markdown ?? '').length}`);
    console.log(`  dialogs                     = ${dialogs.length ? dialogs.join(' | ') : 'none'}`);
    console.log(`  page errors                 = ${pageErrors.length ? pageErrors.join(' | ') : 'none'}`);
    console.log(`  console errors              = ${consoleErrors.length ? consoleErrors.join(' | ') : 'none'}`);
} catch (err) {
    console.log(`\nPROBE THREW: ${err.message ?? err}`);
} finally {
    if (scratchId && !KEEP) {
        try {
            await api(`/api/conversations/${scratchId}`, 'DELETE');
            const after = await convIds();
            console.log(`\ncleanup: ${after.length} conversation(s) (baseline ${before.length})`);
        } catch (err) {
            console.log(`\ncleanup FAILED: ${err.message ?? err}`);
        }
    } else if (KEEP) {
        console.log(`\n--keep: leaving ${scratchId} in place`);
    }
    await context.close();
    await browser.close();
}
