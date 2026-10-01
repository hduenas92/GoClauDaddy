# Trying out GoClaudaddy — a quick first run

You're one of the first people outside the build to actually use this. The point is to click around and tell me what's confusing — this isn't a formal test, and it should take about 10 minutes.

## Setup (2 min)

1. Double-click **`Launch GoClaudaddy.bat`**.
2. The first run may take a minute or two longer than usual — that's normal, it only happens once.
3. Your browser should open on its own. If a black window appears and stays open, that's expected — leave it open while you're using the app.

If anything goes wrong here, **stop and send a screenshot** — see "If something breaks" below.

## Things to try (8 min)

Work through these roughly in order — each one exercises a different part of the app:

1. **Ask it something simple.** Type a question in the message box and press Enter. Wait for the full reply.
2. **Ask a follow-up.** Reply to what it just said, referencing something specific from its answer — confirm it actually remembers the context.
3. **Start a second, separate conversation.** Click **＋ New Chat**. Ask it something unrelated. Switch back to your first conversation and confirm it's still there, unchanged.
4. **Attach a file.** Drag any small text file or screenshot into the message box, then ask a question about it (e.g. "what's in this file?" for a text file, or "what's in this image?" for a screenshot).
5. **Stop a response early.** Ask something that takes a little while to answer, then click **Stop** partway through. Confirm the app doesn't get stuck.
6. **(Optional) Try a Project.** Click **＋ New Project**, point it at a real folder on your computer, and ask it something about that folder.

## What I actually want to know

Don't just tell me "it worked" or "it didn't." I'd rather know:

- **What did you try to do, and what did you expect to happen?**
- **What actually happened instead**, if different — screenshot if you can.
- **Did anything feel confusing** even if it technically worked? (Wording, where a button was, what a setting meant.)
- **Did you get stuck anywhere** without knowing what to do next?

Small stuff counts. "The Send button felt too far from the text box" is as useful to me as "it crashed."

## If something breaks

- Read the message on screen first — it's meant to be understandable without technical knowledge, and usually tells you exactly what to do.
- If it doesn't make sense, or the app just seems stuck/broken: grab a screenshot and send it to me, along with a rough idea of what you were doing right before it happened.
- The app also keeps a log file at `~/.goclaudaddy/logs/app.log` (on Windows, `C:\Users\<you>\.goclaudaddy\logs\app.log`) — you don't need to read it, just know it exists in case I ask for it.

Thanks for being the first real test of GoClaudaddy outside my own machine.
