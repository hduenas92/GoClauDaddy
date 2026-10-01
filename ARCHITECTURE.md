# GoClaudaddy — Architecture (rebuilt 2026-09)

A local, single-user web UI for the `claude` CLI. Rebuilt from scratch to replace two divergent single-file implementations (now archived under `legacy/`); this doc describes what actually shipped.

## Why it exists

The org only provides Claude access via a CaaS-fronted API key wired into the `claude` CLI (no Claude.ai account, no Desktop, no web Claude Code). This is a proper web frontend on top of that CLI, aimed at non-technical/terminal-shy users, each running their own local instance.

## Stack

- **Backend**: FastAPI + `uvicorn`, SQLite (stdlib `sqlite3`), no ORM.
- **Frontend**: plain ES modules, no bundler, no framework. Vendored `marked.js` (MIT) + `highlight.js` (BSD-3-Clause) as pinned static files.
- **Transport**: one WebSocket per conversation (`/ws/chat/{conversation_id}`) carries send, streamed reply, stop, and approval responses. Everything else is plain REST, except the right sidebar's LOG panel, which consumes a server-sent-events stream from `routers/server.py`.
- There is no terminal feature: no xterm, no `/ws/terminal`, and no ``Ctrl+` `` shortcut.

## Directory layout

```
backend/
  app/
    main.py            # FastAPI app factory, lifespan, routers, WS route
    config.py           # every path/constant lives here — data dir ~/.goclaudaddy, port, model list, etc.
    logging_setup.py     # rotating file logger (~/.goclaudaddy/logs/app.log)
    startup_check.py     # fails loudly before serving: disk writable, port free, claude on PATH, auth env vars present
    watchdog.py          # supervisor: restarts run.py on unexpected exit, capped at 3 restarts per rolling 5-min window
    data_migration.py    # one-time copy ~/.claudioui -> ~/.goclaudaddy
    db/                  # schema.sql, connection.py (WAL, parameterized only), migrations.py (numbered, append-only)
    models/              # plain dataclasses (Project, Conversation, Message, Attachment) with from_row()
    services/            # claude_cli.py, stream_parser.py, process_registry.py, attachments_service.py, projects_service.py, conversations_service.py, cost.py, dir_picker.py
    routers/             # thin FastAPI routers; server.py serves /api/server/info, /api/server/stats, and the SSE log stream
    ws/chat_socket.py     # WebSocket handler tying everything together
  tests/                 # pytest — unit tests for parsing/commands/services, no mocking of the LLM itself
  run.py                 # entrypoint: data migration, startup checks, then uvicorn.run(host="127.0.0.1", ...)
frontend/
  index.html
  static/
    css/                 # base.css (app styles) + vendor/ (highlight.js theme)
    js/
      main.js             # boots the app, wires panels/settings/shortcuts together
      honeycomb.js        # decorative animated honeycomb canvas background
      api/http.js         # thin REST wrapper
      api/socket.js       # one WebSocket per conversation; JSON send/receive dispatch
      api/template_cache.js # in-memory flow-template cache shared by pickers
      state/store.js      # pub-sub state store
      state/actions.js    # the only code that calls api/* and mutates state
      state/storage.js    # safe localStorage wrapper
      ui/sidebar_projects.js       # project list, create/edit/delete, working-dir picker
      ui/sidebar_conversations.js  # conversation list, search, "+ New Chat"
      ui/chat_pane.js              # transcript rendering, streaming bubbles, export/retry
      ui/composer.js               # input box, send/stop, attachments, ↑/↓ sent-message history
      ui/composer_draft.js         # per-conversation draft autosave (localStorage)
      ui/settings_panel.js         # ⚙ Settings drawer: model, permission mode, thinking, max tokens, prompt, theme, font size, budget, feature toggles
      ui/right_sidebar.js          # SESSION / MONTH / PROJECT / SERVER / LOG panels; LOG consumes the SSE stream
      ui/modal.js                  # shared modal/confirm/error-toast helpers
      ui/onboarding_tour.js        # first-visit intro overlay
      ui/attachment_view.js        # shared attachment chip/image rendering rules
      ui/template_picker.js        # full template browser + custom-template CRUD
      ui/tooltips.js               # accessible [data-tooltip] behavior
      render/content_registry.js   # extension seam for message renderers
      render/markdown.js           # marked + hljs, escapes HTML first
      vendor/marked.min.js         # vendored markdown renderer
      vendor/highlight.min.js      # vendored code highlighter
legacy/                    # the two original implementations, archived, unmaintained
```

## The `claude` CLI boundary (`services/claude_cli.py`)

`build_command()` constructs the CLI argv in this exact order:

```
claude -p --output-format stream-json --verbose --model <model>
       [--permission-mode <mode>]   # only if in PERMISSION_MODES whitelist
       [--system-prompt <prompt>]
       [--resume <session_id>]      # never --continue
       <prompt>
```

Notes earned the hard way (verified against the real installed CLI, not assumed from the old code):
- `--verbose` is required alongside `-p --output-format stream-json` or the CLI refuses to start.
- The flag is `--system-prompt`, not `--system`.
- Token controls are passed as environment variables, not argv: `CLAUDE_CODE_MAX_OUTPUT_TOKENS` for max output tokens and `MAX_THINKING_TOKENS` for thinking budget (`build_env()`). The tested CLI rejects `--max-tokens` and `--thinking`.
- Real `--permission-mode` values: `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`.

Runs via `asyncio.create_subprocess_exec` (never blocks the event loop), with a configurable timeout (`RESPONSE_TIMEOUT_SECONDS`, default 10 min) that force-kills a hung process and emits a `timeout` event rather than hanging forever. `process_registry.py` tracks one live subprocess per conversation; **Stop is a WebSocket message** (`{"type": "stop"}`), not a `/stop` route.

## Session, project, and attachment resolution (`ws/chat_socket.py`)

On every `send`:
1. Look up the conversation (must already exist via `POST /api/conversations`).
2. If it belongs to a project, `cwd` = project's `working_dir`, `system_prompt` defaults to the project's (conversation-level override still wins if set); otherwise `cwd` defaults to the user's home directory.
3. Any `attachment_ids` in the payload are resolved to file paths and appended to the prompt as `[Attached file: <path>]` lines — the CLI is text-in/text-out, so a path reference is the only way a file crosses that boundary. Attachments live under `~/.goclaudaddy/attachments/<conversation_id>/`, not a swept OS temp dir, so they survive restarts.
4. The user's message is persisted to SQLite **before** the subprocess even starts (crash-safe: a crash mid-response loses at most the in-flight assistant turn, never what was asked).
5. Streamed events are forwarded over the WebSocket as they arrive and simultaneously accumulated; on completion the full assistant message (text + thinking + token usage) is persisted in one write.

## Data model (`db/schema.sql` + `db/migrations.py`)

Current tables after the latest migration:
- `projects` — name, working_dir, system_prompt, timestamps.
- `conversations` — project_id nullable, session_id, model, permission_mode, status, plus later columns for system_prompt, thinking_budget, source, started_at, completed_at, max_tokens.
- `messages` — role, content, thinking, token counts, seq, plus later columns for model, cache read/creation tokens, tool_calls, stopped, superseded_by.
- `attachments` — conversation/message links, original_name, stored_path, mime_type, size_bytes.
- `flow_templates` — built-in and custom prompt templates (id, title, description, body, category, is_builtin, sort_order).
- `messages_fts` — FTS5 external-content virtual table over `messages.content`, kept in sync by triggers.
- `schema_version` — the migration runner's current version.

There is no `app_config` table: migration 19 dropped it (along with `conversation_tags` and `project_files`) because no code read it. Migrations 12–13 created `team_sessions` and `team_members`, but migration 20 drops them when empty. `schema_version` + `migrations.py` make future schema changes additive, not rewrites.

## Reliability

- Global FastAPI exception handler: one bad request logs a full traceback and returns a generic error, never crashes the whole process.
- `watchdog.py`: the launcher entrypoint (`python -m app.watchdog`) runs `run.py` and restarts it after an unexpected exit, allowing at most 3 restarts in any rolling 5-minute window. A fourth unexpected exit in that window stops the loop and points at the log file; a clean exit (rc=0) is not restarted. `Launch GoClaudaddy.bat` runs the app through this by default.
- Rotating file log at `~/.goclaudaddy/logs/app.log` (5MB × 5 backups), using the `goclaudaddy` logger namespace.
- Startup self-checks (disk writable, port free, `claude` on PATH, required auth env vars present) fail with one clear printed reason instead of a silent hang or a deep exception.
- The right sidebar's LOG panel consumes the SSE stream at `GET /api/server/logs` (`routers/server.py`).

## Security notes

- No CORS headers: the frontend and API are served from the same origin.
- Every SQL query is parameterized; `permission_mode` is checked against `PERMISSION_MODES` before it reaches the CLI argv.
- Attachment filenames are sanitized (path separators and `..` stripped) before touching disk; uploads are capped by size and a file-extension allowlist.
- The CaaS API key (`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_BASE_URL`) is never logged, stored, or proxied by this codebase — it's the `claude` CLI's own responsibility via the process environment. `startup_check.py` only checks that the variables are present; the launcher itself does not read them.
- Markdown rendering escapes HTML before handing text to `marked.js` (which does not sanitize by default).

## Deployment

`Launch GoClaudaddy.bat` treats every machine as a fresh setup: installs Python via winget (or the official installer fallback) and the `claude` CLI via its official installer if either is missing, creates a `.venv` and installs pinned dependencies on first run only, then launches the app through the watchdog. The one thing it cannot automate is the org's CaaS credential — that's a `setx`-based one-time step per the org's own onboarding instructions, checked by the app but never performed by this script.

## Extending it later

- **New backend feature**: one `services/*.py` + one `routers/*.py`, registered in `main.py`. `process_registry` and the WS handler are already generic per-conversation, so most features don't touch them.
- **New frontend panel**: one `ui/*.js` module. Message rendering always goes through `render/content_registry.js`'s registry rather than a type switch in `chat_pane.js` — an Artifacts-style panel later is `registry.register('artifact', ...)` plus a new panel module, not a rewrite.
- **Schema changes**: append a new numbered entry to `db/migrations.py`; never edit a shipped one.
