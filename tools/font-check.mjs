/**
 * font-check.mjs — is the typeface actually rendering, or silently falling back?
 *
 * A font-family declaration that resolves correctly in getComputedStyle tells you
 * nothing about whether the glyphs on screen came from that font. If the webfont
 * never loaded, the browser silently uses the next entry in the stack and the
 * computed value looks identical. This measures the rendering, not the CSS.
 */
import { chromium } from 'playwright';

// Navigation uses domcontentloaded, never networkidle. networkidle is
// documented by Playwright as discouraged and inherently racy, and it hung in a
// clean Linux container against the /api/server/logs SSE stream. It was also
// redundant here: every navigation below is followed by an explicit wait, and
// that is what actually establishes readiness.

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

const requests = [];
page.on('response', (r) => {
  const u = r.url();
  if (/fonts\.(googleapis|gstatic)\.com/.test(u) || /\.(woff2?|ttf|otf)(\?|$)/i.test(u)) {
    requests.push({ status: r.status(), url: u.length > 110 ? u.slice(0, 110) + '…' : u });
  }
});
page.on('requestfailed', (r) => {
  const u = r.url();
  if (/fonts\.(googleapis|gstatic)\.com/.test(u) || /\.(woff2?|ttf|otf)(\?|$)/i.test(u)) {
    requests.push({ status: 'FAILED', url: u.slice(0, 110), err: r.failure()?.errorText });
  }
});

await page.addInitScript(() => { try { localStorage.setItem('gca_onboarded', '1'); } catch {} });
await page.goto(URL, { waitUntil: 'domcontentloaded' });
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(600);

const r = await page.evaluate(() => {
  // Width-comparison test: render the same string in the candidate family and in
  // a family that certainly does not exist. If the widths match, the candidate
  // never loaded and both fell back to the same default.
  function widthIn(family, weight = 400) {
    const s = document.createElement('span');
    s.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font-size:48px;font-weight:${weight};font-family:${family}`;
    s.textContent = 'Handgloves 0123 Illegal1';
    document.body.appendChild(s);
    const w = s.getBoundingClientRect().width;
    s.remove();
    return Math.round(w * 100) / 100;
  }

  const bogus = widthIn('"__NoSuchFace__"');

  // Derive the family list from the page's own Google Fonts URL rather than
  // hardcoding it. A hardcoded list cannot detect the failure that matters:
  // a family requested over the network that never renders. Fira Code sat in
  // that state and the first version of this check merely printed it as a note.
  const gf = [...document.querySelectorAll('link[rel="stylesheet"]')]
    .map((l) => l.href)
    .find((h) => h.includes('fonts.googleapis.com/css2'));
  const requested = {};   // family -> Set of requested numeric weights
  if (gf) {
    for (const m of gf.matchAll(/family=([^&]+)/g)) {
      const [rawFam, rawSpec = ''] = decodeURIComponent(m[1]).split(':');
      const fam = rawFam.replace(/\+/g, ' ');
      // "wght@400;600;700" or "ital,wght@0,400;1,400" — take the last number
      // of each tuple, which is the weight in both axis orderings.
      const weights = [...(rawSpec.split('@')[1] ?? '').matchAll(/(\d+)(?=;|$)/g)]
        .map((w) => Number(w[1]));
      requested[fam] = weights.length ? [...new Set(weights)] : [400];
    }
  }

  const probes = {};
  for (const [fam, weights] of Object.entries(requested)) {
    probes[fam] = {
      loadedApi: document.fonts.check(`16px "${fam}"`),
      width: widthIn(`"${fam}", "__NoSuchFace__"`),
      distinctFromFallback: widthIn(`"${fam}", "__NoSuchFace__"`) !== bogus,
      // A weight that is used but not loaded is faux-bolded silently. Checking
      // each requested weight is what surfaces the inverse case too: a weight
      // requested and never used shows up as loaded-but-unreferenced.
      weights: Object.fromEntries(
        weights.map((w) => [w, document.fonts.check(`${w} 16px "${fam}"`)]),
      ),
    };
  }

  // Every distinct font-weight any rendered element actually asks for, so a
  // used-but-unrequested weight cannot hide the way DM Sans 700 did.
  const usedWeights = {};
  for (const el of document.querySelectorAll('*')) {
    // A form control's text lives in `value`/`placeholder`, not textContent, so
    // a bare textContent guard silently drops it. That is exactly what happened
    // on the first run: #composer-input renders JetBrains Mono 600, yet 600 was
    // reported "requested but unused" because an empty textarea has no
    // textContent. Any element that CAN show text has to count.
    const isField = /^(INPUT|TEXTAREA|SELECT|OPTION)$/.test(el.tagName);
    if (!isField && !el.textContent?.trim()) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const fam = cs.fontFamily.split(',')[0].replace(/['"]/g, '').trim();
    (usedWeights[fam] ??= new Set()).add(Number(cs.fontWeight));
  }

  // what the real UI elements resolve to, and what they actually render as
  const sample = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { family: cs.fontFamily, size: cs.fontSize, weight: cs.fontWeight };
  };

  const loaded = [...document.fonts].map((f) => `${f.family} ${f.weight} ${f.status}`);

  return {
    fontsStatus: document.fonts.status,
    fontFaceEntries: loaded,
    bogusWidth: bogus,
    requested,
    probes,
    usedWeights: Object.fromEntries(
      Object.entries(usedWeights).map(([k, v]) => [k, [...v].sort((a, b) => a - b)]),
    ),
    ui: {
      body: sample('body'),
      newProjectBtn: sample('#new-project-btn'),
      convName: sample('.conv-name'),
      metricLabel: sample('.metric-label'),
      composer: sample('#composer-input'),
    },
    linkTags: [...document.querySelectorAll('link[rel="stylesheet"]')].map((l) => l.href.slice(0, 120)),
  };
});

await browser.close();

const line = (s = '') => console.log(s);
line(`=== font-check  ${URL} ===`);
line();
line(`document.fonts.status : ${r.fontsStatus}`);
line(`FontFace entries      : ${r.fontFaceEntries.length ? r.fontFaceEntries.join(', ') : '(none registered)'}`);
line();
line('Network (fonts):');
if (!requests.length) line('  (no font requests observed at all)');
for (const q of requests) line(`  ${String(q.status).padEnd(7)} ${q.url}${q.err ? '  ' + q.err : ''}`);
line();
const problems = [];

line(`Fallback baseline width: ${r.bogusWidth}px  (a family that does not exist)`);
line('Per-family (families read from the page\'s own Google Fonts URL):');
for (const [fam, p] of Object.entries(r.probes)) {
  const verdict = p.distinctFromFallback ? 'RENDERING' : 'NOT RENDERING (falling back)';
  line(`  ${fam.padEnd(16)} fonts.check=${String(p.loadedApi).padEnd(5)} width=${String(p.width).padEnd(8)} ${verdict}`);
  if (!p.distinctFromFallback) {
    problems.push(`${fam} is requested over the network but never renders — remove it from the request, or from the font stack ahead of whatever shadows it.`);
  }
  const missing = Object.entries(p.weights).filter(([, ok]) => !ok).map(([w]) => w);
  line(`  ${''.padEnd(16)} weights requested: ${Object.keys(p.weights).join(', ')}` +
       (missing.length ? `   NOT LOADED: ${missing.join(', ')}` : ''));

  // The inverse, and the one that bites: a weight rendered but never requested
  // is synthesized by the browser (faux bold / faux oblique) with no warning.
  const used = r.usedWeights[fam] ?? [];
  const unrequested = used.filter((w) => !(w in p.weights));
  if (unrequested.length) {
    problems.push(`${fam} renders at weight ${unrequested.join(', ')} but that weight is not requested — the browser is faux-bolding it. Add it to the Google Fonts URL.`);
  }
  const unused = Object.keys(p.weights).map(Number).filter((w) => !used.includes(w));
  if (unused.length) {
    // Scope limit, stated so it is never mistaken for proof: this only sees the
    // page as currently rendered. Weights that appear solely inside message
    // content (hljs bold tokens, markdown <strong>) are invisible on an empty
    // conversation. "unused" here means "not on screen", NOT "safe to remove".
    line(`  ${''.padEnd(16)} not on screen right now: ${unused.join(', ')}  (may still be used in message content — not proof it is dead)`);
  }
  line(`  ${''.padEnd(16)} weights actually rendered: ${used.join(', ') || '(family not used on this page)'}`);
}
line();
line('UI elements:');
for (const [k, v] of Object.entries(r.ui)) {
  line(v ? `  ${k.padEnd(15)} ${v.size} ${v.weight}  ${v.family}` : `  ${k.padEnd(15)} (not found)`);
}
line();
line('Stylesheet links:');
for (const l of r.linkTags) line(`  ${l}`);

line();
if (r.fontsStatus !== 'loaded') {
  problems.push(`document.fonts.status is "${r.fontsStatus}" after awaiting fonts.ready — a requested face never resolves.`);
}
if (problems.length) {
  line(`RESULT: FAIL — ${problems.length} font problem(s):`);
  for (const p of problems) line(`  - ${p}`);
  process.exit(1);
}
line('RESULT: PASS — every requested family renders, and every rendered weight is requested.');
process.exit(0);
