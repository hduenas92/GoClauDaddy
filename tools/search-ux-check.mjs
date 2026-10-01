/**
 * search-ux-check.mjs — 4-P2. One box that finds everything.
 *
 * Two groups in one #conv-search input:
 *   Chats    — client-side name filter, instant, no network
 *   Messages — server-side content hits from GET /api/search
 *
 * ANTI-VACUITY, and it is the first thing this file does.
 *
 * The database has 8 conversations and ~92 messages, but NO assertion here may
 * assume a given word appears in them. That is precisely the trap 4-P1 walked
 * into: `messages.stopped` had zero rows set, so every assertion about stopped
 * messages passed while testing nothing.
 *
 * This harness therefore DISCOVERS a token instead of assuming one, and instead
 * of seeding one. It reads real message content through the API, picks a word
 * that appears in CONTENT but in no conversation NAME, and then proves the word
 * is actually findable via /api/search before any UI assertion runs. If no such
 * word exists, the cases that need one report INCONCLUSIVE and say why.
 *
 * Discovery beat seeding here for two reasons, both worth recording. There is
 * no POST /api/conversations/{id}/messages route -- the first draft assumed one
 * and would have failed at runtime -- so seeding would have meant writing
 * straight into the live SQLite file, and a harness that mutates the user's real
 * data to test a read path is a bad trade. Reading proves the same thing:
 * the set is non-empty because its contents were inspected, not hoped for.
 *
 * Case 4 is the one that matters most. Out-of-order responses are invisible in
 * manual testing and in any test that does not deliberately reorder them: type
 * `foo` then `foobar`, and `foo`'s slower response can land last and win. It is
 * asserted by route interception, never by timing luck.
 *
 * Exit: 0 PASS · 1 FAIL · 2 INCONCLUSIVE
 */
import { chromium } from 'playwright';

const APP = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const OTHER = 'quorbindle';   // nonsense, used only for the stale-response race
// Must match SEARCH_DEBOUNCE_MS in sidebar_conversations.js. Case 1 asserts the
// local filter completes inside this window, i.e. without any network wait.
const SEARCH_DEBOUNCE_MS = 250;

const results = [];
const add = (label, state, detail = '') => {
  results.push({ label, state, detail });
  console.log(`${state.padEnd(13)} ${label}\n              ${detail}`);
};

// ---------------------------------------------------------------------------
// Discover a token that is provably in message CONTENT and in no NAME.
// Read-only: nothing is created, so nothing needs cleaning up.
// ---------------------------------------------------------------------------
const STOPWORDS = new Set([
  'about','after','again','because','before','being','between','could','every',
  'first','from','should','their','there','these','thing','think','those','through',
  'under','where','which','while','would','conversation','message','assistant',
]);

async function discoverToken() {
  const listRes = await fetch(`${APP}/api/conversations`);
  if (!listRes.ok) return { error: `GET /api/conversations -> ${listRes.status}` };
  const convs = await listRes.json();
  if (!convs.length) return { error: 'no conversations exist' };

  const names = convs.map((c) => (c.name || '').toLowerCase());
  const inAnyName = (w) => names.some((n) => n.includes(w));

  // Newest first: most likely to hold substantial content.
  for (const c of convs.slice(0, 6)) {
    const one = await fetch(`${APP}/api/conversations/${c.id}`);
    if (!one.ok) continue;
    const body = await one.json();
    for (const m of body.messages ?? []) {
      const words = String(m.content || '').toLowerCase().match(/[a-z]{6,14}/g) ?? [];
      for (const w of words) {
        if (STOPWORDS.has(w) || inAnyName(w)) continue;
        // The non-empty proof: the index must actually return it. A word that
        // is in content but not in the FTS index would make every case below
        // fail for a reason that has nothing to do with the UI.
        const probe = await fetch(`${APP}/api/search?q=${encodeURIComponent(w)}`);
        if (!probe.ok) continue;
        const hits = (await probe.json()).results ?? [];
        if (hits.length > 0) return { token: w, hits: hits.length, conv: c.id };
      }
    }
  }
  return { error: 'no content word was both absent from every name and present in the index' };
}

const browser = await chromium.launch();

async function freshPage({ blockSearch = false, fixtureChats = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_sb_open', '1');
      localStorage.setItem('gca_feat_assess', '0');
    } catch {}
  });
  const page = await ctx.newPage();
  if (blockSearch || fixtureChats) {
    // Scoped to the search route by pathname. A `**/api/**` glob would also
    // match the static module at /static/js/api/http.js and break module
    // loading, which reads as "the filter is broken" — a trap already hit once
    // on this project.
    await page.route('**/*', async (route) => {
      const req = route.request();
      const p = new URL(req.url()).pathname;
      if (blockSearch && p === '/api/search') return route.abort();
      // 2026-10-01: case 1 filters the sidebar client-side and needs 2+ chats;
      // the live DB may hold one. Append two old fixture chats to the real list
      // (never written to the DB; older than every real chat, so boot still
      // opens a real one).
      if (fixtureChats && p === '/api/conversations' && req.method() === 'GET') {
        const real = await (await route.fetch()).json();
        const fx = (id, name) => ({ ...(real[0] ?? {}), id, name, session_id: null, status: 'idle',
          created_at: '2000-01-01T00:00:00+00:00', updated_at: '2000-01-01T00:00:00+00:00' });
        return route.fulfill({ json: [...real, fx('fixture-a', 'Zebra fixture chat'), fx('fixture-b', 'Quokka fixture notes')] });
      }
      return route.continue();
    });
  }
  await page.goto(APP, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  return { ctx, page };
}

const type = async (page, text) => {
  await page.fill('#conv-search', text);
  await page.waitForTimeout(900);   // debounce is 250ms; leave room for the round trip
};

const groups = (page) =>
  page.evaluate(() => ({
    chats: [...document.querySelectorAll('#conv-list .conv-item:not(.conv-hit) .conv-name')]
      .map((e) => e.textContent.trim()),
    header: document.querySelector('#conv-list .conv-group-header')?.textContent?.trim() ?? '',
    hits: [...document.querySelectorAll('#conv-list .conv-hit')].map((e) => e.textContent.trim()),
    note: document.querySelector('#conv-list .conv-group-note')?.textContent?.trim() ?? '',
    marks: [...document.querySelectorAll('#conv-list .conv-hit mark')].map((e) => e.textContent),
    toast: document.querySelector('.error-toast')?.innerText?.trim() ?? '',
  }));

const found = await discoverToken();
const TOKEN = found.token;
const haveToken = Boolean(TOKEN);
console.log(haveToken
  ? `discovered token ${JSON.stringify(TOKEN)} - ${found.hits} indexed hit(s), absent from every conversation name\n`
  : `NO USABLE TOKEN: ${found.error}\n`);

try {
  // --- 1. local filter works with the network blocked --------------------
  {
    const label = '1. the Chats filter works with /api/search blocked';
    const { ctx, page } = await freshPage({ blockSearch: true, fixtureChats: true });
    try {
      const before = (await groups(page)).chats.length;
      if (before < 2) {
        add(label, 'INCONCLUSIVE', `need 2+ conversations to filter; found ${before}`);
      } else {
        const name = (await groups(page)).chats[0];
        // NO WAIT. This is the whole assertion, and the first version got it
        // wrong: it used the 900ms helper, so with the synchronous render
        // deleted the list still narrowed — later, via the error path — and the
        // case passed. It proved "the filter eventually works", which is not
        // the property. The property is that filtering does NOT wait on the
        // network: it must be done before the 250ms debounce could even have
        // fired, let alone a round trip.
        const t0 = Date.now();
        await page.fill('#conv-search', name.slice(0, 6));
        const g = await groups(page);            // read immediately
        const elapsed = Date.now() - t0;
        const narrowed = g.chats.length > 0 && g.chats.length < before;
        add(label, narrowed && elapsed < SEARCH_DEBOUNCE_MS ? 'PASS' : 'FAIL',
            `${before} -> ${g.chats.length} chats in ${elapsed}ms ` +
            `(must be < ${SEARCH_DEBOUNCE_MS}ms debounce, search route aborted)`);
      }
    } finally { await ctx.close(); }
  }

  // --- 2. content-only hits appear ---------------------------------------
  {
    const label = '2. a word only in message CONTENT produces a Messages group';
    if (!haveToken) {
      add(label, 'INCONCLUSIVE',
          `${found.error}; refusing to assert against an unknown set`);
    } else {
      const { ctx, page } = await freshPage();
      try {
        await type(page, TOKEN);
        const g = await groups(page);
        const named = g.chats.some((n) => n.toLowerCase().includes(TOKEN));
        add(label, g.hits.length > 0 && !named ? 'PASS' : 'FAIL',
            `hits=${g.hits.length} header=${JSON.stringify(g.header)} ` +
            `(the token must NOT be in any conversation NAME: ${named})`);
      } finally { await ctx.close(); }
    }
  }

  // --- 3. escaping happens BEFORE decoration -----------------------------
  {
    const label = '3. the snippet highlights, and markup in it renders as TEXT';
    const { ctx, page } = await freshPage();
    try {
      await page.route('**/*', async (route) => {
        const p = new URL(route.request().url()).pathname;
        if (p !== '/api/search') return route.continue();
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            truncated: false,
            results: [{
              conversation_id: 'x', conversation_name: 'Fixture', message_id: 'm', role: 'user',
              // The server wraps hits in ** ; the <script> is hostile content.
              snippet: 'a **hit** and <script>alert(1)</script> after it',
              created_at: new Date().toISOString(),
            }],
          }),
        });
      });
      await type(page, 'anything');
      const g = await groups(page);
      const scriptEls = await page.evaluate(() => document.querySelectorAll('#conv-list script').length);
      const marked = g.marks.includes('hit');
      // Assert on textContent, not on the absence of a tag: the script must be
      // present AS TEXT. "No <script> element" alone would also pass if the
      // snippet had been silently dropped.
      const asText = g.hits.some((t) => t.includes('<script>alert(1)</script>'));
      add(label, marked && asText && scriptEls === 0 ? 'PASS' : 'FAIL',
          `marks=${JSON.stringify(g.marks)} scriptElements=${scriptEls} renderedAsText=${asText}`);
    } finally { await ctx.close(); }
  }

  // --- 4. out-of-order responses: the LAST query wins ---------------------
  {
    const label = '4. a slow earlier response cannot overwrite a newer one';
    const { ctx, page } = await freshPage();
    try {
      let n = 0;
      await page.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname !== '/api/search') return route.continue();
        n += 1;
        const q = url.searchParams.get('q') ?? '';
        // The FIRST search is held back deliberately so it lands LAST.
        if (n === 1) await new Promise((r) => setTimeout(r, 2500));
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            truncated: false,
            results: [{
              conversation_id: 'c', conversation_name: `RESULT FOR ${q}`, message_id: 'm',
              role: 'user', snippet: `snippet for ${q}`, created_at: new Date().toISOString(),
            }],
          }),
        });
      });

      await page.fill('#conv-search', TOKEN);
      await page.waitForTimeout(400);      // past the debounce: request 1 is away and stalled
      await page.fill('#conv-search', OTHER);
      await page.waitForTimeout(4000);     // request 2 returns, then request 1 finally lands

      const g = await groups(page);
      const shows = g.hits.join(' ');
      const stale = shows.includes(TOKEN);
      const fresh = shows.includes(OTHER);
      if (n < 2) {
        add(label, 'INCONCLUSIVE', `only ${n} search request(s) were made; the race never happened`);
      } else {
        add(label, fresh && !stale ? 'PASS' : 'FAIL',
            `${n} requests; displayed=${JSON.stringify(shows.slice(0, 80))} ` +
            `(stale "${TOKEN}" present: ${stale})`);
      }
    } finally { await ctx.close(); }
  }

  // --- 5. a 400 surfaces and leaves Chats alone ---------------------------
  {
    const label = '5. a failed search is reported and Chats keeps working';
    const { ctx, page } = await freshPage();
    try {
      // MEASURED, and it corrects this task's own plan: q="!!!" does NOT 400.
      // The server returns 200 with an empty result set — _build_fts_query
      // handles punctuation-only input rather than rejecting it. Asserting
      // against a 400 that never happens would have been a case that could
      // only ever fail for the wrong reason.
      //
      // So the failure is INJECTED. That is the better test anyway: this case
      // exists to prove the frontend's catch path reports errors, not to
      // characterise the backend's tokeniser, which is free to change.
      await page.route('**/*', async (route) => {
        const p = new URL(route.request().url()).pathname;
        if (p !== '/api/search') return route.continue();
        return route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'q has no searchable terms' }),
        });
      });

      // Watch for the toast rather than sampling once. The first version of
      // this case compared chat COUNTS before and after, which is the wrong
      // assertion twice over: "!!!" legitimately empties the Chats group,
      // because no conversation NAME contains it, so 10 -> 0 is correct
      // behaviour being reported as a failure. And a toast that appears and
      // auto-dismisses inside the wait reads as silence.
      await page.evaluate(() => {
        window.__toasts = [];
        new MutationObserver(() => {
          const t = document.querySelector('.error-toast');
          if (t) window.__toasts.push(t.innerText.trim());
        }).observe(document.body, { childList: true, subtree: true });
      });

      // A word that genuinely exists, so the ONLY reason for failure is the
      // injected 400 — not an empty result set being mistaken for an error.
      await type(page, TOKEN || 'search');

      const seen = await page.evaluate(() => window.__toasts || []);
      const g = await groups(page);
      // What must hold: the failure was REPORTED, and the Chats group is still
      // a working list rather than an error state — i.e. clearing the box
      // brings the conversations back.
      await type(page, '');
      const restored = (await groups(page)).chats.length;

      const reported = seen.length > 0 || g.toast.length > 0;
      add(label, reported && restored > 0 && g.hits.length === 0 ? 'PASS' : 'FAIL',
          `toasts=${seen.length} lastToast=${JSON.stringify((seen[0] || g.toast).slice(0, 60))} ` +
          `hits=${g.hits.length} chatsAfterClearing=${restored}`);
    } finally { await ctx.close(); }
  }

  // --- 6. truncated is surfaced ------------------------------------------
  {
    const label = '6. truncated results say so';
    const { ctx, page } = await freshPage();
    try {
      await page.route('**/*', async (route) => {
        const p = new URL(route.request().url()).pathname;
        if (p !== '/api/search') return route.continue();
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            truncated: true,
            results: [{
              conversation_id: 'c', conversation_name: 'Fixture', message_id: 'm',
              role: 'user', snippet: 'one of many', created_at: new Date().toISOString(),
            }],
          }),
        });
      });
      await type(page, 'anything');
      const g = await groups(page);
      add(label, /showing the first/i.test(g.note) ? 'PASS' : 'FAIL', `note=${JSON.stringify(g.note)}`);
    } finally { await ctx.close(); }
  }

  // --- 7. clearing removes the group entirely -----------------------------
  {
    const label = '7. clearing the box removes the Messages group';
    if (!haveToken) {
      add(label, 'INCONCLUSIVE', 'no discovered token; cannot produce hits to clear');
    } else {
      const { ctx, page } = await freshPage();
      try {
        const before = (await groups(page)).chats.length;
        await type(page, TOKEN);
        const withHits = await groups(page);
        if (withHits.hits.length === 0) {
          add(label, 'INCONCLUSIVE', 'no hits appeared, so clearing them proves nothing');
        } else {
          await type(page, '');
          const after = await groups(page);
          add(label, after.hits.length === 0 && after.header === '' && after.chats.length === before
                ? 'PASS' : 'FAIL',
              `hits ${withHits.hits.length}->${after.hits.length}, header=${JSON.stringify(after.header)}, ` +
              `chats ${before}->${after.chats.length}`);
        }
      } finally { await ctx.close(); }
    }
  }
} finally {
  await browser.close();
  // Nothing to clean up: discovery is read-only by design.
}

console.log('\n=== search-ux-check ===\n');
const bad = results.filter((r) => r.state === 'FAIL');
const meh = results.filter((r) => r.state === 'INCONCLUSIVE');
console.log(`${results.filter((r) => r.state === 'PASS').length} passed · ${bad.length} failed · ${meh.length} inconclusive · ${results.length} case(s)`);
if (bad.length) { console.log(`\nRESULT: FAIL — ${bad.length}`); process.exit(1); }
if (meh.length) { console.log(`\nRESULT: INCONCLUSIVE — ${meh.length}`); process.exit(2); }
console.log('\nRESULT: PASS — one box finds names and content, and the last query wins');
process.exit(0);
