# ClaudioUI — Codebase Reference

A self-contained local web UI for streaming conversations with the `claude` CLI. No build step, no framework, no external dependencies beyond the standard library — the entire frontend (HTML/CSS/JS) is embedded as a string inside the backend script and served on `http://127.0.0.1:8765`.

There are **two independent server implementations** of the same idea. They are not kept in sync — see [Divergence between implementations](#divergence-between-implementations).

| File | Runtime | Lines | Role |
|---|---|---|---|
| [claudioui_server.py](claudioui_server.py) | Python 3 (`http.server`, stdlib only) | ~2,180 | Documented as "primary" in `CLAUDE.md`, but simpler/older feature set |
| [claudioui_server.ps1](claudioui_server.ps1) | Pure PowerShell (`HttpListener`) | ~2,440 | What the shipped launchers (`.bat`/`.vbs`) actually start; more features |
| [claudioui_gui.py](claudioui_gui.py) | Python + Tkinter | ~185 | Small launcher/watchdog window — Launch/Stop/Restart/Open Browser buttons, polls `/api/state` every 3s |
| [Launch ClaudioUi.bat](Launch%20ClaudioUi.bat) / [.vbs](Launch%20ClaudioUi.vbs) | Batch / VBScript | — | Kill anything on port 8765, start `claudioui_server.ps1` hidden/minimized, open the browser once `/api/state` responds |
| [apply_style.py](apply_style.py) / [.ps1](apply_style.ps1) | one-shot helper | — | Splices a replacement `<style>` block from `new_style_temp.txt` into the PS1 server's embedded HTML |

## What it does

A single-page chat UI (sidebars + chat pane) that shells out to the `claude` CLI per message and streams the JSON/text output back to the browser over Server-Sent Events. It layers on top of the CLI:

- **Multiple conversations**, each mapped to a `claude` session ID so `--resume` continues the right thread.
- **Model / max-tokens / system-prompt** controls exposed as UI panels, applied via `/api/config`.
- **Token & cost tracking** (input/output tokens, running `$` estimate) shown in a metrics panel.
- **A draggable/collapsible/resizable panel layout**, persisted to `localStorage`.
- **Server lifecycle controls** in the browser itself (Restart / Stop / Open) via `/api/restart` and `/api/shutdown`.

## Architecture (Python server, primary per CLAUDE.md)

All state lives in a single in-memory `state` dict in [claudioui_server.py](claudioui_server.py):

| key | purpose |
|---|---|
| `conversations` | dict of conversation objects, persisted to `~/.claudioui/conversations.json` |
| `active_conv` | currently selected conversation ID |
| `proc` | the running `claude` subprocess (or `None`) |
| `model`, `max_tokens`, `system_prompt` | config, persisted to `~/.claudioui/config.json` |

A conversation object: `{id, name, session_id, created_at, updated_at, status, message_count, messages[]}`.

### API surface (Python)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Full state snapshot (conversations, model, token/cost totals) — polled every 5s by the frontend |
| GET | `/api/tokens` | Token/cost counters only |
| GET | `/api/browse` | Opens a native Tk folder picker dialog (blocks the request thread) |
| POST | `/api/chat` | Streams a reply via SSE (`text/event-stream`, chunked) |
| POST | `/api/conversations` | Create a new conversation |
| POST | `/api/conversations/active` | Switch active conversation |
| PATCH | `/api/conversations/<id>` | Rename |
| DELETE | `/api/conversations/<id>` | Delete |
| POST | `/api/config` | Update `model` / `max_tokens` / `system_prompt` |
| POST | `/api/restart` | Spawns a fresh copy of the script, then `os._exit(0)` |
| POST | `/api/shutdown` | `os._exit(0)` after a short delay |

### How `/api/chat` works

1. Builds `claude -p --output-format stream-json --model <m> --max-tokens <n> [--system <prompt>] [--resume <session_id>] <prompt>`.
2. Runs it as a subprocess (`CREATE_NO_WINDOW` on Windows), reads stdout line by line.
3. Each line is parsed as JSON (`stream-json` format): `system/init` → captures `session_id` and stores it on the conversation; `assistant` blocks → forwards `thinking`/`text` deltas as SSE events; `result` → final usage numbers, updates running token/cost totals.
4. ANSI escape codes are stripped (`strip_ansi`) before anything is forwarded.
5. Session continuity is achieved purely via `--resume <session_id>`, one per conversation — never `--continue` (that would pick up whatever the last CLI session was, not necessarily this conversation's).

### Frontend (embedded in `HTML = r"""..."""`)

- Vanilla JS, no framework. State lives in a single `S` object (`convs`, `active`, `thinking`, `queue`, token counters, model, message history for ↑/↓ recall).
- Uses `XMLHttpRequest` with `onprogress` (not `fetch`+`ReadableStream`) to consume the SSE-shaped stream — parses `data: {...}\n\n` frames out of `xhr.responseText` incrementally.
- Sending while a response is in-flight queues the message client-side (`S.queue`) and drains it after the current response completes; messages that look like a mid-stream redirect ("wait", "actually", "no", "stop"...) get an extra 2.5s delay before sending, on the theory the user may still be typing a correction.
- Minimal inline Markdown renderer (```code fences```, `` `inline` ``, `**bold**`, `*italic*`, newlines → `<br>`) — no external Markdown/syntax-highlighting library.
- Panel drag-and-drop reordering, per-panel collapse, and sidebar width are all persisted to `localStorage` (`claudioui_lsb/rsb/lorder/rorder/collapsed`).
- Visual theme: dark "terminal/HUD" look — near-black background, cyan/magenta/teal glow accents, scanline overlay, monospace metrics.

The `MODELS` list near the top of the Python file (`claude-sonnet-4-6`, `claude-opus-4-5`, `claude-haiku-4-5-20251001`, `claude-sonnet-5`, `claude-fable-5`, `claude-opus-5`) is injected into the page as `window._MODELS_` at request time — that's the single place to add/remove entries in the model picker.

## claudioui_gui.py

A ~180-line Tkinter window, independent of the Python server's own logic — it does not import `claudioui_server.py`, it only launches it as a subprocess and polls `http://127.0.0.1:8765/api/state`. Buttons: **Launch** (spawn `claudioui_server.py` detached, poll for up to 12s), **Stop** (`POST /api/shutdown`), **Restart** (`POST /api/restart`), **Open Browser**. Despite the docstring name-checking the PS1 server, `SCRIPT` in this file points at `claudioui_server.py` — i.e. this particular GUI launches the *Python* server, not the one the `.bat`/`.vbs` launchers use.

## Divergence between implementations

`CLAUDE.md` describes `claudioui_server.ps1` as "a pure PowerShell port of the same server," but in practice the two files have diverged substantially — the PS1 version is the more actively evolved one:

- **Different visual design entirely.** Python: cyan/magenta neon terminal HUD. PowerShell: a purple/pink "synthwave" theme with Google Fonts (Poppins), gradient buttons, aurora background glows.
- **Extra endpoints PS1 has that Python doesn't:** `/api/history`, `/api/logs`, `/api/stop` (kill the in-flight `claude` process), `/api/settings`, `/api/plugins`, `/api/open-dir`.
- **Different session-tracking strategy.** Python parses `stream-json` output to read `session_id` directly from the `system/init` event. PowerShell instead runs plain `claude -p` (no `--output-format stream-json`), streams raw text/log lines, and *infers* the new session by diffing the set of `*.jsonl` filenames under `~/.claude/projects/**` before and after the first message in a conversation. This is why the PS1 comment explicitly warns: "never use `--continue` (it bleeds into terminal)."
- **Different conversation persistence.** Python persists conversations server-side to `~/.claudioui/conversations.json`. PowerShell's JS keeps the conversation list in browser `localStorage` (`getConvs`/`putConvs`) — a page's conversations don't survive switching browsers/profiles the way Python's server-side file does.
- **PS1-only features:** pasted-image support (base64 → temp PNG file, path appended to the prompt), a permission-mode selector whitelisted to `acceptEdits`/`bypassPermissions`/`plan` before being placed on the command line, `--thinking-budget`, agent spawning (`spawnAgent`/`listAgents`), and panels for browsing installed plugins/hooks/MCP servers and running slash commands (`runSlash`).
- **What actually runs when you double-click a launcher:** `Launch ClaudioUi.bat`/`.vbs` both start `claudioui_server.ps1`, not the Python server — despite CLAUDE.md calling the Python file "primary, actively maintained." If you intend to edit the UI/behavior a user will actually see from those launchers, edit the `.ps1` file.

## Editing the embedded UI

There's no build step — the HTML/CSS/JS is a literal string inside each server script and must be edited in place:

- Python: the `HTML = r"""..."""` string in [claudioui_server.py](claudioui_server.py).
- PowerShell: the `$HTML = @'...'@` here-string in [claudioui_server.ps1](claudioui_server.ps1).

[apply_style.py](apply_style.py) is a narrow one-shot tool: it locates the first `<style>...</style>` block in `claudioui_server.ps1` and replaces it with the contents of a `new_style_temp.txt` file placed alongside it (also prepending Google Fonts preconnect links). It doesn't touch the Python server and has no effect unless `new_style_temp.txt` exists.

## Data persisted to disk

| Path | Written by | Contents |
|---|---|---|
| `~/.claudioui/conversations.json` | Python server | All conversations + messages + session IDs |
| `~/.claudioui/config.json` | Python server | `model`, `max_tokens`, `system_prompt` |
| `claudioui.log` | PowerShell server | Rolling text log of requests/`claude` invocations (also kept in-memory, capped at 500 lines, served via `/api/logs`) |

The PowerShell server does not appear to persist conversations or config to disk at all — conversation state lives in the browser's `localStorage` and session IDs live only in the in-memory `$Script:Sessions` dictionary, so both are lost on server restart.

## Practical notes for future work here

- Port **8765** is hardcoded in both servers and all four launchers/GUI. Changing it means updating every file in the table above.
- Both servers assume `claude` is on `PATH` and spawn it with a hidden window (`CREATE_NO_WINDOW` / `CreateNoWindow`) so no console flashes when a message is sent.
- Because the two servers are functionally different products sharing a name and a port, changes to "ClaudioUI" should specify *which* server file is meant — a fix in one will not appear in the other.
