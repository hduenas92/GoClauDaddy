# ClaudioUI

A simple chat window for talking to Claude — no terminal required.

## Starting it up

Double-click **`Launch ClaudioUi.bat`**. A window will open and, after a few seconds, your browser will open to the chat screen automatically.

The very first time you run it, it may take a minute or two longer while it sets a few things up — that only happens once. Every time after that, it starts in a few seconds.

**Leave the black window open** while you're using ClaudioUI — closing it will stop the app. You can minimize it.

If something goes wrong on startup, the window will tell you what happened and wait for you to press a key before closing, so you can read the message. See **If something goes wrong** below.

## Sending your first message

Type in the box at the bottom and press **Enter** (or click **Send**). Claude's reply will appear above, streaming in as it's written.

- **Shift+Enter** adds a new line without sending.
- Click **Stop** while Claude is replying if you want to cut it off early.
- Attach a file by dragging it into the message box, pasting an image, or clicking the 📎 button.

## Conversations

The left sidebar lists your conversations. Click **+ New Conversation** to start a fresh one — Claude won't remember anything from other conversations, which is useful when you want a clean slate.

- Click a conversation to switch to it — your full history is saved, so you can always come back to it later, even after closing and reopening the app.
- Hover over a conversation to rename (✎) or delete (✕) it.

## Projects

A **Project** points Claude at a specific folder on your computer and (optionally) a standing instruction that applies to every conversation inside it. Use one when you're working on something tied to a particular set of files — Claude will look in that folder by default instead of your general user folder.

Click **+ New Project** in the sidebar, pick a folder, and give it a name. Every conversation you create while that project is selected will use that folder. Click **All conversations** to go back to seeing everything.

## Model & Permission mode

At the top of the chat, you can choose:

- **Model** — which version of Claude answers you. The default is a good general-purpose choice; the others trade off speed, cost, and capability.
- **Permission mode** — controls how cautious Claude is about taking actions (like editing files) on your computer:
  - **(default)** — Claude asks before doing anything that changes files.
  - **plan** — Claude only plans and explains, and won't make any changes at all.
  - **acceptEdits** — Claude can make file edits without asking each time, but still checks before anything riskier.
  - **bypassPermissions** — Claude won't ask before anything. Only use this if you're confident about what you're asking it to do.
  - A couple of other options appear in the dropdown (`auto`, `manual`, `dontAsk`) for more advanced use — check with a technical teammate before using those if you're not sure what they do.

If you're not sure, leave both on their defaults.

## Attachments

Files you attach (images, text files, PDFs, etc.) are given to Claude for that message. They stay attached to that conversation, so Claude can still refer back to a file you shared earlier in the same conversation.

## If something goes wrong

- **The chat seems stuck** — click **Stop**, then try sending again.
- **The launcher window shows an error** — read the message; it's written in plain language and tells you what to do next (e.g. "close this window and run it again").
- **Something looks broken and you want to report it** — the app keeps a log file at:
  `C:\Users\<you>\.claudioui\logs\app.log`
  Grab the last part of that file and send it along when you report the issue — it has the details needed to figure out what happened.
- **The app crashes** — it will usually restart itself automatically. If it fails to start three times in a row, the window will say so and stop trying, rather than looping forever.

## What ClaudioUI is *not*

Everything you type stays on your own computer and goes only to Claude through your organization's existing setup — there's no separate account, no cloud storage of your conversations beyond your own machine, and nothing here can access anything outside the folder you've pointed it at.
