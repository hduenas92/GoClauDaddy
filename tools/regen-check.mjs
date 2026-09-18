/**
 * regen-check.mjs — end-to-end proof for Task 2.1 (regenerate).
 *
 *   node tools/regen-check.mjs            run the proof, clean up, exit 0/1
 *   node tools/regen-check.mjs --keep     leave the scratch conversation behind
 *   node tools/regen-check.mjs --skip-preflight   do not probe the CLI first
 *
 * WHAT THIS PROVES
 * One click on Regenerate must
 *   (a) supersede the old answer,
 *   (b) re-send the last user turn BY ITSELF — no second click, no manual
 *       re-send, and
 *   (c) leave the transcript as [user][assistant].
 * (c) is the interesting one: composer.send() used to append a user bubble and
 * the handler used to persist a user row, so a regenerate that went through
 * send() doubled the user turn into [user][user][assistant] — a transcript the
 * user cannot repair from the UI.
 *
 * HOW THE COMPLETION IS DETECTED (two earlier signals were wrong)
 * 1. `assistants > n && !busy && regen-btn` — never satisfiable. The replacement
 *    row is appended synchronously and the superseded row is removed on the next
 *    microtask, so a CORRECT regenerate ends with exactly ONE assistant row. The
 *    only instant the count exceeds n is while the new row is status-processing,
 *    i.e. precisely when !busy is false. Measured peak over a whole click: 1.
 * 2. "the answer TEXT changed" — unsound. The model may legitimately return the
 *    same text for the same question twice, which is indistinguishable from
 *    "nothing happened" (observed: two runs both answered 847293 to turn 1).
 * 3. `waitForSelector('.regen-btn')` for turn 1 — sound on the happy path only.
 *    finishAssistantMessage() paints that button ONLY when status === "done", so
 *    a turn that ends in `error` (or `stopped`, or `timeout`) renders no button
 *    at all and this file waited 700s on a signal that could never arrive, while
 *    the server log showed the turn had in fact completed. A wait on a UI
 *    affordance must target a signal reachable on EVERY terminal status.
 * The signal is now server-side IDENTITY: the live (non-superseded) assistant
 * row must become a DIFFERENT row, with the app idle again. That is exactly the
 * contract — the old answer is superseded and a new one answers the same
 * question — and it cannot be satisfied by a stale transcript.
 *
 * A bounded poll replaces waitForFunction: a bare timeout reports only that time
 * passed, whereas this prints what was last observed, so "the server never
 * answered", "the DOM never went idle" and "the row was never replaced" are
 * distinguishable instead of all looking like a hang.
 *
 * On row counts: the general rule is "never assert on element counts". This file
 * is the documented exception, because here the count IS the contract — the
 * acceptance criterion is literally "exactly 2 rows, never 3". The assertion is
 * on the ROLE SEQUENCE; raw counts are reported as evidence.
 *
 * PRE-FLIGHT (why this file now probes the CLI before opening a browser)
 * If the backend cannot answer at all — an exhausted CaaS budget, an expired
 * token — the CLI retries the rejected request for ~3 minutes, exits rc=1 with
 * NO stderr, and the app emits `error` then `done` with nothing rendered. Every
 * assertion below then fails as a timeout and the report blames the UI for an
 * environment fault. So before launching a browser this file runs
 * `claude -p "<one word>"` and, if that fails, prints the CLI's OWN message and
 * exits 2 without touching the app. Pass --skip-preflight to bypass it.
 *
 * Requires: the app on 127.0.0.1:8765 and a working `claude` CLI. It creates its
 * own conversation and deletes it again unless --keep is passed, so a passing
 * run leaves the conversation count exactly as it found it.
 */

import { spawnSync } from 'node:child_process';
import { chromium } from 'playwright';

const BASE_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const PROMPT = process.env.GCA_PROMPT ?? 'Reply with a random integer between 100000 and 999999, and nothing else.';
const TURN_TIMEOUT = Number(process.env.GCA_TURN_TIMEOUT ?? 240_000);
const KEEP = process.argv.slice(2).includes('--keep');
const SKIP_PREFLIGHT = process.argv.slice(2).includes('--skip-preflight');
const CLAUDE_BIN = process.env.GCA_CLAUDE ?? 'claude';
// Measured: a request the gateway REFUSES (429 / ExceededBudget) makes the CLI
// retry before it gives up and exits rc=1 — observed at 205s on 2026-09-17. The
// budget must exceed that, or this probe times out and reports nothing useful.
const PREFLIGHT_TIMEOUT = Number(process.env.GCA_PREFLIGHT_TIMEOUT ?? 300_000);

const ROW_SEL = '.msg.msg-user, .msg.msg-assistant';

const failures = [];
const measured = {};

function check(name, ok, detail) {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
    if (!ok) failures.push(name);
    return ok;
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

async function api(path, method = 'GET') {
    const res = await fetch(BASE_URL + path, { method });
    if (!res.ok) throw new Error(`${method} ${path} -> HTTP ${res.status}`);
    return res.status === 204 ? null : res.json();
}

async function convIds() {
    const list = await api('/api/conversations');
    return (list ?? []).map((c) => c.id);
}

/** Role of every transcript row, in DOM order. */
async function domRoles(page) {
    return page.$$eval(ROW_SEL, (els) =>
        els.map((e) => (e.className.includes('msg-user') ? 'user' : 'assistant'))
    );
}

async function serverRoles(id) {
    const data = await api(`/api/conversations/${id}`);
    return (data.messages ?? []).map((m) => m.role);
}

/** The live (non-superseded) assistant message: {id, content} or null. */
async function liveAssistantMessage(id) {
    const msgs = (await api(`/api/conversations/${id}`)).messages ?? [];
    return msgs.filter((m) => m.role === 'assistant').pop() ?? null;
}

async function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

/** What the user can actually see right now. Runs in the page. */
const domSnapshot = () => {
    const rows = [...document.querySelectorAll('.msg.msg-user, .msg.msg-assistant')];
    return {
        rows: rows.length,
        users: rows.filter((r) => r.className.includes('msg-user')).length,
        assistants: rows.filter((r) => r.className.includes('msg-assistant')).length,
        processing: document.querySelectorAll('.status-processing').length,
        regen: document.querySelectorAll('.regen-btn').length,
        assistantTexts: [...document.querySelectorAll('.msg.msg-assistant .text-block')]
            .map((e) => e.innerText.trim().slice(0, 20)),
    };
};

/**
 * Sample the transcript in-page, ten times a second, from before the click. A
 * single post-hoc count cannot see a row that is never removed.
 */
const installRowWatch = () => {
    window.__rowWatch = [];
    window.__rowWatchStart = performance.now();
    window.__rowWatchTimer = setInterval(() => {
        const rows = [...document.querySelectorAll('.msg.msg-user, .msg.msg-assistant')];
        window.__rowWatch.push({
            t: Math.round(performance.now() - window.__rowWatchStart),
            rows: rows.length,
            users: rows.filter((r) => r.className.includes('msg-user')).length,
            assistants: rows.filter((r) => r.className.includes('msg-assistant')).length,
            processing: document.querySelectorAll('.status-processing').length,
            regen: document.querySelectorAll('.regen-btn').length,
        });
    }, 100);
};

const readRowWatch = () => {
    clearInterval(window.__rowWatchTimer);
    return window.__rowWatch;
};

/** Collapse the sample stream to the moments where something changed. */
function compressTimeline(samples) {
    const out = [];
    let prev = null;
    for (const s of samples) {
        const k = `${s.rows}/${s.users}/${s.assistants}/${s.processing}/${s.regen}`;
        if (k !== prev) {
            out.push(s);
            prev = k;
        }
    }
    return out;
}

/** Poll the API until the conversation set grows by one, then return the new id. */
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

// --- Pre-flight: can the backend answer at all? ------------------------------
// Fail here, in seconds, naming the environment fault, rather than in ~12
// minutes naming the UI. See the header for why this exists.
if (!SKIP_PREFLIGHT) {
    console.log(`pre-flight: ${CLAUDE_BIN} -p "Reply with the single word: ok"`);
    const probe = spawnSync(`${CLAUDE_BIN} -p "Reply with the single word: ok"`, {
        encoding: 'utf8',
        timeout: PREFLIGHT_TIMEOUT,
        shell: true,
    });
    const said = `${probe.stdout ?? ''}${probe.stderr ?? ''}`.trim();
    const timedOut = probe.error?.code === 'ETIMEDOUT';
    const how = timedOut
        ? `no answer within ${PREFLIGHT_TIMEOUT}ms — the CLI was still retrying a refused request`
        : probe.error
            ? `spawn failed: ${probe.error.message}`
            : `exit ${probe.status}`;
    if (probe.status !== 0) {
        console.error(`ABORT — the \`claude\` CLI cannot answer (${how}).`);
        if (said) console.error(said);
        else if (timedOut) {
            console.error('The CLI printed nothing while retrying; a rejected request (budget, credentials)');
            console.error('usually means it never gets a response to report. Try it directly to see why:');
            console.error(`  ${CLAUDE_BIN} -p "say ok"`);
        }
        console.error('Regenerate cannot be proven while every request is refused.');
        console.error('This is an environment condition (budget, credentials, network), NOT an app defect.');
        console.error('Re-run with --skip-preflight to attempt the proof regardless.');
        process.exit(2);
    }
    console.log(`pre-flight: CLI answered (exit 0)${said ? ` — ${said.split('\n')[0].slice(0, 60)}` : ''}`);
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });

// A fresh context has an empty localStorage, which makes the app paint the
// onboarding tour on first load — a .ob-overlay [role="dialog"] that covers the
// sidebar and would swallow every click below. Seed the flag before any page
// script runs. Pin the assessment flag off so the send path is deterministic and
// does not sit behind a 9s heuristic race.
await context.addInitScript(() => {
    try {
        localStorage.setItem('gca_onboarded', '1');
        localStorage.setItem('gca_feat_assess', '0');
    } catch {
        /* no storage: the tour will show and the first click will fail loudly */
    }
});

const page = await context.newPage();

// Evidence the exit gate also cares about: a dialog (alert) or an uncaught error
// during a normal regenerate is itself a defect, so collect them.
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

const before = await convIds();
measured.conversations_before = before.length;
console.log(`baseline: ${before.length} conversation(s)`);

let scratchId = null;

try {
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#new-conv-btn', { timeout: 15_000 });

    // Mark the current composer so we can detect the fresh mount that follows the
    // switch. switchToConversation() awaits socket.connect() BEFORE mounting the
    // composer, so "a new composer exists" is a sound proxy for "the socket is
    // up" — without it the very first send can race the WebSocket and vanish.
    await page.evaluate((sel) => {
        const el = document.querySelector(sel);
        if (el) el.setAttribute('data-stale', '1');
    }, '#composer-input');

    await page.click('#new-conv-btn');
    scratchId = await waitForNewConversation(before);
    measured.scratch_id = scratchId;

    await page.waitForSelector('#composer-input:not([data-stale])', { timeout: 20_000 });

    // Guard: the scratch conversation must genuinely start empty, otherwise the
    // transcript assertions below could pass on content we did not create.
    const startRoles = await serverRoles(scratchId);
    check('scratch conversation starts empty', startRoles.length === 0, `roles=[${startRoles.join(',')}]`);

    await page.fill('#composer-input', PROMPT);
    await page.click('#composer-send');

    // .regen-btn exists only on a finished (status === "done") latest assistant
    // turn, so its arrival is an honest "the turn is complete" signal.
    await page.waitForSelector('.regen-btn', { timeout: TURN_TIMEOUT });

    const rolesAfterFirst = await domRoles(page);
    measured.rows_after_first_turn = rolesAfterFirst.length;
    measured.dom_after_first_turn = rolesAfterFirst.join(',');
    check(
        'first turn renders [user][assistant]',
        same(rolesAfterFirst, ['user', 'assistant']),
        `[${rolesAfterFirst.join(',')}]`
    );

    const srvAfterFirst = await serverRoles(scratchId);
    measured.server_after_first_turn = srvAfterFirst.join(',');
    check(
        'first turn persisted as [user][assistant]',
        same(srvAfterFirst, ['user', 'assistant']),
        `[${srvAfterFirst.join(',')}]`
    );

    // The baseline the regenerate must move: one step billed, one answer live.
    const msgBefore = await liveAssistantMessage(scratchId);
    const answerBefore = msgBefore?.content ?? '';
    const statsBefore = await api(`/api/conversations/${scratchId}/stats`);
    measured.assistant_msg_before_regen = msgBefore?.id;
    measured.answer_before_regen = answerBefore.slice(0, 40);
    measured.step_count_before_regen = statsBefore.step_count;
    check(
        'first turn bills exactly one step and leaves one live answer',
        statsBefore.step_count === 1 && !!msgBefore?.id && answerBefore.length > 0,
        `step_count=${statsBefore.step_count} msg=${msgBefore?.id} answer=${JSON.stringify(answerBefore.slice(0, 20))}`
    );

    // ── the click under test ────────────────────────────────────────────────
    await page.evaluate(installRowWatch);
    await page.click('.regen-btn');

    const deadline = Date.now() + TURN_TIMEOUT;
    let replaced = null;
    let lastSeen = null;
    while (Date.now() < deadline) {
        const live = await liveAssistantMessage(scratchId);
        const dom = await page.evaluate(domSnapshot);
        lastSeen = { liveMsg: live?.id, liveText: (live?.content ?? '').slice(0, 20), ...dom };
        if (
            live?.id && live.id !== msgBefore?.id && (live.content ?? '').trim().length > 0
            && dom.assistants === 1 && dom.users === 1 && dom.processing === 0 && dom.regen === 1
        ) {
            replaced = live;
            break;
        }
        await sleep(1000);
    }

    const timeline = compressTimeline(await page.evaluate(readRowWatch));
    measured.dom_timeline = timeline.map((s) => `+${s.t}ms rows=${s.rows} u=${s.users} a=${s.assistants} busy=${s.processing} regen=${s.regen}`);

    check(
        'ONE click replaced the live answer with a new one, no further click',
        !!replaced && replaced.id !== msgBefore?.id,
        replaced
            ? `live assistant row ${msgBefore?.id} -> ${replaced.id}`
            : `no replacement within ${(TURN_TIMEOUT / 1000).toFixed(0)}s; last seen ${JSON.stringify(lastSeen)}`
    );

    const rolesAfterRegen = await domRoles(page);
    measured.rows_after_regen = rolesAfterRegen.length;
    measured.dom_after_regen = rolesAfterRegen.join(',');
    check(
        'transcript stays [user][assistant] — never [user][user][assistant]',
        same(rolesAfterRegen, ['user', 'assistant']),
        `[${rolesAfterRegen.join(',')}] (${rolesAfterRegen.length} row(s))`
    );

    // The same invariant, sampled continuously across the click. This catches a
    // superseded row that is never removed. It cannot catch a sub-100ms transient,
    // because the sampler is a timer and the removal happens inside the click
    // task — stated plainly rather than implied.
    const peakAssistants = timeline.length ? Math.max(...timeline.map((s) => s.assistants)) : 0;
    const peakUsers = timeline.length ? Math.max(...timeline.map((s) => s.users)) : 0;
    check(
        'never two assistant rows or two user rows in the sampled DOM',
        peakAssistants <= 1 && peakUsers <= 1,
        `peak assistants=${peakAssistants} peak users=${peakUsers} over ${timeline.length} state change(s)`
    );

    const srvAfterRegen = await serverRoles(scratchId);
    measured.server_after_regen = srvAfterRegen.join(',');
    check(
        'server transcript also stays [user][assistant]',
        same(srvAfterRegen, ['user', 'assistant']),
        `[${srvAfterRegen.join(',')}]`
    );

    // Non-empty-set guard: a hidden row is only meaningful if a live answer
    // actually replaced it, and the replaced text is reported either way.
    const msgAfter = await liveAssistantMessage(scratchId);
    const answerAfter = msgAfter?.content ?? '';
    measured.assistant_msg_after_regen = msgAfter?.id;
    measured.answer_after_regen = answerAfter.slice(0, 40);
    check(
        'the superseded answer is no longer the live one',
        !!msgAfter?.id && msgAfter.id !== msgBefore?.id && answerAfter.trim().length > 0,
        `live ${msgBefore?.id} -> ${msgAfter?.id}; text ${JSON.stringify(answerBefore.slice(0, 20))} -> ${JSON.stringify(answerAfter.slice(0, 20))}`
    );

    const liveAssistant = await page.$$eval('.msg.msg-assistant .text-block', (els) =>
        els.map((e) => e.innerText.trim())
    );
    measured.live_assistant_texts = liveAssistant.map((t) => t.slice(0, 40));
    check(
        'the surviving assistant row has content',
        liveAssistant.length > 0 && liveAssistant.every((t) => t.length > 0),
        JSON.stringify(measured.live_assistant_texts)
    );

    // The accounting carve-out, measured in band: two assistant turns were billed
    // even though only one is live. If the stats query had inherited the
    // superseded filter this would read 1.
    const stats = await api(`/api/conversations/${scratchId}/stats`);
    measured.step_count_after_regen = stats.step_count;
    measured.tokens_in_after_regen = stats.tokens_in;
    check(
        'stats still count the superseded turn (2 steps, 1 live)',
        stats.step_count === 2 && stats.tokens_in > 0,
        `step_count=${stats.step_count} tokens_in=${stats.tokens_in}`
    );

    const exported = await api(`/api/conversations/${scratchId}/export`);
    measured.export_chars = (exported.markdown ?? '').length;
    check('export still renders', measured.export_chars > 0, `${measured.export_chars} chars`);
} catch (err) {
    check('run completed without throwing', false, String(err.message ?? err));
} finally {
    if (scratchId && !KEEP) {
        try {
            await api(`/api/conversations/${scratchId}`, 'DELETE');
            const after = await convIds();
            measured.conversations_after = after.length;
            check(
                'scratch conversation deleted; count restored',
                after.length === before.length && !after.includes(scratchId),
                `${after.length} conversation(s)`
            );
        } catch (err) {
            check('cleanup deleted the scratch conversation', false, String(err.message ?? err));
        }
    } else if (KEEP) {
        console.log(`--keep: leaving conversation ${scratchId} in place`);
    }

    check('no alert()/dialog fired during the flow', dialogs.length === 0, dialogs.join(' | ') || 'none');
    check('no uncaught page error', pageErrors.length === 0, pageErrors.join(' | ') || 'none');
    if (consoleErrors.length) {
        console.log(`WARN  ${consoleErrors.length} console error(s): ${consoleErrors.join(' | ')}`);
    } else {
        console.log('PASS  console clean');
    }

    await context.close();
    await browser.close();
}

console.log('\nMEASURED');
for (const [k, v] of Object.entries(measured)) {
    console.log(`  ${k} = ${Array.isArray(v) ? JSON.stringify(v) : v}`);
}

if (failures.length) {
    console.log(`\nRESULT: FAIL (${failures.length}) — ${failures.join('; ')}`);
    process.exit(1);
}
console.log('\nRESULT: PASS');
process.exit(0);
