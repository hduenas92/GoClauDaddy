/**
 * features-check.mjs — P2-C (WRITE job): build the 3 dropped v1 features + the
 * Incomplete badge.
 *
 * Cases (12 feature cases, all expected red at 76e5e05):
 *   F1 switch-away · F1 New Chat · F1 reload · F1 send clears
 *   F2 composer thumbnail · F2 history thumbnail after reload ·
 *   F2 non-image keeps chip · F2 svg not inline
 *   F3 change applies · F3 persists after reload
 *   F4 stopped:1 shows badge · F4 normal reply shows none
 *
 * METHOD. Same shape as error-visibility-check.mjs: every case gets its own
 * browser context, REST is stub-routed so nothing is written to the real
 * database, and WebSocket behaviour is driven with page.routeWebSocket in
 * mocking mode (Playwright 1.63.0, verified against the installed typings).
 *
 * The guard runs first: if the composer does not mount, every draft/thumbnail/
 * font case below would be vacuous. The guard FAILs (never INCONCLUSIVE) when
 * the composer is missing.
 *
 * Usage:  node tools/features-check.mjs          (app must be on :8765)
 *         GCA_URL=http://127.0.0.1:8766 node tools/features-check.mjs
 * Exit:   0 PASS · 1 FAIL · 2 INCONCLUSIVE (a path could not be exercised)
 */
import { chromium } from 'playwright';

const APP_URL = process.env.GCA_URL ?? 'http://127.0.0.1:8765';

const results = [];
const pass = (name, detail) => results.push({ name, state: 'PASS', detail });
const fail = (name, detail) => results.push({ name, state: 'FAIL', detail });
const skip = (name, detail) => results.push({ name, state: 'INCONCLUSIVE', detail });

const browser = await chromium.launch();

// 1x1 transparent PNG, so <img> elements served through the download route
// actually decode rather than firing onerror during the run.
const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

const CONV_A = {
  id: 'cA', project_id: null, name: 'Chat A', session_id: null,
  model: 'claude-sonnet-4-6', permission_mode: null, system_prompt: null,
  thinking_budget: null, max_tokens: null, status: 'idle', source: 'web',
  created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:00:00Z',
  started_at: null, completed_at: null, cost_usd: 0,
};
const CONV_B = {
  ...CONV_A,
  id: 'cB', name: 'Chat B',
};
const CONV_NEW = {
  ...CONV_A,
  id: 'cNew', name: 'New Chat',
};

const CONFIG_BODY = {
  models: [
    { value: 'claude-sonnet-4-6', label: 'Sonnet 4.6', context_window: 1000000, input_rate: 3, output_rate: 15 },
    { value: 'claude-opus-4-5', label: 'Opus 4.5', context_window: 1000000, input_rate: 5, output_rate: 25 },
    { value: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5', context_window: 200000, input_rate: 1, output_rate: 5 },
  ],
  default_model: 'claude-sonnet-4-6',
  permission_modes: ['auto'],
};

const msg = (over = {}) => ({
  id: 'mA1', conversation_id: 'cA', role: 'assistant', content: 'hello',
  thinking: null, input_tokens: 10, output_tokens: 5,
  model: 'claude-sonnet-4-6', cache_read_tokens: 0, cache_creation_tokens: 0,
  seq: 1, created_at: '2026-09-25T00:00:00Z', tool_calls: null, stopped: false,
  ...over,
});

async function freshPage() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('gca_onboarded', '1');
      localStorage.setItem('gca_feat_assess', '0');
    } catch {}
  });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  return { ctx, page, pageErrors };
}

/**
 * Hermetic boot stubs. Every REST read the app makes at boot is answered with a
 * fixture; every write is intercepted so nothing reaches the real database.
 * Download GETs return a real 1x1 PNG so thumbnail <img> elements decode.
 */
async function stubBoot(page, { messages = [] } = {}) {
  await page.route('**/api/projects', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : r.continue());
  await page.route('**/api/flow-templates', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      : r.continue());
  await page.route('**/api/config', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONFIG_BODY) }));
  await page.route('**/api/server/info', (r) => {
    const m = /^https?:\/\/([^/:]+)(?::(\d+))?/.exec(APP_URL);
    return r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ url: APP_URL, host: m?.[1] ?? '127.0.0.1', port: m?.[2] ? Number(m[2]) : 80 }),
    });
  });
  await page.route('**/api/server/stats', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        chat_count: 2, message_count: 1, total_input: 10, total_output: 5,
        monthly_cost_usd: 0, monthly_input: 10, monthly_output: 5, budget_usd: 200,
      }),
    }));

  // Base conversations collection: list + create.
  await page.route('**/api/conversations', (r) => {
    const m = r.request().method();
    if (m === 'GET') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([CONV_A, CONV_B]) });
    }
    if (m === 'POST') {
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONV_NEW) });
    }
    return r.continue();
  });

  // Individual conversations.
  await page.route('**/api/conversations/cA', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ conversation: CONV_A, messages, model_correction: null }),
        })
      : r.continue());
  await page.route('**/api/conversations/cB', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ conversation: CONV_B, messages: [], model_correction: null }),
        })
      : r.continue());
  await page.route('**/api/conversations/cNew', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ conversation: CONV_NEW, messages: [], model_correction: null }),
        })
      : r.continue());

  // Right-sidebar per-conversation stats, and first-turn auto-title.
  await page.route('**/api/conversations/*/stats', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ step_count: 0, tool_call_count: 0 }) }));
  await page.route('**/api/conversations/*/auto-title', (r) =>
    r.request().method() === 'POST'
      ? r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CONV_A) })
      : r.continue());

  // Thumbnail downloads. Registered before the per-case upload route so the
  // per-case route can fallback() to this one for GETs.
  await page.route('**/api/attachments/*/download', (r) =>
    r.fulfill({ status: 200, contentType: 'image/png', body: PNG_1PX }));
}

/** Click a conversation in the sidebar and wait for the switch to finish. */
async function switchTo(page, title) {
  await page.locator('.conv-item', { hasText: title }).locator('.conv-body').click();
  await page.waitForFunction(
    (t) => document.getElementById('conv-title-text')?.textContent === t,
    title,
    { timeout: 8000 },
  );
}

/** Boot the app, wait for the composer, and return it (or null). */
async function bootToComposer(page, { messages = [] } = {}) {
  await stubBoot(page, { messages });
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  return page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
}

// ---------------------------------------------------------------------------
// 0. Non-empty guard: the composer must exist before any draft case runs.
// ---------------------------------------------------------------------------
{
  const name = '0. guard: composer mounts (every case below depends on it)';
  const { ctx, page, pageErrors } = await freshPage();
  try {
    await stubBoot(page);
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    const input = await page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
    if (!input) {
      fail(name, 'composer never mounted — every draft/thumbnail/font case below would assert on nothing');
    } else if (pageErrors.length) {
      fail(name, `composer mounted but page errors leaked: ${pageErrors.join(' | ')}`);
    } else {
      pass(name, '#composer-input present');
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// F1 — Autosave composer draft
// ---------------------------------------------------------------------------
{
  const name = 'F1 draft survives switching to another conversation and back';
  const { ctx, page } = await freshPage();
  try {
    const input = await bootToComposer(page);
    if (!input) {
      skip(name, 'composer never mounted');
    } else {
      await input.fill('draft-switch');
      await switchTo(page, 'Chat B');
      await switchTo(page, 'Chat A');
      const value = await page.$eval('#composer-input', (el) => el.value);
      if (value !== 'draft-switch') {
        fail(name, `draft lost after switching away and back: composer holds "${value}"`);
      } else {
        // Minimum behaviour folded in: a whitespace-only draft must not be saved.
        await page.$eval('#composer-input', (el) => { el.value = ''; });
        await page.fill('#composer-input', '   ');
        await switchTo(page, 'Chat B');
        await switchTo(page, 'Chat A');
        const wsValue = await page.$eval('#composer-input', (el) => el.value);
        if (wsValue !== '') {
          fail(name, `whitespace-only draft was saved: composer holds "${wsValue}"`);
        } else {
          pass(name, 'draft restored after switch; whitespace-only draft not saved');
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F1 draft survives New Chat and coming back';
  const { ctx, page } = await freshPage();
  try {
    const input = await bootToComposer(page);
    if (!input) {
      skip(name, 'composer never mounted');
    } else {
      await input.fill('draft-newchat');
      await page.click('#new-conv-btn');
      await page.waitForFunction(
        () => document.getElementById('conv-title-text')?.textContent === 'New Chat',
        null,
        { timeout: 8000 },
      );
      const emptyInNew = await page.$eval('#composer-input', (el) => el.value);
      if (emptyInNew !== '') {
        fail(name, `new conversation did not start with an empty composer: "${emptyInNew}"`);
      } else {
        await switchTo(page, 'Chat A');
        const value = await page.$eval('#composer-input', (el) => el.value);
        if (value !== 'draft-newchat') {
          fail(name, `draft lost after New Chat and coming back: composer holds "${value}"`);
        } else {
          pass(name, 'draft restored after New Chat round-trip');
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F1 draft survives a page reload';
  const { ctx, page } = await freshPage();
  try {
    const input = await bootToComposer(page);
    if (!input) {
      skip(name, 'composer never mounted');
    } else {
      await input.fill('draft-reload');
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#composer-input', { timeout: 8000 });
      await page.waitForFunction(
        () => document.getElementById('conv-title-text')?.textContent === 'Chat A',
        null,
        { timeout: 8000 },
      );
      const value = await page.$eval('#composer-input', (el) => el.value);
      if (value !== 'draft-reload') {
        fail(name, `draft lost after reload: composer holds "${value}"`);
      } else {
        pass(name, 'draft restored after reload');
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F1 send clears the saved draft';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    let wsRoute = null;
    let sent = null;
    await page.routeWebSocket('**/ws/chat/*', (route) => {
      wsRoute = route;
      route.onMessage((m) => {
        try {
          const j = JSON.parse(m.toString());
          if (j.type === 'send') {
            sent = j.message;
            route.send(JSON.stringify({ type: 'done' }));
          }
        } catch {}
      });
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    const input = await page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
    if (!input) {
      skip(name, 'composer never mounted');
    } else {
      await input.fill('draft-send');
      const saved = await page.evaluate((id) => localStorage.getItem('gca_draft_' + id), 'cA');
      if (saved !== 'draft-send') {
        fail(name, `draft was not persisted before send (localStorage "${saved}") — send-clears cannot be observed`);
      } else if (!wsRoute) {
        skip(name, 'WebSocket route never intercepted');
      } else {
        await page.click('#composer-send');
        await page.waitForFunction(
          () => document.getElementById('composer-input')?.value === '',
          null,
          { timeout: 8000 },
        );
        const after = await page.evaluate((id) => localStorage.getItem('gca_draft_' + id), 'cA');
        if (after !== null) {
          fail(name, `draft survived send: localStorage "${after}"`);
        } else if (sent !== 'draft-send') {
          fail(name, `sent frame text mismatch: ${JSON.stringify(sent)}`);
        } else {
          pass(name, 'draft persisted, then cleared by send (send frame captured)');
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// F2 — Image preview for attachments
// ---------------------------------------------------------------------------
{
  const name = 'F2 composer chip shows a thumbnail for a .png attachment';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    await page.route('**/api/attachments**', (r) => {
      if (r.request().method() !== 'POST') return r.fallback();
      return r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'a1', original_name: 'pic.png', mime_type: 'image/png', size_bytes: PNG_1PX.length, created_at: '2026-09-25T00:00:00Z' }),
      });
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    const input = await page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
    const fileInput = await page.$('#composer-file-input');
    if (!input || !fileInput) {
      skip(name, 'composer or file input missing');
    } else {
      await fileInput.setInputFiles({ name: 'pic.png', mimeType: 'image/png', buffer: PNG_1PX });
      const img = await page.waitForSelector('#attachment-strip img.attachment-thumb', { timeout: 4000 }).catch(() => null);
      if (!img) {
        fail(name, 'no img.attachment-thumb in the composer chip after attaching a .png');
      } else {
        const alt = await img.getAttribute('alt');
        if (alt !== 'pic.png') {
          fail(name, `thumbnail alt is "${alt}", expected "pic.png"`);
        } else {
          pass(name, 'png attachment renders <img class="attachment-thumb" alt="pic.png"> in the composer chip');
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F2 history shows the attachment thumbnail after reload';
  const { ctx, page } = await freshPage();
  try {
    const userMsg = {
      id: 'mU1', conversation_id: 'cA', role: 'user', content: 'look at this',
      thinking: null, input_tokens: 10, output_tokens: 0,
      model: 'claude-sonnet-4-6', cache_read_tokens: 0, cache_creation_tokens: 0,
      seq: 1, created_at: '2026-09-25T00:00:00Z', tool_calls: null, stopped: false,
      attachments: [{ id: 'a1', original_name: 'pic.png', mime_type: 'image/png' }],
    };
    await stubBoot(page, { messages: [userMsg] });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.msg-user', { timeout: 8000 }).catch(() => null);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.msg-user', { timeout: 8000 }).catch(() => null);
    const img = await page.$('.msg-user img.msg-attachment-thumb');
    if (!img) {
      fail(name, 'no img.msg-attachment-thumb in the sent message after reload');
    } else {
      const alt = await img.getAttribute('alt');
      if (alt !== 'pic.png') {
        fail(name, `history thumbnail alt is "${alt}", expected "pic.png"`);
      } else {
        pass(name, 'sent message renders <img class="msg-attachment-thumb" alt="pic.png"> after reload');
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F2 non-image attachment keeps the filename chip';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    await page.route('**/api/attachments**', (r) => {
      if (r.request().method() !== 'POST') return r.fallback();
      return r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'a2', original_name: 'notes.txt', mime_type: 'text/plain', size_bytes: 1, created_at: '2026-09-25T00:00:00Z' }),
      });
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    const input = await page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
    const fileInput = await page.$('#composer-file-input');
    if (!input || !fileInput) {
      skip(name, 'composer or file input missing');
    } else {
      await fileInput.setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('x') });
      const chip = await page.waitForSelector('#attachment-strip .attachment-chip', { timeout: 4000 }).catch(() => null);
      if (!chip) {
        fail(name, 'no attachment chip appeared for a .txt upload');
      } else {
        const nameEl = await chip.$('.attachment-chip-name');
        const imgs = await chip.$$('img');
        if (!nameEl) {
          fail(name, 'non-image chip has no .attachment-chip-name span');
        } else {
          const text = (await nameEl.textContent()).trim();
          if (text !== 'notes.txt') {
            fail(name, `chip filename is "${text}", expected "notes.txt"`);
          } else if (imgs.length) {
            fail(name, 'non-image chip rendered an <img>');
          } else {
            pass(name, 'txt attachment keeps a filename chip ("notes.txt") and no thumbnail');
          }
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F2 svg-named attachment is never rendered inline';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page);
    await page.route('**/api/attachments**', (r) => {
      if (r.request().method() !== 'POST') return r.fallback();
      return r.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'a3', original_name: 'evil.svg', mime_type: 'image/svg+xml', size_bytes: 10, created_at: '2026-09-25T00:00:00Z' }),
      });
    });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    const input = await page.waitForSelector('#composer-input', { timeout: 8000 }).catch(() => null);
    const fileInput = await page.$('#composer-file-input');
    if (!input || !fileInput) {
      skip(name, 'composer or file input missing');
    } else {
      await fileInput.setInputFiles({ name: 'evil.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>') });
      const chip = await page.waitForSelector('#attachment-strip .attachment-chip', { timeout: 4000 }).catch(() => null);
      if (!chip) {
        fail(name, 'no attachment chip appeared for the svg-named upload');
      } else {
        const html = await page.$eval('#attachment-strip', (el) => el.innerHTML);
        const nameEl = await chip.$('.attachment-chip-name');
        const imgs = await chip.$$('img');
        const svgs = await chip.$$('svg');
        if (!nameEl) {
          fail(name, 'svg-named chip has no .attachment-chip-name span');
        } else if ((await nameEl.textContent()).trim() !== 'evil.svg') {
          fail(name, `chip filename mismatch: "${(await nameEl.textContent()).trim()}"`);
        } else if (imgs.length || svgs.length || /<svg|data:image\/svg/i.test(html)) {
          fail(name, `svg-named attachment rendered inline or as an img: ${html.slice(0, 120)}`);
        } else {
          pass(name, 'svg-named attachment stays a filename chip; no <img>, no inline <svg>, no data: URL');
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// F3 — Font size controls (chat, not the xterm fontSize)
// ---------------------------------------------------------------------------
{
  const name = 'F3 font-size change applies to chat + composer without reload';
  const { ctx, page } = await freshPage();
  try {
    const input = await bootToComposer(page, { messages: [msg()] });
    if (!input) {
      skip(name, 'composer never mounted');
    } else {
      await page.keyboard.press('Control+Comma');
      const sel = await page.waitForSelector('#chat-font-size', { state: 'visible', timeout: 4000 }).catch(() => null);
      if (!sel) {
        fail(name, 'no #chat-font-size control in the settings drawer');
      } else {
        await page.selectOption('#chat-font-size', '16px');
        await page.waitForTimeout(150);
        const sizes = await page.evaluate(() => ({
          bubble: getComputedStyle(document.querySelector('.msg-bubble')).fontSize,
          composer: getComputedStyle(document.getElementById('composer-input')).fontSize,
          stored: localStorage.getItem('gca_chat_font_size'),
        }));
        if (sizes.bubble !== '16px' || sizes.composer !== '16px') {
          fail(name, `font size did not apply without reload: ${JSON.stringify(sizes)}`);
        } else {
          pass(name, `16px applied to bubble and composer without reload (localStorage=${sizes.stored})`);
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F3 font-size choice persists across reload';
  const { ctx, page } = await freshPage();
  try {
    const input = await bootToComposer(page, { messages: [msg()] });
    if (!input) {
      skip(name, 'composer never mounted');
    } else {
      await page.keyboard.press('Control+Comma');
      const sel = await page.waitForSelector('#chat-font-size', { state: 'visible', timeout: 4000 }).catch(() => null);
      if (!sel) {
        fail(name, 'no #chat-font-size control in the settings drawer');
      } else {
        await page.selectOption('#chat-font-size', '16px');
        await page.reload({ waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#composer-input', { timeout: 8000 });
        await page.waitForFunction(
          () => document.getElementById('conv-title-text')?.textContent === 'Chat A',
          null,
          { timeout: 8000 },
        );
        await page.keyboard.press('Control+Comma');
        await page.waitForSelector('#chat-font-size', { state: 'visible', timeout: 4000 }).catch(() => null);
        const sizes = await page.evaluate(() => ({
          bubble: getComputedStyle(document.querySelector('.msg-bubble')).fontSize,
          composer: getComputedStyle(document.getElementById('composer-input')).fontSize,
          select: document.getElementById('chat-font-size')?.value ?? null,
        }));
        if (sizes.bubble !== '16px' || sizes.composer !== '16px' || sizes.select !== '16px') {
          fail(name, `font size did not persist after reload: ${JSON.stringify(sizes)}`);
        } else {
          pass(name, '16px persisted across reload (select + bubble + composer all 16px)');
        }
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// F4 — Incomplete badge
// ---------------------------------------------------------------------------
{
  const name = 'F4 stopped:1 assistant reply shows an Incomplete badge after reload';
  const { ctx, page } = await freshPage();
  try {
    await stubBoot(page, { messages: [msg({ id: 'mS1', role: 'assistant', content: 'cut off', stopped: true })] });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.msg-assistant', { timeout: 8000 }).catch(() => null);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.msg-assistant', { timeout: 8000 }).catch(() => null);
    const badge = await page.$('.msg-assistant .incomplete-badge');
    if (!badge) {
      fail(name, 'no .incomplete-badge on a stopped:1 assistant message after reload');
    } else {
      const text = (await badge.textContent()).trim();
      if (text !== 'Incomplete') {
        fail(name, `badge text is "${text}", expected "Incomplete"`);
      } else {
        pass(name, 'Incomplete badge rendered on the stopped assistant message after reload');
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

{
  const name = 'F4 normal reply shows no badge (exactly one badge, on the stopped message)';
  const { ctx, page } = await freshPage();
  try {
    const stoppedMsg = msg({ id: 'mS1', role: 'assistant', content: 'cut off', stopped: true, seq: 1 });
    const normalMsg = msg({ id: 'mS2', role: 'assistant', content: 'complete', stopped: false, seq: 2 });
    await stubBoot(page, { messages: [stoppedMsg, normalMsg] });
    await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.msg-assistant', { timeout: 8000 }).catch(() => null);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.msg-assistant', { timeout: 8000 }).catch(() => null);
    const count = await page.locator('.msg-assistant .incomplete-badge').count();
    if (count !== 1) {
      fail(name, `expected exactly 1 badge (on the stopped message), found ${count}`);
    } else {
      const placement = await page.evaluate(() => {
        const rows = [...document.querySelectorAll('.msg-assistant')];
        return {
          onStopped: !!rows[0]?.querySelector('.incomplete-badge'),
          onNormal: !!rows[1]?.querySelector('.incomplete-badge'),
        };
      });
      if (!placement.onStopped || placement.onNormal) {
        fail(name, `badge on the wrong message: ${JSON.stringify(placement)}`);
      } else {
        pass(name, 'exactly one badge, on the stopped message; the normal reply has none');
      }
    }
  } finally {
    await ctx.close().catch(() => {});
  }
}

await browser.close();

console.log('\n=== features-check ===\n');
for (const r of results) {
  console.log(`${r.state.padEnd(13)} ${r.name}`);
  console.log(`              ${r.detail}`);
}

// Non-empty-set guard (§2.2). If nothing ran, this harness proves nothing, and
// "0 failures" must not read as success.
if (results.length === 0) {
  console.log('\nRESULT: FAIL — no case executed; this run asserted nothing.');
  process.exit(1);
}

const failed = results.filter((r) => r.state === 'FAIL');
const skipped = results.filter((r) => r.state === 'INCONCLUSIVE');
const passed = results.filter((r) => r.state === 'PASS');

console.log(`\n${passed.length} passed · ${failed.length} failed · ${skipped.length} inconclusive · ${results.length} case(s) run`);
console.log('\nNOT COVERED, stated rather than silently omitted:');
console.log('  F1 whitespace-only draft is folded into the F1 switch-away case.');
console.log('  F2 the backend already rejects .svg at upload (attachments_service.py'); 
console.log('  extension allowlist); this harness simulates an svg-named row to prove');
console.log('  the frontend never inlines it.');

if (failed.length) { console.log(`\nRESULT: FAIL — ${failed.length}`); process.exit(1); }
if (skipped.length) { console.log(`\nRESULT: INCONCLUSIVE — ${skipped.length} path(s) not exercised`); process.exit(2); }
console.log('\nRESULT: PASS — every exercised feature case passes');
process.exit(0);
