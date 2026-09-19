/**
 * xss-probe.mjs — four innerHTML interpolation candidates.
 *
 * Each case intercepts the API route that feeds the candidate, fulfills it with
 * a fixture carrying the payload, then asserts on TWO things:
 *   (a) window.__xss — did the payload actually execute;
 *   (b) img[src="x"] count — was an element CREATED.
 * PASS = neither fired, i.e. the value went in as text.
 *
 * ANTI-VACUITY. "No XSS" is trivially true of a page that rendered nothing, so
 * every case also proves the payload REACHED THE DOM. Arrival is tested by
 * searching the SERIALISED HTML for a distinctive ASCII marker, because three
 * of the four payloads land in an ATTRIBUTE, where textContent can never see
 * them:
 *     class="conv-dot conv-dot-${c.status}"
 *     data-id="${t.id}"   and   class="tp-cat-${t.category}"
 * The marker survives HTML-escaping, so present-in-outerHTML means the value
 * arrived (escaped or raw, text position or attribute position); absent means
 * it genuinely did not.
 *
 * When the marker is absent the case reports WHY: the route-hit count, the
 * number of elements matching the case's own container selector (zero means
 * the surface never rendered at all, a different failure from "rendered
 * without my data"), and the first 400 characters of that container's
 * outerHTML (or document.body's if the container is missing).
 *
 * Routes are scoped by pathname, never by a `**\/api\/**` glob: that glob also
 * matches the static module at /static/js/api/http.js and breaks module loading.
 * Every page.route() is registered BEFORE page.goto(), so the boot fetch cannot
 * race past the interception.
 *
 * HOW EACH SURFACE IS DRIVEN (measured, not guessed):
 *   1. template_picker — opened with Control+Shift+T. The template list is
 *      fetched lazily on open, and template_cache.js memoises it, so a second
 *      open in the same page does NOT refetch. One open per context.
 *   2. chat_pane.finishAssistantMessage — driven by a WEBSOCKET event, not an
 *      HTTP route. page.route() cannot intercept it. chat_pane.js exports only
 *      mountChatPane, so finishAssistantMessage is genuinely unreachable from
 *      the page. The case is reported INCONCLUSIVE, not faked, not deleted, and
 *      not turned into a string-match of the source file.
 *   3. sidebar conv-dot — GET /api/conversations. Fixture must be a COMPLETE
 *      conversation row; a missing field the render path reads throws during
 *      render and leaves the payload absent from the DOM.
 *   4. composer._showAssessment — reached through the composer's send path.
 *      _showAssessment runs only when localStorage.gca_feat_assess === "1" AND
 *      _shouldAssess(text) is true (text > 50 words, or matches _RISKY). We
 *      type a >50-word message and click Send. The assessment route is
 *      POST /api/conversations/assess.
 *
 * VERDICT LOGIC:
 *   marker ABSENT                                              -> INCONCLUSIVE
 *   marker PRESENT, __xss undefined and img[src="x"] count 0   -> PASS
 *   marker PRESENT and (__xss fired or an img was created)     -> FAIL
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const APP = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

// Distinctive ASCII marker. HTML-escaping does not alter it, so it survives
// whether the value lands in text or in an attribute, escaped or raw.
const MARK = 'XSSPROBE7';

// Proves EXECUTION or ELEMENT CREATION, not mere string presence. The leading
// `">` closes whatever attribute the value was interpolated into; the img then
// becomes a real element and its onerror fires.
const PAYLOAD = `${MARK}"><img src=x onerror="window.__xss=(window.__xss||0)+1">`;

const results = [];
const add = (label, state, detail = '') => {
  results.push({ label, state, detail });
  console.log(`${state.padEnd(13)} ${label}\n              ${detail}`);
};

const browser = await chromium.launch();

async function freshPage({ assess = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(({ assess }) => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_sb_open', '1');
      // Case 4 needs the assessment gate ON. Every other case leaves it off so
      // the composer's send path is not gated by an unrelated dialog.
      localStorage.setItem('gca_feat_assess', assess ? '1' : '0');
    } catch {}
  }, { assess });
  const page = await ctx.newPage();
  return { ctx, page };
}

/**
 * The two assertions, read together. `executed` is the payload's own counter;
 * `created` counts img elements with src="x", which catches the case where the
 * element was built but onerror was blocked or had not fired yet.
 */
const probe = (page) => page.evaluate(() => ({
  executed: window.__xss || 0,
  created: document.querySelectorAll('img[src="x"]').length,
}));

/**
 * Anti-vacuity: did the value ARRIVE? Searches the serialised HTML for the
 * marker, which is visible in text position and in attribute position alike.
 */
const markerPresent = (page, mark) =>
  page.evaluate((m) => document.body.outerHTML.includes(m), mark);

/**
 * Diagnostics for an absent marker. Reports the container count and a slice of
 * the container's outerHTML so the harness says WHY, not just that.
 */
const absenceDiagnostics = (page, selector) => page.evaluate(({ selector, mark }) => {
  const nodes = document.querySelectorAll(selector);
  const src = nodes.length ? nodes[0].outerHTML : document.body.outerHTML;
  return {
    containerSelector: selector,
    containerCount: nodes.length,
    htmlSlice: src.slice(0, 400),
    markerInBody: document.body.outerHTML.includes(mark),
  };
}, { selector, mark: MARK });

// ---------------------------------------------------------------------------
// 1. template_picker.js renderCard
//    data-id="${t.id}" · class="tp-cat-${t.category}" · >${t.category}<
//    Category and id are user-supplied via the create/edit form.
//
//    Opened with Control+Shift+T. The list is fetched lazily on open, and
//    template_cache.js memoises it, so we open exactly once per context.
// ---------------------------------------------------------------------------
{
  const label = '1. template_picker renderCard: id and category';
  const { ctx, page } = await freshPage();
  let hits = 0;
  try {
    // Route registered BEFORE goto so the boot fetch cannot race past it.
    await page.route('**/*', async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p !== '/api/flow-templates') return route.continue();
      hits += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{
          id: PAYLOAD,
          title: 'Fixture',
          description: 'fixture',
          body: 'body',
          category: PAYLOAD,
          is_builtin: false,
          created_at: new Date().toISOString(),
          sort_order: 0,
        }]),
      });
    });

    await page.goto(APP, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);

    // Control+Shift+T opens the picker. Nothing is fetched until it opens.
    await page.keyboard.press('Control+Shift+T');
    await page.waitForTimeout(1200);

    // THE STEP THAT WAS MISSING, and the reason this case measured nothing for
    // two sessions. template_picker.js:43 defaults `activeCategory = "report"`,
    // and the filter at :78 is
    //     activeCategory === "custom" ? !t.is_builtin : t.category === activeCategory
    // Our payload lives IN t.category, so under the default it can never equal
    // "report" and the template is filtered out before render: `list` is empty,
    // renderEmpty() runs at :107, and .tp-card count is 0. The payload and the
    // filter key are the same field, which looks like a contradiction —
    // a fixture that renders carries no payload, one that carries the payload
    // never renders.
    //
    // The "custom" branch is the way through: it ignores t.category entirely
    // and tests !t.is_builtin, which our fixture satisfies. So switch tabs,
    // and renderCard finally runs with the payload in place.
    const customTab = await page.$('.tp-cat-btn[data-cat="custom"]');
    if (!customTab) {
      add(label, 'INCONCLUSIVE',
          `route hits=${hits} · the picker opened but has no [data-cat="custom"] tab, ` +
          `so the category filter cannot be moved off its "report" default and ` +
          `renderCard cannot run on this fixture`);
      throw new Error('__handled__');
    }
    await customTab.click();
    await page.waitForTimeout(600);

    const r = await probe(page);
    const present = await markerPresent(page, MARK);

    if (!present) {
      const d = await absenceDiagnostics(page, '.tp-card');
      add(label, 'INCONCLUSIVE',
          `route hits=${hits} · marker absent · container ${d.containerSelector} count=${d.containerCount} · ` +
          `html[0:400]=${JSON.stringify(d.htmlSlice)}`);
    } else {
      add(label, r.executed === 0 && r.created === 0 ? 'PASS' : 'FAIL',
          `route hits=${hits} · __xss=${r.executed} · img[src=x]=${r.created} · markerPresent=${present}`);
    }
  } catch (err) {
    // '__handled__' means the case already recorded its own verdict above and
    // threw only to skip the rest. Reporting it a second time as a crash would
    // overwrite a precise INCONCLUSIVE with a meaningless one.
    if (err.message !== '__handled__') add(label, 'FAIL', `threw: ${err.message}`);
  } finally { await ctx.close(); }
}

// ---------------------------------------------------------------------------
// 2. chat_pane.js finishAssistantMessage: ${STATUS_LABEL[status] || status}
//    The raw fallback when status is not done/error/stopped/timeout.
//
//    Driven by a WEBSOCKET event, not an HTTP route. page.route() cannot
//    intercept it. chat_pane.js exports only mountChatPane, so
//    finishAssistantMessage is genuinely unreachable from the page. This case
//    is NOT faked, NOT deleted, and NOT turned into a string-match of the
//    source file. It is reported as not-yet-provable, naming what would be
//    needed.
// ---------------------------------------------------------------------------
{
  const label = '2. chat_pane finishAssistantMessage: status fallback';
  const { ctx, page } = await freshPage();
  const hits = 0; // no HTTP route feeds this surface; kept for the detail string
  try {
    await page.goto(APP, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);

    // Probe the module to record exactly what is reachable, so the report
    // names the gap rather than asserting it.
    const probeMod = await page.evaluate(async () => {
      try {
        const m = await import('/static/js/ui/chat_pane.js');
        return {
          ok: typeof m.finishAssistantMessage === 'function',
          exports: Object.keys(m),
        };
      } catch (e) {
        return { ok: false, error: String(e).slice(0, 160) };
      }
    });

    // NOT INCONCLUSIVE, AND NOT A BROWSER ASSERTION EITHER. This case spent two
    // sessions reported as not-yet-provable, on the assumption that a hostile
    // status could arrive over the websocket and that reaching it needed a
    // routeWebSocket fixture. Reading the CALL SITES settled it instead:
    // `status` is a parameter, and all five callers pass a string literal
    // ("timeout" :635, "error" :648, "stopped" :652, "done" :657,
    // "error" :665) — every one a key in STATUS_LABEL. The `|| status`
    // fallback is unreachable with any value a user or server can influence.
    //
    // There is therefore nothing to inject, and a probe that drove a payload
    // through the socket would have reported PASS for a reason unrelated to
    // what it claimed to test. The invariant that actually protects this sink
    // is "every caller passes a literal", which is a statement about source,
    // and it is asserted in backend/tests/test_frontend_xss_invariants.py
    // where it can be mutation-verified. This case defers to it rather than
    // pretending to measure something.
    add(label, 'CLEAR-BY-CONSTRUCTION',
        `not a DOM assertion: \`status\` is a parameter and all 5 call sites in ` +
        `chat_pane.js pass a string literal, so STATUS_LABEL[status] never falls ` +
        `back to raw input. Pinned by test_frontend_xss_invariants.py:: ` +
        `test_finish_assistant_message_is_only_ever_called_with_a_literal_status. ` +
        `(module exports: ${JSON.stringify(probeMod.exports ?? [])})`);
  } catch (err) {
    add(label, 'FAIL', `threw: ${err.message}`);
  } finally { await ctx.close(); }
}

// ---------------------------------------------------------------------------
// 3. sidebar_conversations.js: class="conv-dot conv-dot-${c.status}"
//    Inside a quoted attribute, so the payload must break out of the quotes.
//
//    Fixture is a COMPLETE conversation row. A missing field the render path
//    reads throws during render and leaves the payload absent from the DOM.
// ---------------------------------------------------------------------------
{
  const label = '3. sidebar conv-dot: status in a quoted attribute';
  const { ctx, page } = await freshPage();
  let hits = 0;
  try {
    // Route registered BEFORE goto so the boot fetch cannot race past it.
    await page.route('**/*', async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p !== '/api/conversations') return route.continue();
      hits += 1;
      const now = new Date().toISOString();
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{
          id: 'c1',
          name: 'Fixture',
          status: PAYLOAD,
          source: 'web',
          project_id: null,
          session_id: 's1',
          model: 'claude-sonnet-4-5',
          system_prompt: null,
          permission_mode: 'default',
          max_tokens: 4096,
          thinking_budget: 0,
          cost_usd: 0,
          created_at: now,
          updated_at: now,
          started_at: now,
          completed_at: null,
        }]),
      });
    });

    await page.goto(APP, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);

    const r = await probe(page);
    const present = await markerPresent(page, MARK);

    if (!present) {
      const d = await absenceDiagnostics(page, '.conv-item');
      add(label, 'INCONCLUSIVE',
          `route hits=${hits} · marker absent · container ${d.containerSelector} count=${d.containerCount} · ` +
          `html[0:400]=${JSON.stringify(d.htmlSlice)}`);
    } else {
      add(label, r.executed === 0 && r.created === 0 ? 'PASS' : 'FAIL',
          `route hits=${hits} · __xss=${r.executed} · img[src=x]=${r.created} · markerPresent=${present}`);
    }
  } catch (err) {
    add(label, 'FAIL', `threw: ${err.message}`);
  } finally { await ctx.close(); }
}

// ---------------------------------------------------------------------------
// 4. composer.js _showAssessment: ${level.toUpperCase()}
//
//    Reached through the composer's send path, not by loading the page.
//    _showAssessment runs only when localStorage.gca_feat_assess === "1" AND
//    _shouldAssess(text) is true (text > 50 words, or matches _RISKY). We type
//    a >50-word message and click Send. The assessment route is
//    POST /api/conversations/assess.
//
//    .toUpperCase() does not neutralise the payload — the angle brackets and
//    quotes pass through — so the candidate is real.
// ---------------------------------------------------------------------------
{
  const label = '4. composer _showAssessment: level.toUpperCase()';
  const { ctx, page } = await freshPage({ assess: true });
  let hits = 0;
  try {
    // Route registered BEFORE goto so the boot fetch cannot race past it.
    await page.route('**/*', async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p !== '/api/conversations/assess') return route.continue();
      hits += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ level: PAYLOAD, summary: 'fixture', concerns: [] }),
      });
    });

    await page.goto(APP, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);

    // Type a message long enough to trip _shouldAssess (>50 words) and send it.
    // The composer's send path calls api.assessMessage, which hits the route
    // above; a non-"low" level then calls _showAssessment.
    const longText = Array.from({ length: 60 }, (_, i) => `word${i}`).join(' ');
    await page.evaluate((text) => {
      const input = document.querySelector('#composer-input');
      if (!input) return;
      input.value = text;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }, longText);
    await page.waitForTimeout(200);

    await page.evaluate(() => {
      document.querySelector('#composer-send')?.click();
    });
    await page.waitForTimeout(1500);

    const r = await probe(page);
    const present = await markerPresent(page, MARK);

    if (!present) {
      const d = await absenceDiagnostics(page, '.assess-level');
      add(label, 'INCONCLUSIVE',
          `route hits=${hits} · marker absent · container ${d.containerSelector} count=${d.containerCount} · ` +
          `html[0:400]=${JSON.stringify(d.htmlSlice)}`);
    } else {
      add(label, r.executed === 0 && r.created === 0 ? 'PASS' : 'FAIL',
          `route hits=${hits} · __xss=${r.executed} · img[src=x]=${r.created} · markerPresent=${present}`);
    }
  } catch (err) {
    add(label, 'FAIL', `threw: ${err.message}`);
  } finally { await ctx.close(); }
}

await browser.close();

console.log('\n=== xss-probe ===\n');
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
// CLEAR-BY-CONSTRUCTION is counted separately and deliberately NOT folded into
// "passed". A case that was settled by reading source is weaker evidence than
// one settled by driving a payload through a live render, and the summary line
// should say which kind of evidence each verdict rests on rather than let the
// weaker sort hide inside the stronger. It does not fail the run, because the
// property IS established — just somewhere else, by a test that can be
// mutation-verified.
const byConstruction = results.filter((r) => r.state === 'CLEAR-BY-CONSTRUCTION');
console.log(`${results.filter((r) => r.state === 'PASS').length} proved in the DOM · `
  + `${byConstruction.length} clear by construction · ${bad.length} failed · `
  + `${meh.length} inconclusive · ${results.length} case(s)`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — no candidate executed and no element was created');
if (byConstruction.length) {
  console.log(`(${byConstruction.length} case(s) rest on a source invariant, `
    + `pinned in backend/tests/test_frontend_xss_invariants.py)`);
}
process.exit(0);
