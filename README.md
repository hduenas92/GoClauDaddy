# GoClaudaddy

A simple chat window for talking to Claude — no terminal required.

## One-time setup (before your first run)

GoClaudaddy talks to Claude through your organization's own CaaS-provisioned API access — it never has its own login. Before running it for the first time, two environment variables need to be set **persistently** (so they're still there the next time you log in or restart — not just in one PowerShell window):

```
setx ANTHROPIC_AUTH_TOKEN "your-caas-token-here"
setx ANTHROPIC_BASE_URL "https://caas-gocode-prod.caas-prod.prod.onkatana.net"
```

Get your token the same way you already do for the `claude` CLI itself, following your organization's CaaS onboarding steps. After running `setx`, **close and reopen your terminal/File Explorer window** before launching GoClaudaddy — a variable set with `setx` isn't visible to windows that were already open.

If you skip this, GoClaudaddy will tell you exactly which variable is missing when it starts, rather than failing silently. The launcher itself does not read these variables; the app's startup check verifies that they are present.

GoClaudaddy keeps its data under your home folder at `~/.goclaudaddy` (on Windows, `C:\Users\<you>\.goclaudaddy`). On the first launch after an older install that used `~/.claudioui`, it copies existing data once if present. After that, conversations, logs, and attachments live under `.goclaudaddy`.

## Starting it up

Double-click **`Launch GoClaudaddy.bat`**. A window will open and, after a few seconds, your browser will open to the chat screen automatically.

There is also a `legacy\Launch ClaudioUi.bat`. **Do not use it** — it starts an older
PowerShell server, not this app.

The very first time you run it, it may take a minute or two longer while it sets a few things up — that only happens once. Every time after that, it starts in a few seconds.

**Leave the black window open** while you're using GoClaudaddy — closing it will stop the app. You can minimize it.

If something goes wrong on startup, the window will tell you what happened and wait for you to press a key before closing, so you can read the message. See **If something goes wrong** below.

## Sending your first message

Type in the box at the bottom and press **Enter** (or click **Send**). Claude's reply will appear above, streaming in as it's written.

- **Shift+Enter** adds a new line without sending.
- Press **↑** in an empty message box to recall your previous sent message; use **↓** to move forward again.
- Click **Stop** while Claude is replying if you want to cut it off early.
- Attach a file by dragging it into the message box, pasting an image, or clicking the 📎 button.

## Conversations

The left sidebar lists your conversations. Click **＋ New Chat** to start a fresh one — Claude won't remember anything from other conversations, which is useful when you want a clean slate.

- Click a conversation to switch to it — your full history is saved, so you can always come back to it later, even after closing and reopening the app.
- Hover over a conversation to rename (✎) or delete (✕) it.

## Projects

A **Project** points Claude at a specific folder on your computer and (optionally) a standing instruction that applies to every conversation inside it. Use one when you're working on something tied to a particular set of files — Claude will start in that folder by default instead of your general user folder.

Click **＋ New Project** in the sidebar, pick a folder, and give it a name. Every conversation you create while that project is selected will use that folder. Click **All conversations** to go back to seeing everything.

## Model & Permission mode

Open the **⚙ Settings** drawer (the gear button in the header, or **Ctrl+,**) and look under **This conversation**:

- **Model** — which version of Claude answers you. The default is a good general-purpose choice; the others trade off speed, cost, and capability.
- **Permission mode** — how cautious Claude is about taking actions (like editing files) on your computer:
  - **(default)** — no override; the `claude` CLI's normal permission behavior applies.
  - **Plan mode** — Claude plans and explains rather than making changes.
  - **Auto-approve edits** — Claude can make file edits without asking each time, but still checks before anything riskier.
  - **Bypass all prompts** — Claude won't ask before anything. Only use this if you're confident about what you're asking it to do.
  - **Full auto**, **Manual approval**, and **Never ask** are more advanced options — check with a technical teammate before using them if you're not sure what they do.

The same drawer also has **Extended thinking** and **Max output tokens**. If you're not sure, leave everything on its default.

## Attachments

Files you attach (images, text files, PDFs, etc.) are given to Claude for that message. They stay attached to that conversation, so Claude can still refer back to a file you shared earlier in the same conversation. The app stores them under `~/.goclaudaddy/attachments/<conversation_id>/`.

## If something goes wrong

- **The chat seems stuck** — click **Stop**, then try sending again.
- **The launcher window shows an error** — read the message; it's written in plain language and tells you what to do next (e.g. "close this window and run it again").
- **Something looks broken and you want to report it** — the app keeps a log file at:
  `~/.goclaudaddy/logs/app.log` (on Windows, `C:\Users\<you>\.goclaudaddy\logs\app.log`)
  Grab the last part of that file and send it along when you report the issue — it has the details needed to figure out what happened.
- **The app exits unexpectedly** — it will usually restart itself automatically. The watchdog allows up to 3 restarts in a rolling 5-minute window; if the server exits unexpectedly a fourth time in that window, the watchdog stops and points you at the log file rather than looping forever.

## What GoClaudaddy is *not*

Everything you type is sent to Claude through your organization's existing CaaS setup. GoClaudaddy has no separate account and does not store your conversation history anywhere except on your own machine. A Project folder is only the working directory Claude starts in — it is **not a sandbox**, and Claude may read or change files outside it. **Bypass all prompts** lets Claude act without asking, so use it only when you're confident about what you're asking it to do.
