# Changelog

Real bugs found through actual coworker testing, in the order they were found. Kept as a reference for diagnosing the same class of issue quickly if it resurfaces, and as a record of what "test on real, diverse machines" actually caught that code review alone wouldn't have.

## Reliability fixes found via real-machine testing

### Crash on first launch: missing log directory
- **Symptom:** `FileNotFoundError` on `~/.claudioui/logs/app.log` on a genuinely fresh machine.
- **Root cause:** `RotatingFileHandler` opens a file but never creates its parent directory. Never surfaced on the dev machine because that directory already existed from earlier manual runs.
- **Fix:** `LOG_FILE.parent.mkdir(parents=True, exist_ok=True)` before creating the handler (`backend/app/logging_setup.py`).
- **Commit:** `8683ad7`

### Doomed dependency build on a too-new Python
- **Symptom:** `pip install` failed trying to compile `pydantic-core` from Rust source; the Rust/PyO3 toolchain itself then also failed because it didn't support the installed Python version either.
- **Root cause:** The launcher only checked "is *some* Python on PATH," not whether it was a version with prebuilt wheels available. A machine already had Python 3.14 installed (newer than anything tested), and `pydantic-core` had no wheel for it yet.
- **Fix:** Version-gate Python selection to a known-good range (3.10–3.13) via a new `:FindGoodPython` routine; prefer `py -3.13` over bare `python`; install 3.13 if nothing compatible is found. Added `--only-binary=:all:` to the dependency install as a second-layer failsafe — even if an incompatible interpreter ever slips through, this fails fast with a readable error instead of a multi-minute doomed compile. Also fixed a latent bug found while in there: venv creation can succeed while the pip install after it still fails, so a `.venv\.deps_ok` marker now tracks real completion instead of just "the folder exists" — a retry now always resumes cleanly instead of skipping into a different, more confusing error.
- **Commit:** `20206a5`

### winget not present on every machine
- **Symptom:** `'winget' is not recognized as an internal or external command`.
- **Root cause:** winget (App Installer) is an optional Windows component, not guaranteed present — the original plan wrongly assumed it "ships with Windows 11."
- **Fix:** Check for `winget` first; when absent, download the official python.org installer directly and run it silently as a per-user install (no admin needed).
- **Commit:** `803b3a2`

### Fallback installer hardcoded to x64
- **Symptom:** none reported yet — found via a proactive audit after the winget fix, not a coworker report.
- **Root cause:** The direct-download fallback always fetched the `amd64` installer, which would be silently wrong on an ARM64 Windows machine. Confirmed by reading the official `claude` CLI installer's own source, which does this arch check correctly.
- **Fix:** Detect `PROCESSOR_ARCHITECTURE`/`PROCESSOR_ARCHITEW6432` and pick `arm64` vs `amd64` accordingly; confirmed python.org publishes both.
- **Commit:** `52a9c87`

### Regression: the ARM64 fix itself broke the fallback URL for everyone
- **Symptom:** `curl: (22) The requested URL returned error: 404` on the very next real test.
- **Root cause:** `PYARCH` was set inside the same parenthesized `if/else` block it was read in, so bare `%PYARCH%` expanded at **parse time** to its pre-block (empty) value — producing a URL like `python-3.13.7-.exe`. This is the exact `%errorlevel%`-freeze class of cmd.exe bug already documented at the top of the launcher; the rule wasn't applied to a variable introduced two commits earlier.
- **Fix:** Use delayed expansion (`!PYARCH!`) instead of `%PYARCH%`.
- **Commit:** `9c90e71`
- **Lesson:** *Any* variable set and read within the same `( ... )` block in this file must use `!var!`, never `%var%` — this is now the second time this exact class of bug has appeared. Check this first whenever a batch-only change produces a wrong-looking string/URL.

### "claude CLI not found on PATH" during startup checks
- **Symptom:** App fails to start with a message about missing `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_BASE_URL`.
- **Root cause:** Not a bug — working as designed. A freshly-imaged machine hadn't run the org's CaaS onboarding to set those as *persistent* env vars.
- **Fix (of a real gap found alongside it):** The error message pointed at "your org's setup instructions" with nowhere in the repo actually documenting them. Added a "One-time setup" section to the top of `README.md` with the exact `setx` commands, and pointed the error message at it by name.
- **Commit:** `26378c7`

### "claude CLI not found on PATH" during an actual chat request (different from the above)
- **Symptom:** Server starts fine, startup checks pass, but every chat message fails with this exact error.
- **Root cause:** `shutil.which()` (used by the startup check) applies `PATHEXT` and correctly finds a `claude.cmd`/`.ps1` shim (e.g. from an npm-style install). `asyncio.create_subprocess_exec()` (used to actually spawn `claude` for a chat request) does **not** apply `PATHEXT` on Windows — so on a machine where `claude` only resolves to a shim, with no real `.exe` reachable anywhere on PATH, the startup check passes but every chat request fails.
- **Verified directly:** built an isolated PATH containing only a fake `claude.cmd`; confirmed `shutil.which` found it while `create_subprocess_exec` failed with the exact reported error; confirmed passing the `shutil.which`-resolved path fixes it. Also confirmed the regression test actually catches this by temporarily disabling the fix and watching it fail.
- **Fix:** Resolve `cmd[0]` via `shutil.which()` before spawning in `claude_cli.run()`.
- **Commit:** `6e1758c`

## General lessons for next time

- **Test on machines you don't control**, not just your own dev machine — every fix above was found this way, none by code review.
- **Two resolution mechanisms that "should" agree on Windows often don't**: `shutil.which()` vs `CreateProcess`/PATHEXT, `where` vs bare `python` on PATH, an OS feature "shipping with Windows 11" vs actually being present. Verify the specific mechanism actually used at runtime, not an assumption about the platform.
- **cmd.exe delayed-expansion (`!var!` vs `%var%`) bugs are the single most common source of "it worked when I tested it, broke on a real machine"** in this launcher — check this first for any batch-only regression.
- **A version/architecture/tool check should test the actual thing**, not just presence — "is Python installed" isn't the same question as "is a *compatible* Python installed."
