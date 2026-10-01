/**
 * tools/hover-check.mjs — QA §11 hover symmetry of the custom tooltips.
 *
 * Read-only Playwright/Chromium harness for the four header tooltip hosts:
 *   #export-btn, #shortcuts-btn, #settings-btn, #sb-toggle-btn
 *
 * The CSS tooltip is [data-tooltip]::after; ui/tooltips.js keeps the
 * .tooltip-open class while the pointer is on the host (or the tooltip box).
 *
 * Per theme, per host:
 *   1. enter                -> visible
 *   2. leave to empty area  -> hidden
 *   3. H -> next host       -> only next visible
 *   4. 20 enter/leave cycles -> no residue, listeners stable
 * Plus one theme-wide #settings-btn Esc/re-enter case.
 *
 * 4 hosts x 4 cases + 1 = 17 cases per theme, 34 total.
 * Exit 0 = every case passes, 1 = any case fails.
 */
import { chromium } from 'playwright';

const URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';
const THEMES = ['cyberpunk-console', 'lit-workbench'];
const HOSTS = ['#export-btn', '#shortcuts-btn', '#settings-btn', '#sb-toggle-btn'];
const VIEWPORT = { width: 1440, height: 900 };

let total = 0;
let failed = 0;

function report(theme, name, ok, detail) {
  total += 1;
  if (!ok) failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  [${theme}] ${name}  -> ${detail}`);
}

async function reportCase(theme, name, fn) {
  try {
    const result = await fn();
    report(theme, name, result.ok, result.detail);
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    report(theme, name, false, `error=${message}`);
  }
}

function stateText(state) {
  const opacity = Number.isFinite(state.opacity) ? state.opacity.toFixed(2) : 'missing';
  return `opacity=${opacity} tooltip-open=${state.open === true}`;
}

function isVisible(state) {
  return state.opacity > 0.5;
}

function isHidden(state) {
  return state.opacity < 0.05 && !state.open;
}

/**
 * Wait for the opacity transition on one host to finish. We force a style
 * flush first so a pending class change has instantiated its transition, then
 * await every animation on the host subtree, bounded at 2000 ms.
 */
async function settleHost(page, selector) {
  await page.evaluate(async (sel) => {
    const host = document.querySelector(sel);
    if (!host) return;
    // Force style flush: this starts a transition that has not been
    // instantiated yet. The opacity read is deliberately discarded.
    void getComputedStyle(host, '::after').opacity;
    const animations = host.getAnimations({ subtree: true });
    let timer;
    await Promise.race([
      Promise.all(animations.map((animation) => animation.finished.catch(() => {}))),
      new Promise((resolve) => {
        timer = setTimeout(resolve, 2000);
      }),
    ]);
    clearTimeout(timer);
  }, selector);
}

async function readHost(page, selector) {
  await settleHost(page, selector);
  return page.evaluate((sel) => {
    const host = document.querySelector(sel);
    if (!host) return { opacity: Number.NaN, open: false, missing: true };
    const cs = getComputedStyle(host, '::after');
    return {
      opacity: Number.parseFloat(cs.opacity),
      open: host.classList.contains('tooltip-open'),
    };
  }, selector);
}

async function readAllHosts(page) {
  const states = {};
  for (const selector of HOSTS) {
    states[selector] = await readHost(page, selector);
  }
  return states;
}

async function nextFrame(page) {
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => resolve());
  }));
}

async function moveTo(page, point) {
  await page.mouse.move(point.x, point.y, { steps: 1 });
  await nextFrame(page);
}

async function hostCentres(page) {
  return page.evaluate((hosts) => Object.fromEntries(hosts.map((selector) => {
    const host = document.querySelector(selector);
    const rect = host.getBoundingClientRect();
    return [selector, {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      width: rect.width,
      height: rect.height,
    }];
  })), HOSTS);
}

/**
 * A point inside #chat-scroll, far from every toolbar button and, as a
 * secondary preference, far from other interactive controls. In the 1440x900
 * layout this is normally the middle of the chat pane.
 */
async function findEmptyPoint(page) {
  return page.evaluate(() => {
    const pane = document.getElementById('chat-scroll');
    if (!pane) throw new Error('#chat-scroll not found');
    const paneRect = pane.getBoundingClientRect();
    if (!(paneRect.width > 0 && paneRect.height > 0)) {
      throw new Error('#chat-scroll has no layout box');
    }

    const toolbarRects = [...document.querySelectorAll('[data-tooltip]')]
      .map((el) => el.getBoundingClientRect());
    const controlRects = [...document.querySelectorAll(
      'button, [role="button"], a[href], summary, input, textarea, select',
    )].map((el) => el.getBoundingClientRect());

    const distanceToRect = (x, y, rect) => {
      const dx = Math.max(rect.left - x, 0, x - rect.right);
      const dy = Math.max(rect.top - y, 0, y - rect.bottom);
      return Math.hypot(dx, dy);
    };
    const minDistance = (x, y, rects) => (rects.length
      ? Math.min(...rects.map((rect) => distanceToRect(x, y, rect)))
      : Infinity);

    let best = null;
    for (let ix = 1; ix <= 20; ix += 1) {
      for (let iy = 1; iy <= 20; iy += 1) {
        const x = paneRect.left + (paneRect.width * ix) / 21;
        const y = paneRect.top + (paneRect.height * iy) / 21;
        if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) continue;
        const toolbarDistance = minDistance(x, y, toolbarRects);
        const controlDistance = minDistance(x, y, controlRects);
        const centreDistance = Math.hypot(
          x - (paneRect.left + paneRect.width / 2),
          y - (paneRect.top + paneRect.height / 2),
        );
        const score = toolbarDistance * 2 + Math.min(controlDistance, 500) - centreDistance * 0.01;
        if (!best || score > best.score) {
          best = { x, y, toolbarDistance, controlDistance, score };
        }
      }
    }
    if (!best) throw new Error('no candidate point inside #chat-scroll');
    if (best.toolbarDistance < 100) {
      throw new Error(`could not find a chat-pane point 100px from the toolbar (best ${best.toolbarDistance.toFixed(1)}px)`);
    }
    return best;
  });
}

/**
 * Count direct event listeners on a DOM node via CDP. objectId must come from
 * Runtime.evaluate; DOMDebugger.getEventListeners returns the listener list.
 */
async function listenerInfo(cdp, selector) {
  try {
    const { result } = await cdp.send('Runtime.evaluate', {
      expression: `document.querySelector(${JSON.stringify(selector)})`,
      objectGroup: 'hover-check-listeners',
      returnByValue: false,
    });
    if (!result || !result.objectId) return { count: -1, types: [] };
    const { listeners } = await cdp.send('DOMDebugger.getEventListeners', {
      objectId: result.objectId,
      depth: 1,
    });
    await cdp.send('Runtime.releaseObject', { objectId: result.objectId }).catch(() => {});
    const list = Array.isArray(listeners) ? listeners : [];
    return { count: list.length, types: list.map((listener) => listener.type) };
  } catch (err) {
    return { count: -1, types: [], error: err && err.message ? err.message : String(err) };
  }
}

/** Wait until ui/tooltips.js has attached its pointerenter listener. */
async function waitForTooltipsMounted(cdp) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    const info = await listenerInfo(cdp, '#export-btn');
    if (info.types.includes('pointerenter')) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('ui/tooltips.js did not attach pointerenter listeners within 12s');
}

const browser = await chromium.launch();

for (const theme of THEMES) {
  const ctx = await browser.newContext({ viewport: VIEWPORT });
  const page = await ctx.newPage();

  await page.addInitScript(({ themeId }) => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_theme', themeId);
    } catch {}
  }, { themeId: theme });

  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#export-btn', { state: 'visible', timeout: 15000 });
  await page.waitForFunction((hosts) => hosts.every((selector) => {
    const host = document.querySelector(selector);
    if (!host || !host.matches('[data-tooltip]')) return false;
    const rect = host.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }), HOSTS, { timeout: 15000 });

  const cdp = await ctx.newCDPSession(page);
  await waitForTooltipsMounted(cdp);
  await page.waitForTimeout(200);

  const centres = await hostCentres(page);
  const empty = await findEmptyPoint(page);

  for (let index = 0; index < HOSTS.length; index += 1) {
    const host = HOSTS[index];
    const next = HOSTS[(index + 1) % HOSTS.length];
    const hostCentre = centres[host];
    const nextCentre = centres[next];

    await reportCase(theme, `${host} enter -> visible`, async () => {
      await moveTo(page, empty);
      await moveTo(page, hostCentre);
      const state = await readHost(page, host);
      return { ok: isVisible(state), detail: stateText(state) };
    });

    await reportCase(theme, `${host} leave to empty area -> hidden`, async () => {
      await moveTo(page, empty);
      const state = await readHost(page, host);
      return { ok: isHidden(state), detail: stateText(state) };
    });

    await reportCase(theme, `${host} -> ${next} directly -> only next visible`, async () => {
      await moveTo(page, empty);
      await moveTo(page, hostCentre);
      const before = await readHost(page, host);

      // Straight line from H's centre to the next host's centre, five steps.
      await page.mouse.move(nextCentre.x, nextCentre.y, { steps: 5 });
      await nextFrame(page);

      const states = await readAllHosts(page);
      const otherVisible = HOSTS
        .filter((selector) => selector !== host && selector !== next)
        .filter((selector) => isVisible(states[selector]));

      const ok = isVisible(before)
        && isHidden(states[host])
        && isVisible(states[next])
        && otherVisible.length === 0;

      let detail = `before=${stateText(before)}; after=${host}:${stateText(states[host])} ${next}:${stateText(states[next])}`;
      if (otherVisible.length) detail += `; also-visible=${otherVisible.join(',')}`;
      return { ok, detail };
    });

    await reportCase(theme, `${host} 20 enter/leave cycles -> no residue, listeners stable`, async () => {
      await moveTo(page, empty);
      const before = await listenerInfo(cdp, host);

      for (let cycle = 0; cycle < 20; cycle += 1) {
        await moveTo(page, hostCentre);
        await moveTo(page, empty);
      }

      const after = await listenerInfo(cdp, host);
      const state = await readHost(page, host);
      const ok = before.count >= 0
        && after.count >= 0
        && before.count === after.count
        && isHidden(state);
      return {
        ok,
        detail: `listeners ${before.count}->${after.count}; ${stateText(state)}`,
      };
    });
  }

  await reportCase(theme, 'Esc while tooltip open -> hidden, and re-enter shows it again', async () => {
    const settingsCentre = centres['#settings-btn'];
    await moveTo(page, empty);
    await moveTo(page, settingsCentre);
    const open = await readHost(page, '#settings-btn');

    await page.keyboard.press('Escape');
    await nextFrame(page);
    const afterEsc = await readHost(page, '#settings-btn');

    await moveTo(page, empty);
    await moveTo(page, settingsCentre);
    const reentered = await readHost(page, '#settings-btn');

    const ok = isVisible(open) && isHidden(afterEsc) && isVisible(reentered);
    return {
      ok,
      detail: `open=(${stateText(open)}); Esc=(${stateText(afterEsc)}); re-enter=(${stateText(reentered)})`,
    };
  });

  await ctx.close();
}

await browser.close();
console.log(failed ? `RESULT: FAIL — ${failed} of ${total}` : `RESULT: PASS — ${total} cases`);
process.exit(failed ? 1 : 0);
