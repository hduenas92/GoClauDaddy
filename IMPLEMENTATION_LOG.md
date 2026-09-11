# Implementation Log

The full chronological record of how ClaudioUI went from an unscoped learning project to a field-tested internal tool. `ARCHITECTURE.md` describes what it *is*; `CHANGELOG.md` is the detailed bug-by-bug record of the hardening round; this document is the narrative of *how it got there*, kept as a reference for replicating this process on future work.

## Origin

ClaudioUI began as two divergent, single-file scripts (`claudioui_server.py`, `claudioui_server.ps1`) that shelled out to the `claude` CLI and streamed output to a browser — no real architecture, embedded HTML/CSS/JS as string literals, inconsistent persistence (JSON files vs. `localStorage`), and the two implementations no longer behaved the same way. The org's only path to Claude is a CaaS-fronted API key wired into the `claude` CLI (no Claude.ai account, no Desktop, no web Claude Code), so the CLI is the sole integration point.

**Decision:** fresh rebuild rather than patch the legacy code, targeted at non-technical/terminal-shy coworkers, each running their own local instance. Old files archived to `legacy/`, not deleted. Full requirements-gathering and locked-in decisions are recorded in the original plan file; see `ARCHITECTURE.md` for the resulting design.

## v1 build (commit `00d4141`)

Built in phases, each verified end-to-end against the real `claude` CLI before moving on (the standing rule for this project: every component tested against real behavior, not just code review or mocks):

1. Skeleton — FastAPI app, health check, static serving.
2. Vertical slice — one conversation, `claude_cli.py` + `stream_parser.py` + WebSocket handler; proved session continuity (`--resume` across turns) against the real CLI.
3. SQLite persistence — schema, multi-conversation sidebar, messages surviving a restart.
4. Model & permission-mode controls, whitelisted server-side.
5. Projects — working-dir + system-prompt containers, folder picker.
6. Attachments — upload, drag/drop, path-injection into the prompt, orphan sweep.
7. Polish — vendored markdown/syntax-highlighting, status badges, stop button, theme.

Non-functional baseline built in from the start: no CORS, Pydantic validation on every request, parameterized SQL only, structured rotating-file logging, a startup self-check that fails loudly with one clear reason, a watchdog that auto-restarts on crash (capped to prevent an infinite loop), filename sanitization, upload size limits, `127.0.0.1`-only binding.

Two real bugs were caught and fixed *during* this build via real CLI/browser testing rather than code review: a WebSocket concurrency bug (`stop` never processed while a `send` was in-flight — receive loop was awaiting the full turn before looping back) and an intermittent character-corruption bug (`a72d789` — chunked stdout decoded independently instead of with a persistent incremental UTF-8 decoder, corrupting multi-byte characters split across reads).

`README.md` (`45d813c`) and `FIRST_TEST.md` (`e20e6bc`) written for non-technical first-time users.

## Field testing and hardening round

v1 was then handed to real coworkers on machines the developer didn't control — every fix below was found this way, none by code review. Full symptom/root-cause/fix/verification detail for each is in `CHANGELOG.md`; summarized here in order:

1. **Missing log directory** crashed first launch on a genuinely fresh machine (`8683ad7`).
2. **Too-new Python (3.14)** on a coworker's machine had no prebuilt dependency wheels yet, triggering a doomed Rust source build. Fixed with explicit version-gating (`20206a5`) plus a network preflight and self-updating `claude` CLI (`e513905`).
3. **`winget` absent** on a real machine (it's an optional Windows component, not guaranteed present as originally assumed) — added a direct python.org download fallback (`803b3a2`).
4. **ARM64 not handled** in that new fallback, found via a proactive audit, not a report (`52a9c87`) — then a **self-inflicted regression** in that very fix (`9c90e71`): a variable set and read inside the same parenthesized block used bare `%` instead of delayed `!` expansion, producing a 404 for everyone, not just ARM64 users. The exact bug class the launcher's own header comment already warned about.
5. **Auth setup was undiscoverable** — the startup check correctly refused to run without persistent CaaS env vars (working as designed), but pointed at "your org's setup instructions" with nothing in the repo actually documenting them. Added a "One-time setup" section to `README.md` (`26378c7`).
6. **The core chat-request bug**: server started fine, but every chat message failed with "claude CLI not found on PATH." Root cause: `shutil.which()` (used by the startup check) applies Windows' `PATHEXT` and finds a `claude.cmd`/`.ps1` shim; `asyncio.create_subprocess_exec()` (used to actually spawn `claude`) does not — so a machine where `claude` only resolves to a shim passed the startup check but failed on every real request. Reproduced directly, fixed by resolving through `shutil.which()` before spawning, and confirmed field-verified after the fix (`6e1758c`, `bf894e6`).
7. **Attachment chip cleanup**: a known UX gap (not a coworker report) — removing a pending attachment before sending only updated local UI state, leaving a permanent orphaned file + DB row forever. Fixed and verified end-to-end in a real browser session (`7ad889b`).

`CHANGELOG.md` written (`a054977`) specifically to capture this round's lessons for future reference — notably that cmd.exe delayed-expansion bugs and Windows PATH-resolution mismatches were each responsible for more than one real failure.

## Current status (as of 2026-09-11)

v1 is deployed and field-verified across multiple real coworker machines. The hardening round above is closed. A rebrand (new name chosen, visual theme still being designed) is in progress separately, with a Project rename/edit UI deliberately deferred until that design settles — its backend already exists in full (`PATCH /api/projects/{id}`), only the frontend UI is missing.

## Process notes worth repeating on future work

- **Every fix in the hardening round was found by testing on a real, uncontrolled machine** — none by code review. This is now the established pattern for this project, not a one-off.
- **Reproduce before fixing, and confirm the fix by disabling it and watching the test fail** — done explicitly for the PATH-resolution bug and the attachment cleanup, not just "looks right."
- **A batch-file regression is very often a delayed-expansion (`!var!` vs `%var%`) bug** — check this first; it has already caused two separate real failures in this codebase.
- **"An OS feature/tool is present" is an assumption to verify, not state** — winget, PATHEXT resolution, and Python version compatibility were all wrong assumptions that only surfaced on real hardware.
