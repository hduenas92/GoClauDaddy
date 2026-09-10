# ClaudioUI — Architecture (rebuilt 2026-09)

A local, single-user web UI for the `claude` CLI. Rebuilt from scratch to replace two divergent single-file implementations (now archived under `legacy/`). Full design rationale lives in `.claude/plans/what-do-you-understand-squishy-elephant.md`; this doc describes what actually shipped.

## Why it exists

The org only provides Claude access via a CaaS-fronted API key wired into the `claude` CLI (no Claude.ai account, no Desktop, no web Claude Code). This is a proper web frontend on top of that CLI, aimed at non-technical/terminal-shy users, each running their own local instance.

## Stack

- **Backend**: FastAPI + `uvicorn`, SQLite (stdlib `sqlite3`), no ORM.
- **Frontend**: plain ES modules, no bundler, no framework. Vendored `marked.js` (MIT) + `highlight.js` (BSD-3-Clause) as pinned static files.
- **Transport**: one WebSocket per conversation (`/ws/chat/{conversation_id}`) carries the prompt, streamed reply, and stop command over a single connection. Everything else is plain REST.

## Directory layout

```
backend/
  app/
    main.py            # FastAPI app factory, lifespan, routers, WS route
    config.py           # every path/constant lives here — the one place to change the port, model list, etc.
    logging_setup.py     # rotating file logger (~/.claudioui/logs/app.log)
    startup_check.py     # fails loudly before serving: disk writable, port free, claude on PATH, auth env vars present
    watchdog.py          # optional supervisor: restarts run.py on crash, capped at 3x/5min
    db/                  # schema.sql, connection.py (WAL, parameterized only), migrations.py (numbered, append-only)
    models/              # plain dataclasses (Project, Conversation, Message, Attachment) with from_row()
    services/            # the actual logic — see below
    routers/             # thin FastAPI routers, Pydantic request/response models, no business logic
    ws/chat_socket.py     # the WebSocket handler tying everything together
  tests/                 # pytest — unit tests for parsing/commands/services, no mocking of the LLM itself
  run.py                 # entrypoint: startup checks, then uvicorn.run(host="127.0.0.1", ...)
frontend/
  index.html
  static/
    css/                 # base.css (app styles) + vendor/ (highlight.js theme)
    js/
      main.js             # boots the app, wires everything together
      api/                # http.js (REST), socket.js (WebSocket wrapper)
      state/              # store.js (pub-sub), actions.js (the only code that calls api/* and mutates state)
      ui/                 # one module per panel — sidebar_projects, sidebar_conversations, chat_pane, composer, settings_panel
      render/              # content_registry.js (extension seam), markdown.js (marked+hljs, escapes HTML first)
      vendor/               # marked.min.js, highlight.min.js
legacy/                    # the two original implementations, archived, unmaintained
```

## The `claude` CLI boundary (`services/claude_cli.py`)

Every value that reaches the subprocess argv is either a hardcoded flag or has passed a whitelist:

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
- `--thinking-budget` does not exist on this CLI version — the old PS1 code's flag was invented/wrong; there is no thinking-budget control in this app.
- Real `--permission-mode` values: `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`.

Runs via `asyncio.create_subprocess_exec` (never blocks the event loop), with a configurable timeout (`RESPONSE_TIMEOUT_SECONDS`, default 10 min) that force-kills a hung process and emits a `timeout` event rather than hanging forever. `process_registry.py` tracks one live subprocess per conversation and backs `/stop`.

## Session, project, and attachment resolution (`ws/chat_socket.py`)

On every `send`:
1. Look up the conversation (must already exist via `POST /api/conversations`).
2. If it belongs to a project, `cwd` = project's `working_dir`, `system_prompt` defaults to the project's (conversation-level override still wins if set); otherwise `cwd` defaults to the user's home directory.
3. Any `attachment_ids` in the payload are resolved to file paths and appended to the prompt as `[Attached file: <path>]` lines — the CLI is text-in/text-out, so a path reference is the only way a file crosses that boundary. Attachments live under `~/.claudioui/attachments/<conversation_id>/`, not a swept OS temp dir, so they survive restarts.
4. The user's message is persisted to SQLite **before** the subprocess even starts (crash-safe: a crash mid-response loses at most the in-flight assistant turn, never what was asked).
5. Streamed events are forwarded over the WebSocket as they arrive and simultaneously accumulated; on completion the full assistant message (text + thinking + token usage) is persisted in one write.

## Data model (`db/schema.sql`)

`projects` (name, working_dir, system_prompt) → `conversations` (project_id nullable, session_id, model, permission_mode, status) → `messages` (role, content, thinking, token counts, seq) and `attachments` (path, size, mime type). `app_config` is a free-form KV table for global settings. `schema_version` + `migrations.py` make future schema changes additive, not rewrites.

## Reliability

- Global FastAPI exception handler: one bad request logs a full traceback and returns a generic error, never crashes the whole process.
- `watchdog.py`: an optional supervisor that restarts a crashed `run.py` up to 3 times per 5-minute window, then stops and points at the log file rather than looping forever. `Launch ClaudioUi.bat` runs the app through this by default.
- Rotating file log at `~/.claudioui/logs/app.log` (5MB × 5 backups), leveled, tagged enough to trace a bad exchange back to one conversation.
- Startup self-checks (disk writable, port free, `claude` on PATH, required auth env vars present) fail with one clear printed reason instead of a silent hang or a deep exception.

## Security notes

- No CORS headers (frontend and API share an origin — the old code's `Access-Control-Allow-Origin: *` served no purpose).
- Every SQL query is parameterized; every subprocess argv value is either a constant or whitelist-checked.
- Attachment filenames are sanitized (path separators and `..` stripped) before touching disk; uploads are capped by size and a file-extension allowlist.
- The CaaS API key (`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_BASE_URL`) is never read, stored, or proxied by this codebase — it's the `claude` CLI's own responsibility via the process environment. `Launch ClaudioUi.bat` and `startup_check.py` only ever check those env vars are *present*.
- Markdown rendering escapes HTML before handing text to `marked.js` (which does not sanitize by default) — verified with a real XSS probe (`<script>`/`<img onerror>`) through a live browser; neither fired.

## Deployment

`Launch ClaudioUi.bat` treats every machine as a fresh setup: installs Python via winget and the `claude` CLI via its official installer if either is missing, creates a `.venv` and installs pinned dependencies on first run only, then launches the app through the watchdog. The one thing it cannot automate is the org's CaaS credential — that's a `setx`-based one-time step per the org's own onboarding instructions, checked but never performed by this script.

## Extending it later

- **New backend feature**: one `services/*.py` + one `routers/*.py`, registered in `main.py`. `process_registry` and the WS handler are already generic per-conversation, so most features don't touch them.
- **New frontend panel**: one `ui/*.js` module. Message rendering always goes through `render/content_registry.js`'s registry rather than a type switch in `chat_pane.js` — an Artifacts-style panel later is `registry.register('artifact', ...)` plus a new panel module, not a rewrite.
- **Schema changes**: append a new numbered entry to `db/migrations.py`; never edit a shipped one.
