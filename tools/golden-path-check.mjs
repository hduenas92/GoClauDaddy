/**
 * golden-path-check.mjs — 5-3. The twelve steps are DEFINED in
 * plans/goclaudaddy-v1-plan.md, section "5-3 — the golden path, defined".
 * That document is the contract; this file is only its instrument. If the two
 * disagree, the plan wins and this file is wrong.
 *
 *   node tools/golden-path-check.mjs                 run all twelve
 *   node tools/golden-path-check.mjs --skip-live     skip the 3 CLI turns (exit 3)
 *   node tools/golden-path-check.mjs --skip-preflight
 *   node tools/golden-path-check.mjs --keep          leave the project/conversation
 *   node tools/golden-path-check.mjs --headed
 *
 * WHY IT IS DRIVEN THROUGH THE UI
 * A golden path asserted over HTTP proves the backend and then calls the result
 * a user journey. That is the wrong-measurement-surface vacuity that has
 * already cost this project three instruments (see the 5-4 notes). So every
 * step is performed the way a person performs it — click, type, confirm — and
 * then verified a second time against the server. A step that passes on one
 * surface and not the other is a FAIL, and both observed values are printed.
 *
 * ORDER
 * send (4) -> regenerate (5) -> attach+send (6) -> stop (7). Regenerate comes
 * before stop because finishAssistantMessage paints .regen-btn ONLY on status
 * "done": a stopped turn renders no Regenerate button, so stopping first would
 * leave step 5 waiting on a signal that can never arrive. regen-check.mjs
 * documents that exact failure; this file does not repeat it.
 *
 * EXIT CODES
 *   0  all executed steps passed
 *   1  at least one step failed
 *   2  environment fault (app unreachable, or the CLI cannot answer at all)
 *   3  passed, but live steps were skipped — NOT a green golden path
 *
 * "Not tested" and "passed" are different results. --skip-live therefore cannot
 * exit 0, however clean the other nine steps are.
 */

import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';

const BASE_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const CLAUDE_BIN = process.env.GCA_CLAUDE ?? 'claude';
const TURN_TIMEOUT = Number(process.env.GCA_TURN_TIMEOUT ?? 240_000);
const PREFLIGHT_TIMEOUT = Number(process.env.GCA_PREFLIGHT_TIMEOUT ?? 300_000);

const argv = process.argv.slice(2);
const SKIP_LIVE = argv.includes('--skip-live');
const SKIP_PREFLIGHT = argv.includes('--skip-preflight');
const KEEP = argv.includes('--keep');
const HEADED = argv.includes('--headed');

/** One token, alphanumeric so FTS5 keeps it whole, unique per run. */
const MARK = 'GPMARK' + Math.random().toString(16).slice(2, 12);
const PROMPT_1 = `Reply with the single word OK. Ref ${MARK}`;
const PROMPT_2 = `Describe the attached file in one short sentence. Ref ${MARK}`;
const PROJECT_NAME = `GP ${MARK}`;
const RENAMED = `GP renamed ${MARK}`;

const failures = [];
const skipped = [];
const evidence = {};

function check(name, ok, detail) {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
    if (!ok) failures.push(name);
    return ok;
}
function skip(name, why) {
    console.log(`SKIP  ${name} — ${why}`);
    skipped.push(name);
}
function step(n, title) {
    console.log(`\n--- step ${n}: ${title}`);
}

class Fatal extends Error { }

/** HTTP helper. `raw:true` returns {status, body} instead of throwing on !ok. */
async function api(path, { method = 'GET', raw = false } = {}) {
    const res = await fetch(BASE_URL + path, { method });
    let body = null;
    const text = await res.text();
    if (text) { try { body = JSON.parse(text); } catch { body = text; } }
    if (raw) return { status: res.status, body };
    if (!res.ok) throw new Fatal(`${method} ${path} -> HTTP ${res.status}: ${String(body).slice(0, 200)}`);
    return body;
}

/**
 * Bounded poll that REPORTS what it last saw.
 *
 * A bare waitForFunction tells you only that time passed, which makes "the
 * server never answered", "the DOM never went idle" and "the row was never
 * replaced" all look like the same hang. `fn` returns {done, seen}.
 */
async function poll(label, fn, timeout = TURN_TIMEOUT, interval = 500) {
    const t0 = Date.now();
    let seen = '(never observed)';
    while (Date.now() - t0 < timeout) {
        let r;
        try { r = await fn(); } catch (e) { r = { done: false, seen: `threw: ${e.message}` }; }
        seen = r.seen ?? seen;
        if (r.done) return { ok: true, seen, ms: Date.now() - t0 };
        await new Promise((r2) => setTimeout(r2, interval));
    }
    return { ok: false, seen, ms: Date.now() - t0, label };
}

/** Roles in server order, and whether they strictly alternate from "user". */
function shapeOf(messages) {
    const roles = (messages ?? []).map((m) => m.role);
    if (roles.length === 0) return { roles, wellFormed: false, why: 'transcript is EMPTY' };
    if (roles[0] !== 'user') return { roles, wellFormed: false, why: `starts with ${roles[0]}` };
    for (let i = 1; i < roles.length; i++) {
        if (roles[i] === roles[i - 1]) return { roles, wellFormed: false, why: `${roles[i]} repeats at index ${i}` };
    }
    return { roles, wellFormed: true, why: '' };
}

function lastAssistant(messages) {
    const a = (messages ?? []).filter((m) => m.role === 'assistant');
    return a.length ? a[a.length - 1] : null;
}

/**
 * Type into the composer and send — defensively, because the composer is
 * REBUILT during a conversation switch.
 *
 * switchToConversation() ends with mountComposer(), which overwrites
 * #composer.innerHTML, so #composer-input and #composer-send are replaced by
 * new elements. Text typed into the old textarea is discarded with it. The
 * first live run of this harness lost its whole first turn that way: fill()
 * landed in the doomed textarea, click() hit the fresh (empty) Send, and the
 * app correctly did nothing — 240s of polling an empty transcript.
 *
 * So: type, then READ IT BACK, and retype if the box came back empty. A user
 * hits the same race (type fast after New Chat and the message vanishes with
 * no error); that is recorded as a 5-6 finding rather than worked around
 * silently here.
 */
async function typeAndSend(page, text, settleMs = 8_000) {
    const t0 = Date.now();
    let landed = false;
    while (Date.now() - t0 < settleMs) {
        await page.fill('#composer-input', text);
        await page.waitForTimeout(250);
        const v = await page.$eval('#composer-input', (el) => el.value).catch(() => '');
        if (v === text) { landed = true; break; }
    }
    if (!landed) {
        const v = await page.$eval('#composer-input', (el) => el.value).catch(() => '(no composer)');
        return { ok: false, why: `the composer would not hold the text — last read ${JSON.stringify(String(v).slice(0, 80))}` };
    }
    await page.click('#composer-send');
    return { ok: true, why: `text held after ${Date.now() - t0}ms` };
}

/**
 * Pre-flight the CLI. A refused request (exhausted budget, expired token) makes
 * the CLI retry for minutes and then exit 1 with no stderr; the app emits error
 * then done and renders nothing, so every live assertion below fails as a
 * timeout and the report blames the UI for an environment fault.
 */
function preflight() {
    const r = spawnSync(CLAUDE_BIN, ['-p', 'Reply with the single word OK.'], {
        encoding: 'utf8', timeout: PREFLIGHT_TIMEOUT, shell: process.platform === 'win32',
    });
    if (r.error) return { ok: false, why: `could not run \`${CLAUDE_BIN}\`: ${r.error.message}` };
    if (r.status !== 0) {
        return { ok: false, why: `\`${CLAUDE_BIN} -p\` exited ${r.status}. Its own output:\n${(r.stderr || r.stdout || '(silent)').trim().slice(0, 800)}` };
    }
    return { ok: true, why: (r.stdout || '').trim().slice(0, 60) };
}

// ---------------------------------------------------------------- the journey

async function run() {
    // Environment first, so a dead app is never reported as twelve UI bugs.
    try {
        await api('/api/server/info');
    } catch (e) {
        console.error(`ENVIRONMENT: the app is not answering at ${BASE_URL}.\n${e.message}`);
        process.exit(2);
    }
    if (!SKIP_LIVE && !SKIP_PREFLIGHT) {
        const pf = preflight();
        if (!pf.ok) {
            console.error(`ENVIRONMENT: the Claude CLI cannot answer, so steps 4-7 cannot be judged.\n${pf.why}`);
            process.exit(2);
        }
        console.log(`preflight ok — CLI answered ${JSON.stringify(pf.why)}`);
    }

    const baseProjects = (await api('/api/projects')).length;
    const baseConvs = (await api('/api/conversations')).length;
    console.log(`baseline: ${baseProjects} projects, ${baseConvs} conversations · mark ${MARK}`);

    const tmp = mkdtempSync(join(tmpdir(), 'gca-gp-'));
    const attachPath = join(tmp, `${MARK}.txt`);
    writeFileSync(attachPath, `This file exists only to be attached by the golden path check. ${MARK}\n`);

    const browser = await chromium.launch({ headless: !HEADED });
    const context = await browser.newContext({ acceptDownloads: true });
    const page = await context.newPage();

    const pageErrors = [];
    const consoleErrors = [];
    page.on('pageerror', (e) => pageErrors.push(e.message));
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });

    let projectId = null;
    let convId = null;
    let attachmentId = null;
    let messageIdFound = null;

    try {
        // -- 1 -------------------------------------------------------------
        step(1, 'app loads cold');
        await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#composer-send', { timeout: 30_000 });
        const shell = await page.evaluate(() => ({
            sidebar: !!document.querySelector('#sidebar'),
            header: !!document.querySelector('#chat-header'),
            composer: !!document.querySelector('#composer-input'),
            bootError: (document.querySelector('#app > pre')?.textContent ?? '').slice(0, 200),
        }));
        check('1 shell renders (sidebar + header + composer)',
            shell.sidebar && shell.header && shell.composer, JSON.stringify(shell));
        check('1 no uncaught page error', pageErrors.length === 0,
            pageErrors.length ? pageErrors.join(' | ').slice(0, 300) : 'none');
        if (consoleErrors.length) console.log(`      note: ${consoleErrors.length} console error(s): ${consoleErrors.join(' | ').slice(0, 300)}`);
        const info = await api('/api/server/info');
        check('1 server answers /api/server/info', !!info, JSON.stringify(info).slice(0, 120));

        // The tour is the first thing a new user meets, and while it is up
        // every control underneath it is unclickable — the first run of this
        // harness aborted at step 2 on exactly that. Walking it is part of the
        // journey, not setup to route around. It is keyed on
        // localStorage.gca_onboarded, so a fresh profile always shows it and a
        // persisted one never does; both are legitimate ways to run this.
        const tour = await page.$('.ob-overlay');
        if (!tour) {
            check('1 onboarding tour', true, 'not shown — this profile is already onboarded');
        } else {
            let pages = 1;
            for (; pages <= 10; pages++) {
                const next = await page.$('.ob-overlay [data-action="next"]');
                if (!next) break;
                await next.click();
            }
            const finish = await page.$('.ob-overlay [data-action="finish"]');
            check('1 the tour reaches its last page', !!finish, `walked ${pages} page(s)`);
            if (finish) await finish.click();
            const p1 = await poll('the overlay goes away', async () => {
                const still = await page.$('.ob-overlay');
                return { done: !still, seen: still ? 'overlay still up' : 'overlay dismissed' };
            }, 10_000, 200);
            check('1 finishing the tour dismisses it', p1.ok, p1.seen);
            const flag = await page.evaluate(() => { try { return localStorage.getItem('gca_onboarded'); } catch { return 'unreadable'; } });
            check('1 the tour records that it was seen', flag === '1',
                `localStorage.gca_onboarded=${JSON.stringify(flag)} — if this is not "1" the tour returns on every launch`);
        }

        // -- 2 -------------------------------------------------------------
        step(2, 'create a project with a working directory');
        const workingDir = process.cwd();
        await page.click('#new-project-btn');
        await page.waitForSelector('.modal-overlay .modal-input[name="name"]');
        await page.fill('.modal-input[name="name"]', PROJECT_NAME);
        await page.fill('.modal-input[name="working_dir"]', workingDir);
        await page.click('.modal-confirm');
        const p2 = await poll('project appears', async () => {
            const names = await page.$$eval('.project-name', (els) => els.map((e) => e.textContent));
            return { done: names.includes(PROJECT_NAME), seen: JSON.stringify(names).slice(0, 200) };
        }, 15_000);
        check('2 project appears in the sidebar', p2.ok, p2.seen);
        const projects = await api('/api/projects');
        const proj = projects.find((p) => p.name === PROJECT_NAME);
        check('2 project exists on the server', !!proj, proj ? proj.id : `server sees ${projects.length} projects`);
        if (!proj) throw new Fatal('no project to hang the rest of the journey on');
        projectId = proj.id;
        check('2 working_dir round-tripped', proj.working_dir === workingDir,
            `server=${JSON.stringify(proj.working_dir)} sent=${JSON.stringify(workingDir)}`);

        // -- 3 -------------------------------------------------------------
        step(3, 'create a conversation inside that project');
        // createConversation() reads activeProjectId from the store, so the
        // project must actually be SELECTED first — clicking its name is what a
        // user does and is what sets it.
        await page.click(`.project-item .project-name:text-is(${JSON.stringify(PROJECT_NAME)})`);
        await page.click('#new-conv-btn');
        const p3 = await poll('conversation appears on the server', async () => {
            const list = await api('/api/conversations');
            const mine = list.filter((c) => c.project_id === projectId);
            return { done: mine.length === 1, seen: `${mine.length} in project, ${list.length} total` };
        }, 20_000);
        check('3 exactly one conversation in the new project', p3.ok, p3.seen);
        const convs = await api('/api/conversations');
        const conv = convs.find((c) => c.project_id === projectId);
        if (!conv) throw new Fatal('conversation was not created; the journey cannot continue');
        convId = conv.id;
        // Poll, do not read once: switchToConversation() is async and ends by
        // remounting the composer, so the sidebar's active marker lands some
        // way after the server row exists. Reading immediately measured the
        // race, not the app.
        const p3b = await poll('the new conversation becomes active in the UI', async () => {
            const names = await page.$$eval('.conv-item.active .conv-name', (els) => els.map((e) => e.textContent.trim()));
            return { done: names.length === 1, seen: `${names.length} active item(s): ${JSON.stringify(names)}` };
        }, 20_000, 250);
        check('3 the new conversation is selected in the UI', p3b.ok, `${p3b.seen} after ${p3b.ms}ms`);
        check('3 it is bound to the project', conv.project_id === projectId,
            `server project_id=${conv.project_id} expected=${projectId}`);

        // -- 4 -------------------------------------------------------------
        step(4, 'send the first message and receive a streamed reply');
        if (SKIP_LIVE) {
            skip('4 first turn', '--skip-live');
            skip('5 regenerate', '--skip-live');
            skip('6 attach and send', '--skip-live');
            skip('7 stop mid-stream', '--skip-live');
        } else {
            const sent4 = await typeAndSend(page, PROMPT_1);
            check('4 the message could be typed and sent', sent4.ok, sent4.why);
            // Prove the turn STARTED before spending 240s proving it finished.
            // Without this, a silently-dropped send is indistinguishable from a
            // slow model, and the report blames the wrong thing.
            const started4 = await poll('the turn starts', async () => {
                const busy = await page.$eval('#composer-stop', (b) => !b.hidden).catch(() => false);
                const { messages } = await api(`/api/conversations/${convId}`);
                return { done: busy || messages.length > 0, seen: `busy=${busy} rows=${messages.length}` };
            }, 30_000, 250);
            check('4 the send actually started a turn', started4.ok,
                started4.ok ? started4.seen : `${started4.seen} — Send was clicked but nothing began`);
            const p4 = await poll('first turn completes', async () => {
                const { messages } = await api(`/api/conversations/${convId}`);
                const idle = await page.$eval('#composer-stop', (b) => b.hidden).catch(() => false);
                const a = lastAssistant(messages);
                const body = (a?.content ?? '').trim();
                return {
                    done: idle && !!a && body.length > 0,
                    seen: `roles=${JSON.stringify(shapeOf(messages).roles)} assistantChars=${body.length} idle=${idle}`,
                };
            });
            check('4 the turn completed and the assistant said something', p4.ok, `${p4.seen} in ${p4.ms}ms`);
            const { messages: m4 } = await api(`/api/conversations/${convId}`);
            const s4 = shapeOf(m4);
            // Non-empty FIRST: an empty transcript satisfies every shape rule
            // there is, which is how a turn that never happened passes a check
            // written about turns that did.
            check('4 transcript is non-empty', s4.roles.length > 0, `roles=${JSON.stringify(s4.roles)}`);
            check('4 transcript is exactly [user, assistant]',
                JSON.stringify(s4.roles) === JSON.stringify(['user', 'assistant']),
                `roles=${JSON.stringify(s4.roles)}`);
            const domRoles4 = await page.$$eval('.msg.msg-user, .msg.msg-assistant',
                (els) => els.map((e) => (e.className.includes('msg-user') ? 'user' : 'assistant')));
            check('4 the DOM shows the same transcript as the server',
                JSON.stringify(domRoles4) === JSON.stringify(s4.roles),
                `dom=${JSON.stringify(domRoles4)} server=${JSON.stringify(s4.roles)}`);
            evidence.firstAnswer = (lastAssistant(m4)?.content ?? '').trim();

            // -- 5 ---------------------------------------------------------
            step(5, 'regenerate that answer');
            const beforeId = lastAssistant(m4)?.id ?? null;
            const regen = await page.$('.regen-btn');
            if (!regen) {
                check('5 a Regenerate button is offered on a completed turn', false,
                    'no .regen-btn in the DOM — finishAssistantMessage paints it only on status "done"');
            } else {
                await regen.click();
                const p5 = await poll('answer is replaced', async () => {
                    const { messages } = await api(`/api/conversations/${convId}`);
                    const a = lastAssistant(messages);
                    const idle = await page.$eval('#composer-stop', (b) => b.hidden).catch(() => false);
                    return {
                        done: idle && !!a && a.id !== beforeId && (a.content ?? '').trim().length > 0,
                        seen: `liveAssistant=${a?.id ?? 'none'} was=${beforeId} idle=${idle} roles=${JSON.stringify(shapeOf(messages).roles)}`,
                    };
                });
                check('5 the old answer was superseded by a new one', p5.ok, `${p5.seen} in ${p5.ms}ms`);
                const { messages: m5 } = await api(`/api/conversations/${convId}`);
                check('5 the transcript is still [user, assistant] — no doubled user turn',
                    JSON.stringify(shapeOf(m5).roles) === JSON.stringify(['user', 'assistant']),
                    `roles=${JSON.stringify(shapeOf(m5).roles)}`);
            }

            // -- 6 ---------------------------------------------------------
            step(6, 'attach a file and send with it');
            const uploaded = page.waitForResponse(
                (r) => r.url().includes('/api/attachments') && r.request().method() === 'POST',
                { timeout: 30_000 });
            await page.setInputFiles('#composer-file-input', attachPath);
            let upBody = null;
            try { upBody = await (await uploaded).json(); } catch { /* reported below */ }
            attachmentId = upBody?.id ?? null;
            check('6 the upload returned an attachment', !!attachmentId,
                attachmentId ? `${attachmentId} (${upBody?.original_name})` : 'POST /api/attachments produced no id');
            const chips = await page.$$eval('.attachment-chip', (els) => els.map((e) => e.textContent));
            check('6 a chip for the file shows in the composer', chips.length === 1,
                JSON.stringify(chips).slice(0, 200));
            if (attachmentId) {
                const dl = await api(`/api/attachments/${attachmentId}/download`, { raw: true });
                check('6 the attachment is downloadable and holds the file',
                    dl.status === 200 && String(dl.body).includes(MARK),
                    `HTTP ${dl.status}, ${String(dl.body).length} bytes`);
            }
            const sent6 = await typeAndSend(page, PROMPT_2);
            check('6 the message could be typed and sent with the file attached', sent6.ok, sent6.why);

            // -- 7 ---------------------------------------------------------
            step(7, 'stop that turn mid-stream');
            const started = await poll('the turn starts streaming', async () => {
                const busy = await page.$eval('#composer-stop', (b) => !b.hidden).catch(() => false);
                return { done: busy, seen: `stopButtonVisible=${busy}` };
            }, 60_000, 200);
            check('7 the Stop button appears while the turn runs', started.ok, started.seen);
            if (started.ok) {
                await page.waitForTimeout(1500);   // let the turn be genuinely in flight
                await page.click('#composer-stop');
                const p7 = await poll('the app returns to idle', async () => {
                    const idle = await page.$eval('#composer-stop', (b) => b.hidden).catch(() => false);
                    return { done: idle, seen: `stopButtonHidden=${idle}` };
                }, 90_000, 250);
                check('7 stopping returns the app to idle', p7.ok, `${p7.seen} in ${p7.ms}ms`);
                const { messages: m7 } = await api(`/api/conversations/${convId}`);
                const s7 = shapeOf(m7);
                check('7 the transcript survived the stop intact', s7.wellFormed,
                    s7.wellFormed ? `roles=${JSON.stringify(s7.roles)}` : `${s7.why} — roles=${JSON.stringify(s7.roles)}`);
            }
        }

        // -- 8 ---------------------------------------------------------------
        step(8, 'change model and thinking mid-conversation');
        await page.click('#settings-btn');
        await page.waitForSelector('#model-select', { timeout: 10_000 });
        const before8 = (await api(`/api/conversations/${convId}`)).conversation;
        const modelOpts = await page.$$eval('#model-select option', (els) => els.map((e) => e.value).filter(Boolean));
        const thinkOpts = await page.$$eval('#thinking-select option', (els) => els.map((e) => e.value).filter(Boolean));
        check('8 the drawer offers models and thinking levels to choose from',
            modelOpts.length > 1 && thinkOpts.length > 0,
            `models=${modelOpts.length} thinking=${thinkOpts.length}`);
        const newModel = modelOpts.find((m) => m !== before8.model) ?? modelOpts[0];
        const newThink = thinkOpts[thinkOpts.length - 1];
        if (newModel) await page.selectOption('#model-select', newModel);
        if (newThink) await page.selectOption('#thinking-select', newThink);
        const p8 = await poll('settings persist', async () => {
            const c = (await api(`/api/conversations/${convId}`)).conversation;
            return {
                done: c.model === newModel && String(c.thinking_budget ?? '') === String(newThink),
                seen: `server model=${c.model} thinking=${c.thinking_budget}`,
            };
        }, 20_000);
        check('8 the change reached the server', p8.ok,
            `${p8.seen} · chose model=${newModel} thinking=${newThink}`);
        await page.click('#settings-btn');   // close the drawer again

        // -- 9 ---------------------------------------------------------------
        step(9, 'rename the conversation');
        await page.click('.conv-item.active .conv-rename');
        await page.waitForSelector('.modal-overlay .modal-input[name="name"]');
        await page.fill('.modal-input[name="name"]', RENAMED);
        await page.click('.modal-confirm');
        const p9 = await poll('the new name shows in the list', async () => {
            const names = await page.$$eval('.conv-item .conv-name', (els) => els.map((e) => e.textContent.trim()));
            return { done: names.includes(RENAMED), seen: JSON.stringify(names).slice(0, 200) };
        }, 15_000);
        check('9 the sidebar shows the new name', p9.ok, p9.seen);
        const c9 = (await api(`/api/conversations/${convId}`)).conversation;
        check('9 the server agrees', c9.name === RENAMED, `server=${JSON.stringify(c9.name)} expected=${JSON.stringify(RENAMED)}`);

        // -- 10 --------------------------------------------------------------
        step(10, 'find it again by searching its content');
        if (SKIP_LIVE) {
            skip('10 search finds the conversation', 'nothing was sent, so there is no content to find');
            skip('11 export', 'nothing was sent, so an export would prove nothing');
        } else {
            await page.fill('#conv-search', MARK);
            const p10 = await poll('a message hit appears', async () => {
                const hits = await page.$$eval('.conv-item.conv-hit', (els) => els.length);
                return { done: hits > 0, seen: `${hits} message hit(s) in the sidebar` };
            }, 15_000, 250);
            check('10 the search box surfaces a message hit', p10.ok, p10.seen);
            const rows = await api(`/api/search?q=${encodeURIComponent(MARK)}`);
            const list = Array.isArray(rows) ? rows : (rows.results ?? rows.rows ?? []);
            const mine = list.filter((r) => r.conversation_id === convId);
            check('10 the server finds the message too', mine.length > 0,
                `${mine.length} of ${list.length} row(s) belong to this conversation`);
            messageIdFound = mine[0]?.message_id ?? null;
            await page.fill('#conv-search', '');
            await page.waitForSelector('.conv-item.active', { timeout: 10_000 });

            // -- 11 ----------------------------------------------------------
            step(11, 'export it');
            const dlPromise = page.waitForEvent('download', { timeout: 20_000 });
            await page.click('#export-btn');
            let md = '';
            try {
                const dl = await dlPromise;
                const stream = await dl.createReadStream();
                md = await new Promise((res, rej) => {
                    let buf = '';
                    stream.on('data', (d) => { buf += d.toString('utf8'); });
                    stream.on('end', () => res(buf));
                    stream.on('error', rej);
                });
            } catch (e) {
                check('11 clicking Export downloads a file', false, e.message);
            }
            if (md) {
                check('11 clicking Export downloads a file', true, `${md.length} chars`);
                check('11 the export contains the question that was asked', md.includes(MARK),
                    `mark ${MARK} ${md.includes(MARK) ? 'present' : 'ABSENT'}`);
                const ans = (evidence.firstAnswer ?? '').slice(0, 12);
                const srv = (await api(`/api/conversations/${convId}/export`)).markdown ?? '';
                check('11 the downloaded file matches what the endpoint serves',
                    md.trim() === srv.trim(),
                    `download=${md.length} chars endpoint=${srv.length} chars`);
                check('11 an assistant answer is in the export', ans.length === 0 || md.includes(ans),
                    ans.length === 0 ? 'no answer text was recorded to look for' : `looked for ${JSON.stringify(ans)}`);
            }
        }

        // -- 12 --------------------------------------------------------------
        step(12, 'delete it, and confirm it is gone');
        if (KEEP) {
            skip('12 delete', '--keep');
        } else {
            await page.click('.conv-item.active .conv-delete');
            await page.waitForSelector('.modal-overlay .modal-confirm');
            await page.click('.modal-overlay .modal-confirm');
            const p12 = await poll('it leaves the list', async () => {
                const list = await api('/api/conversations');
                const still = list.some((c) => c.id === convId);
                return { done: !still, seen: `${list.length} conversation(s), mine ${still ? 'still present' : 'gone'}` };
            }, 20_000);
            check('12 it is gone from the conversation list', p12.ok, p12.seen);
            const direct = await api(`/api/conversations/${convId}`, { raw: true });
            check('12 fetching it directly is a 404', direct.status === 404, `HTTP ${direct.status}`);
            // The attachment is the removal proof this harness CAN see.
            // /api/attachments/{id}/download is keyed on the attachment id
            // alone, so a row that outlives its conversation is a file that is
            // still downloadable by anyone holding the id. Unlike the search
            // assertion below, this one has no join hiding the leak.
            if (attachmentId) {
                const gone = await api(`/api/attachments/${attachmentId}/download`, { raw: true });
                check('12 the attachment is no longer downloadable', gone.status === 404,
                    `HTTP ${gone.status} — anything but 404 means the row outlived its conversation`);
            }
            if (!SKIP_LIVE) {
                // NOT the leak check it looks like. Mutation testing (5-3 M1,
                // PRAGMA foreign_keys = OFF) left 8 orphaned message rows in
                // the DB and this assertion still reported "0 rows match": the
                // search SQL inner-joins `conversations`, so a message whose
                // conversation row is gone leaves the results whether or not
                // the message itself was deleted. What this proves is the
                // user-facing half — deleted content does not surface in
                // search — and that is worth keeping. The other half, "the rows
                // are actually gone", is asserted where it is visible, in
                // backend/tests/test_delete_cascade.py.
                // Step 10 already proved this content WAS findable, so an empty
                // result here is removal rather than a search that never worked.
                const rows = await api(`/api/search?q=${encodeURIComponent(MARK)}`, { raw: true });
                const list = Array.isArray(rows.body) ? rows.body : (rows.body?.results ?? rows.body?.rows ?? []);
                const leftovers = list.filter((r) => r.conversation_id === convId);
                check('12 its messages are gone from search too', leftovers.length === 0,
                    leftovers.length
                        ? `${leftovers.length} orphaned FTS row(s) still match ${MARK} — deleted content is still searchable`
                        : `0 rows match ${MARK}${messageIdFound ? ` (was findable as ${messageIdFound})` : ''}`);
            }
            convId = null;
        }
    } catch (e) {
        if (e instanceof Fatal) {
            console.error(`\nABORTED — ${e.message}`);
            failures.push('journey aborted');
        } else {
            console.error(`\nABORTED — unexpected: ${e.stack ?? e.message}`);
            failures.push('journey aborted (unexpected)');
        }
    } finally {
        // Clean up through the UI where it still can, and through the API where
        // the UI is no longer in a fit state — a harness that leaves debris
        // cannot be run twice, and a path that can only be walked once is not a
        // path.
        if (!KEEP) {
            try { if (convId) await api(`/api/conversations/${convId}`, { method: 'DELETE' }); } catch { }
            try { if (projectId) await api(`/api/projects/${projectId}`, { method: 'DELETE' }); } catch { }
        }
        await context.close().catch(() => { });
        await browser.close().catch(() => { });
        rmSync(tmp, { recursive: true, force: true });
    }

    if (!KEEP) {
        const endProjects = (await api('/api/projects')).length;
        const endConvs = (await api('/api/conversations')).length;
        check('cleanup leaves the counts as it found them',
            endProjects === baseProjects && endConvs === baseConvs,
            `projects ${baseProjects}->${endProjects}, conversations ${baseConvs}->${endConvs}`);
    }

    console.log('\n' + '='.repeat(64));
    if (failures.length) {
        console.log(`GOLDEN PATH: FAIL — ${failures.length} check(s) failed:`);
        failures.forEach((f) => console.log(`  - ${f}`));
        process.exit(1);
    }
    if (skipped.length) {
        console.log(`GOLDEN PATH: INCOMPLETE — every executed check passed, but ${skipped.length} were skipped:`);
        skipped.forEach((s) => console.log(`  - ${s}`));
        console.log('"Not tested" and "passed" are different results. Exit 3, not 0.');
        process.exit(3);
    }
    console.log('GOLDEN PATH: all twelve steps clean.');
    process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
