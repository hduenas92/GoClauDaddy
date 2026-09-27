/**
 * hover-check.mjs — §11 "Pointer & hover", the BLIND-TEST category.
 *
 *   node tools/hover-check.mjs            run every case
 *   node tools/hover-check.mjs --headed
 *
 * §11 says a hover bug is being deliberately withheld, and says not to test one
 * guessed cause but to sweep the space. So this file does not chase a
 * hypothesis: it enumerates the hover-reactive surfaces, states the counts, and
 * measures each claim the CSS makes rather than reading it.
 *
 * THE CENSUS THIS WAS BUILT AGAINST (ripgrep, 2026-09-19, frontend/static):
 *   51  `:hover` selectors, all in base.css
 *    0  `@media (hover: hover)` guards          <- case 5
 *    5  `pointer-events:` declarations           <- case 2
 *   15  `z-index:` declarations                  <- case 6
 *    4  `[data-tooltip]` elements                <- case 1
 *    9  pointer listeners in JS (5 mousedown for outside-click, 4 in
 *       honeycomb.js for the canvas's mouse tracking)
 * Counting first is deliberate: it is how an enumeration that quietly returns a
 * subset gets caught. If these numbers move, this file is measuring the wrong
 * app and the counts must be re-taken before the results mean anything.
 *
 * WHAT IS *NOT* COVERED HERE, and must not be read as passing:
 * fast-traversal tearing, hover across a re-render, hover during streaming,
 * hit-area correctness, hover + focus simultaneously, tooltip behaviour under
 * theme switch, and `prefers-reduced-motion` on hover-triggered animation.
 * Those need either a real stream or a judgement about appearance. "Not tested"
 * and "passed" are different results.
 *
 * Read-only: it clicks existing conversations and hovers things. It creates
 * nothing, deletes nothing, and spends no CaaS budget.
 */

import { chromium } from 'playwright';

const BASE_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const HEADED = process.argv.slice(2).includes('--headed');

const VIEWPORTS = [
    { width: 1440, height: 900, name: '1440x900' },
    { width: 2560, height: 1440, name: '2560x1440' },
    { width: 1024, height: 768, name: '1024x768' },
];

const failures = [];
const notes = [];

function check(name, ok, detail) {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
    if (!ok) failures.push(name);
    return ok;
}
function note(text) { console.log(`      ${text}`); notes.push(text); }
function section(t) { console.log(`\n--- ${t}`); }

/** Get past the first-run tour, which sits over everything. */
async function dismissTour(page) {
    if (!(await page.$('.ob-overlay'))) return;
    for (let i = 0; i < 8; i++) {
        const next = await page.$('.ob-overlay [data-action="next"]');
        if (!next) break;
        await next.click();
    }
    await page.click('.ob-overlay [data-action="finish"]').catch(() => { });
    await page.waitForSelector('.ob-overlay', { state: 'detached', timeout: 5000 }).catch(() => { });
}

/**
 * Where a [data-tooltip]'s ::after actually lands.
 *
 * getBoundingClientRect() cannot see a pseudo-element, so the box is derived
 * from the host's rect plus the ::after's own COMPUTED offsets. An earlier
 * version hardcoded the rule it expected to find (bottom / left:50% /
 * translateX(-50%)) and would therefore have kept reporting the old geometry
 * after the rule was fixed — measuring a fiction, confidently. It now reads
 * whichever of top/bottom/left/right is set and parses the transform matrix,
 * so it follows the stylesheet instead of asserting one.
 *
 * Offsets resolve against the host's padding box (the host is
 * position: relative), so a host with a border introduces at most a
 * border-width of error. That is accepted: the failures this is looking for
 * are tens of pixels, not one.
 */
const TOOLTIP_PROBE = () => [...document.querySelectorAll('[data-tooltip]')].map((el) => {
    const host = el.getBoundingClientRect();
    const cs = getComputedStyle(el, '::after');
    const w = parseFloat(cs.width) || 0;
    const h = parseFloat(cs.height) || 0;
    const padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    const padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    const bw = (parseFloat(cs.borderLeftWidth) || 0) * 2;
    const boxW = cs.boxSizing === 'border-box' ? w : w + padX + bw;
    const boxH = cs.boxSizing === 'border-box' ? h : h + padY + bw;
    // translateX out of the computed transform matrix ("none" or "matrix(...)").
    let tx = 0;
    const m = /matrix\(([^)]+)\)/.exec(cs.transform);
    if (m) tx = parseFloat(m[1].split(',')[4]) || 0;

    const num = (v) => (v === 'auto' ? null : parseFloat(v));
    const cssTop = num(cs.top), cssBottom = num(cs.bottom);
    const cssLeft = num(cs.left), cssRight = num(cs.right);

    let top;
    if (cssTop !== null) top = host.top + cssTop;
    else if (cssBottom !== null) top = host.bottom - cssBottom - boxH;
    else top = host.top;

    let left;
    if (cssLeft !== null) left = host.left + cssLeft + tx;
    else if (cssRight !== null) left = host.right - cssRight - boxW + tx;
    else left = host.left + tx;
    let clip = null;
    for (let p = el.parentElement; p; p = p.parentElement) {
        const ps = getComputedStyle(p);
        if (/hidden|clip|scroll/.test(ps.overflow + ps.overflowX + ps.overflowY)) {
            const r = p.getBoundingClientRect();
            clip = {
                sel: p.id ? '#' + p.id : (p.className || p.tagName.toLowerCase()),
                top: r.top, left: r.left, right: r.right, bottom: r.bottom,
            };
            break;
        }
    }
    return {
        text: el.getAttribute('data-tooltip'),
        id: el.id || el.className,
        host: { top: host.top, left: host.left, right: host.right },
        tip: { top, left, right: left + boxW, bottom: top + boxH, w: boxW, h: boxH },
        clip, vw: innerWidth, vh: innerHeight,
    };
});

async function run() {
    const browser = await chromium.launch({ headless: !HEADED });

    // ---------------------------------------------------------------- case 1
    section('1. tooltip placement at the viewport edges');
    let tooltipCount = 0;
    for (const vp of VIEWPORTS) {
        const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
        const page = await ctx.newPage();
        await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#composer-send', { timeout: 30_000 });
        await dismissTour(page);
        await page.waitForTimeout(200);

        const rows = await page.evaluate(TOOLTIP_PROBE);
        tooltipCount = rows.length;
        // Non-empty guard FIRST. "No tooltip is offscreen" is satisfied
        // perfectly by having no tooltips at all.
        if (!check(`1 [${vp.name}] the page has tooltips to measure`, rows.length > 0, `${rows.length} found`)) {
            await ctx.close();
            continue;
        }
        const bad = rows.filter((r) =>
            r.tip.top < 0 || r.tip.left < 0 || r.tip.right > r.vw || r.tip.bottom > r.vh ||
            (r.clip && (r.tip.top < r.clip.top || r.tip.left < r.clip.left || r.tip.right > r.clip.right)));
        check(`1 [${vp.name}] every tooltip lands inside the viewport and its clipping ancestor`,
            bad.length === 0, `${rows.length - bad.length}/${rows.length} placed correctly`);
        for (const r of bad) {
            const why = [
                r.tip.top < 0 && `${Math.abs(r.tip.top).toFixed(0)}px ABOVE the viewport top`,
                r.tip.left < 0 && `${Math.abs(r.tip.left).toFixed(0)}px past the left edge`,
                r.tip.right > r.vw && `${(r.tip.right - r.vw).toFixed(0)}px past the right edge`,
                r.clip && r.tip.top < r.clip.top && `clipped by ${r.clip.sel}`,
            ].filter(Boolean).join(', ');
            note(`${JSON.stringify(r.text)} (${r.id}) — tip top=${r.tip.top.toFixed(0)} left=${r.tip.left.toFixed(0)} right=${r.tip.right.toFixed(0)} size=${r.tip.w.toFixed(0)}x${r.tip.h.toFixed(0)} · ${why}`);
        }
        await ctx.close();
    }

    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();

    // Instrument listener registration BEFORE the app boots, or case 4 counts
    // only the listeners added after it was already too late.
    await page.addInitScript(() => {
        window.__lsn = { window: 0, document: 0, byType: {} };
        for (const target of [window, document]) {
            const orig = target.addEventListener;
            const key = target === window ? 'window' : 'document';
            target.addEventListener = function (type, ...rest) {
                window.__lsn[key]++;
                window.__lsn.byType[type] = (window.__lsn.byType[type] || 0) + 1;
                return orig.call(this, type, ...rest);
            };
        }
    });
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#composer-send', { timeout: 30_000 });
    await dismissTour(page);

    // ---------------------------------------------------------------- case 2
    section('2. the full-viewport layers that claim pointer-events: none');
    const layers = await page.$$eval('#honeycombCanvas, .ambient-bg, .scanline-overlay', (els) =>
        els.map((e) => ({
            sel: e.id ? '#' + e.id : '.' + e.className.split(' ')[0],
            pe: getComputedStyle(e).pointerEvents,
            z: getComputedStyle(e).zIndex,
        })));
    check('2 all three decorative layers are present', layers.length === 3,
        layers.map((l) => `${l.sel}(z${l.z},${l.pe})`).join(' '));
    check('2 each declares pointer-events: none', layers.every((l) => l.pe === 'none'),
        layers.map((l) => `${l.sel}=${l.pe}`).join(' '));

    // The declaration is a claim. Test it: at a grid of positions, whatever is
    // on top must never be one of those three.
    const hits = await page.evaluate(() => {
        const names = new Set(['honeycombCanvas']);
        const out = [];
        for (let fx = 0.05; fx <= 0.95; fx += 0.1) {
            for (let fy = 0.05; fy <= 0.95; fy += 0.1) {
                const x = Math.round(innerWidth * fx), y = Math.round(innerHeight * fy);
                const el = document.elementFromPoint(x, y);
                if (!el) { out.push({ x, y, hit: '(null)' }); continue; }
                const cls = typeof el.className === 'string' ? el.className : '';
                if (names.has(el.id) || /ambient-bg|scanline-overlay/.test(cls)) {
                    out.push({ x, y, hit: el.id || cls });
                }
            }
        }
        return out;
    });
    check('2 no decorative layer is ever the topmost element under the pointer',
        hits.length === 0,
        hits.length ? `swallowed at ${hits.slice(0, 5).map((h) => `(${h.x},${h.y})->${h.hit}`).join(' ')}` : '100 sample points, none swallowed');

    // ---------------------------------------------------------------- case 3
    section('3. enter/leave symmetry');
    // `read` is the element whose style to sample, when the rule lives on an
    // ANCESTOR of the thing the pointer is over. `.conv-item:hover` paints the
    // row; the pointer is over `.conv-body` inside it. Sampling the child
    // reported "hover changes nothing" — true of that element, and a false
    // accusation against the app. Hover what a user hovers, read where the rule
    // actually applies.
    const SURFACES = [
        { hover: '#export-btn' },
        { hover: '#settings-btn' },
        { hover: '#new-conv-btn' },
        // :not(.active) is load-bearing. `.project-item.active` / `.conv-item.active`
        // set the same background and border-color as `:hover`, at equal
        // specificity and LATER in base.css, so the active row wins and shows no
        // hover response at all. Hovering the first row (which is the active one
        // on a fresh load) therefore measures that collision, not hover. The
        // collision itself is asserted separately below.
        { hover: '.conv-item:not(.active) .conv-body', read: '.conv-item:not(.active)' },
        { hover: '.project-item:not(.active) .project-name', read: '.project-item:not(.active)' },
    ];
    for (const { hover: sel, read: readSel } of SURFACES) {
        const el = await page.$(sel);
        if (!el) { check(`3 ${sel} exists to hover`, false, 'not in the DOM'); continue; }
        const target = readSel ? await page.$(readSel) : el;
        const label = readSel ? `${sel} (read on ${readSel})` : sel;
        const read = () => target.evaluate((e) => {
            const cs = getComputedStyle(e);
            return [cs.backgroundColor, cs.color, cs.opacity, cs.borderColor, cs.transform].join('|');
        });
        const before = await read();
        await el.hover();
        await page.waitForTimeout(250);
        const during = await read();
        // Move the pointer far away rather than to another candidate, so a
        // "stuck" state cannot be masked by the next element's own hover.
        await page.mouse.move(5, 880);
        // Poll until the computed style returns to its resting value. A fixed
        // wait flaked: the transitions run 0.15-0.25s, but a busy main thread
        // can delay the leave event so a single 400ms sample still catches the
        // tail of the interpolation (measured residues: border rgba(1, 164,
        // 165, 0.3) vs rest rgba(0, 163, 165, 0.3) on #settings-btn, and an
        // alpha of 0.004 on a .conv-item). Polling asserts the END state, not
        // how fast the main thread happened to be at one instant.
        let after = null;
        const restDeadline = Date.now() + 3000;
        while (Date.now() < restDeadline) {
            await page.waitForTimeout(150);
            after = await read();
            if (after === before) break;
        }
        check(`3 ${label} — hover changes something`, before !== during,
            before === during ? 'no computed change on hover' : 'changed');
        check(`3 ${label} — hover fully clears on leave`, after === before,
            after === before ? 'returned to the resting style' : `stuck: rest=${before} left=${after}`);
    }

    // The selected row: does it respond to the pointer at all? Asserted on its
    // own because a user's pointer spends most of its time on the list they are
    // already in, and "the row I'm on is the one that stops reacting" is the
    // kind of thing that reads as intentional.
    {
        const active = await page.$('.conv-item.active');
        if (!active) {
            check('3 there is an active conversation row to test', false, 'none rendered');
        } else {
            const read = () => active.evaluate((e) => {
                const cs = getComputedStyle(e);
                return [cs.backgroundColor, cs.borderColor, cs.color].join('|');
            });
            const rest = await read();
            await active.hover();
            await page.waitForTimeout(250);
            const hot = await read();
            await page.mouse.move(5, 880);
            check('3 the ACTIVE row still responds to hover', rest !== hot,
                rest === hot
                    ? `no change — .active and :hover set the same properties at equal specificity, and .active is declared later, so it wins (${rest})`
                    : 'changed');
        }
    }

    // ---------------------------------------------------------------- case 4
    section('4. listener accounting across 20 conversation switches');
    const convs = await page.$$('.conv-item .conv-body');
    if (convs.length < 2) {
        check('4 there are at least 2 conversations to switch between', false,
            `${convs.length} found — this case needs existing conversations and creates none`);
    } else {
        const base = await page.evaluate(() => ({ ...window.__lsn, byType: { ...window.__lsn.byType } }));
        for (let i = 0; i < 20; i++) {
            // The list re-renders on every conversation switch, so element
            // handles grabbed before a click are DETACHED by the next one.
            // Locators re-resolve per action and retry instead of throwing
            // "Element is not attached to the DOM" (the pre-fix failure).
            const n = await page.locator('.conv-item .conv-body').count();
            const nth = i % Math.min(n, 4);
            await page.locator('.conv-item .conv-body').nth(nth).click({ timeout: 5000 }).catch(async () => {
                await page.waitForTimeout(300);
                await page.locator('.conv-item .conv-body').nth(nth).click();
            });
            await page.waitForTimeout(120);
        }
        const after = await page.evaluate(() => ({ ...window.__lsn, byType: { ...window.__lsn.byType } }));
        const grew = (after.window - base.window) + (after.document - base.document);
        const move = (after.byType.mousemove || 0) - (base.byType.mousemove || 0);
        check('4 no window/document listener growth over 20 switches', grew === 0,
            `window ${base.window}->${after.window}, document ${base.document}->${after.document} (+${grew})`);
        check('4 no mousemove listener growth', move === 0,
            `mousemove registrations +${move}`);
        if (grew !== 0) {
            const deltas = Object.entries(after.byType)
                .map(([t, n]) => [t, n - (base.byType[t] || 0)])
                .filter(([, d]) => d !== 0);
            note(`growth by type: ${JSON.stringify(Object.fromEntries(deltas))}`);
        }
    }

    // ---------------------------------------------------------------- case 5
    section('5. hover is never the sole access path');
    const guards = await page.evaluate(async () => {
        const res = await fetch('/static/css/base.css');
        const css = await res.text();
        return {
            hoverRules: (css.match(/:hover/g) || []).length,
            hoverMedia: (css.match(/@media[^{]*\(\s*hover/g) || []).length,
        };
    });
    note(`base.css: ${guards.hoverRules} :hover selectors, ${guards.hoverMedia} @media (hover:...) guards`);
    check('5 hover-only affordances are guarded by @media (hover: hover)',
        guards.hoverMedia > 0,
        guards.hoverMedia === 0
            ? `0 guards across ${guards.hoverRules} :hover rules — on a touch device these either never apply or stick after a tap`
            : `${guards.hoverMedia} guard(s)`);
    // The tooltips carry a title= as well, so their text is not hover-only.
    const titled = await page.$$eval('[data-tooltip]', (els) =>
        els.filter((e) => (e.getAttribute('title') || '').trim().length > 0).length);
    check('5 every [data-tooltip] also carries a title attribute',
        tooltipCount > 0 && titled === tooltipCount, `${titled}/${tooltipCount}`);

    // ---------------------------------------------------------------- case 6
    section('6. stacking order of the always-on layers');
    const zs = await page.evaluate(() => {
        const get = (sel) => {
            const e = document.querySelector(sel);
            return e ? getComputedStyle(e).zIndex : null;
        };
        return { scanline: get('.scanline-overlay'), app: get('#app'), tooltipZ: 200 };
    });
    // .ob-overlay only exists while the tour is up; its value is read from the
    // stylesheet instead so this case does not depend on tour state.
    const obZ = await page.evaluate(async () => {
        const css = await (await fetch('/static/css/base.css')).text();
        const m = css.match(/\.ob-overlay\s*\{[^}]*z-index:\s*(\d+)/);
        return m ? Number(m[1]) : null;
    });
    check('6 the onboarding overlay outranks the always-on scanline layer',
        obZ !== null && Number(zs.scanline) < obZ,
        `.scanline-overlay z=${zs.scanline}, .ob-overlay z=${obZ}${Number(zs.scanline) === obZ ? ' — TIED, so paint order decides which wins' : ''}`);

    await ctx.close();
    await browser.close();

    console.log('\n' + '='.repeat(64));
    console.log('NOT COVERED (do not read as passing): fast-traversal tearing · hover across a');
    console.log('re-render · hover during streaming · hit-area correctness · hover+focus together ·');
    console.log('hover during theme switch · reduced-motion on hover animation.');
    if (failures.length) {
        console.log(`\n§11 HOVER: ${failures.length} check(s) failed:`);
        failures.forEach((f) => console.log(`  - ${f}`));
        process.exit(1);
    }
    console.log('\n§11 HOVER: every covered check passed.');
    process.exit(0);
}

run().catch((e) => { console.error(e); process.exit(1); });
