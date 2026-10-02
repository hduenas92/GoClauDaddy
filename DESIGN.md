# GoClaudaddy — Design System

**Created 2026-09-17.** This is the document `plans/goclaudaddy.md` commissioned long ago and which was never produced. Its absence is why design intent kept getting lost between sessions.

---

## Authority — read this before changing any visual code

Two documents govern design, and they answer **different** questions. Blurring that line is how this project drifted the first time, so the split is stated here deliberately:

| Question | Authority |
|---|---|
| What is the brand palette? What is a theme's *intent* and character? | **Stitch** — project `6250354989880053538`, "GoClaudaddy — The Lit Workbench". 20 design systems live there. Query with `mcp__stitch__list_design_systems`; it returns ~383KB, so parse with PowerShell `ConvertFrom-Json`, never read it raw |
| What does the build actually do, and where does it deliberately diverge? | **This file** |

**Neither is a superset of the other.** If you need a color's identity, Stitch is authoritative. If you need to know why the build differs from Stitch, this file is authoritative. When they conflict on something not listed under "Amendments" below, **Stitch wins and the build is wrong.**

There is no `PRODUCT.md`. Durable project memory lives at
`~/.claude/projects/c--Users-hduenas-LLMs-Claude-Projects/memory/project-goclaudaddy-design-system.md`
and points at both of the above.

---

## The token contract

`frontend/static/css/base.css` defines everything as **channel triplets** plus solid convenience colors. This is not stylistic — it is required.

```css
:root { --accent-rgb: 0 163 165; --accent: rgb(var(--accent-rgb)); }
[data-theme="x"] { --accent-rgb: 220 38 38; }              /* retints everything */
.thing { border: 1px solid rgba(var(--accent-rgb) / .15); } /* alpha composes */
```

**Why triplets and not hex.** The file held 218 `rgba()` calls against only 14 distinct base colors — 132 of them two teals at varying alpha. A hex token cannot express "accent at 15%", so with plain hex tokens a theme switch would have left 218 of 248 color values unchanged. Full derivation in `tools/token-map.md`.

### Two absolute rules

**1. Alpha is never allowed on a text color.** `color:` must always resolve to a solid, AA-passing token. Alpha remains correct for `background`, `border*`, `box-shadow`, `text-shadow`, `background-image`, `filter`.

> Measured before this rule existed: **13 of 49** rendered text elements failed WCAG AA, and *every* failure was an alpha-faded `color:` — worst was **1.71:1** on "No agents running". The underlying colors passed fine at full opacity. Fading them for text is what broke legibility. After: **0 of 48**.

**2. No color literal outside a token block.** `grep -E '#[0-9a-fA-F]{3,8}|rgba?\([0-9]'` on `base.css` must match only inside `:root` / `[data-theme]`. A literal in a component rule is invisible to every theme.

### Adding a theme

Exactly two edits. **Never touch component CSS.** If a theme needs a component rule changed, the token contract has a gap — fix the contract instead.

1. **One entry in the registry** — `window.GCA_THEMES` in `frontend/index.html`:
   ```js
   window.GCA_THEMES = [
     { id: "lit-workbench", label: "Lit Workbench" },
     { id: "grid-void",     label: "Grid Void OS" },   // <- added
   ];
   ```
2. **One `[data-theme="..."]` block** in `base.css` declaring only the `*-rgb` channel triplets. The solid convenience colors (`--accent`, `--text`, …) derive from them automatically.

Everything else follows on its own: the switcher appears (it is `hidden` while `GCA_THEMES.length <= 1`) and persistence works.

**Why the registry is a `window` global rather than an ES module.** The active theme must be applied to `<html>` *before first paint* or the page flashes the default theme. ES modules are deferred, so the apply happens in an inline `<script>` in `<head>` — and both that bootstrap and `settings_panel.js` need to read the same list. The global is the deliberate bridge between them, not an oversight. Treat `window.GCA_THEMES` as a real contract: changing its shape breaks both readers.

`:root` remains the fallback for an unset or unknown `data-theme`, so a corrupt `gca_theme` value still yields a fully styled page rather than an unstyled one.

### Code syntax coloring

`frontend/static/css/hljs-tokens.css` maps every `hljs` class to a token, so code coloring follows any theme with no per-theme files and no runtime stylesheet swap. The vendored `highlight-github-dark.min.css` is **no longer loaded** (still on disk, just unlinked — if it loads it will override these rules).

10 tokens cover 21 syntax roles, so roles deliberately share colors: keyword/operator/punctuation on `--accent-hi`; function/title/type on `--accent`; variable/attr/tag/name on `--text-secondary`; string/addition on `--success`; number/literal on `--cta`; built_in on `--warning`; comment/meta on `--text-muted`; deletion on `--error`. `emphasis`/`strong` are style-only (`color: inherit`) since they nest inside other roles rather than being roles themselves.

Every one of those clears AA against the code block's `--ground` background — dimmest is `--error` at **5.27:1**, comments at **5.86:1**.

---

## Amendments — where the build deliberately differs from Stitch

Each of these was a decision, not an accident. The standing rule is that **usability outranks design fidelity**: when a spec conflicts with readability or UX, usability wins and the spec gets amended rather than silently diverged from.

### 1. Shell font is JetBrains Mono, not DM Mono

Spec says DM Mono for shell output. The build uses **JetBrains Mono**: taller x-height, disambiguated `0/O` and `1/l/I`, ligature support — materially better for dense tracebacks and diffs. DM Sans is retained for UI chrome exactly as specced.

- `--font-ui: 'DM Sans'` — nav, labels, metadata, buttons, sidebar titles, placeholders
- `--font-shell: 'JetBrains Mono'` — CLI output, message bodies, code, composer input, metric values

Fira Code was listed as a shell fallback in the Stitch export's Tailwind config. It has been **removed everywhere** (`--font-shell`, the Google Fonts request, `ui/terminal.js`): it never rendered, because JetBrains Mono always loads first and shadows it. Measured proof — its rendered width was byte-for-byte identical to a font family that does not exist. It also left one face permanently in `loading`, which was independently reproduced by adding it back. `Cascadia Code` is kept as the fallback instead: it ships with Windows, so it works with no network at all.

**Requested weights must equal rendered weights.** A weight used in CSS but absent from the Google Fonts URL is faux-bolded by the browser with no error of any kind — DM Sans 700 was in that state across 15 CSS rules plus every markdown `<strong>`. The request is now exactly `DM Sans 400;600;700` and `JetBrains Mono 0,400;0,600;0,700;1,400` (the italic is for hljs comment tokens). `tools/font-check.mjs` reads the families straight out of the page's own URL and fails on either direction of drift; both failure modes are mutation-verified.

### 2. Muted text is `#8A8AAA`, not `#52526A`

The spec assigns `#52526A` to "disabled states, faint annotations." Measured on Surface Ink `#18181F` that is **2.35:1** — far below the 4.5:1 AA minimum. Disabled controls are exempt from contrast minimums; *annotations* are not.

`#8A8AAA` measures **5.2:1** on surface and **4.8:1** on the teal-tinted active conversation row, keeping the spec's violet-leaning neutral character.

> An intermediate value, `#8080A0`, passed on surface at 4.6:1 but measured **4.19:1** on the tinted active row. Both figures came from the harness compositing real rendered backgrounds — which is why the token is verified against every surface it appears on, not just the common one.

### 3. Two accent steps, not one

Spec defines a single Signal Teal `#00A3A5`. The build uses a base/emphasis pair — `--accent` (`#00A3A5`) and `--accent-hi` (`#2dd4bf`) — because the original CSS used two teals **132 times** as a deliberate hierarchy. Collapsing them would flatten the design.

### 4. Ambient effects are intentional

The honeycomb canvas and scanline overlay were removed on 2026-10-02 (scrapped for the new design). The ambient gradient appears in no Stitch spec. **Kept by explicit user decision.**

- The ambient gradient carries `pointer-events: none` while covering the viewport — verified empirically, not assumed from reading CSS

Note the spec's ban on "neon outer glows" and "card shadows" is otherwise still honored in component styling; the ambient gradient is the scoped exception.

### 5. Text floor is 11px

45 declarations sat at 7–10px. Raised to a hard **11px** floor by user decision. Small text compounds with any contrast weakness, and 9px metric labels were among the worst AA failures.

---

## Verified layout facts

- **Assistant message:** transparent background, `2px` `#00A3A5` left border, `12px` inset, **no shadow, no card**. Exactly per spec — a plan task once proposed adding a card background and was rejected.
- **Prose measure capped at `70ch`** — on the *text children* of `.text-block`, never the container. Markdown renders into `.text-block` via `innerHTML`, so code blocks and tables are its children; capping the container capped those too. Measured before the fix: `pre 546px` vs `prose 546px`. After: `pre 1266px` at 1440px, `2274px` at 2560px.
- Was **162 characters** per line at 1440px and **291** at 2560px before the cap.

---

## How to verify a visual change

```powershell
cd C:\Users\hduenas\LLMs\Claude\Projects\GoClaudaddy\app
node tools\ui-check.mjs              # contrast, measure, code width, console, drift
node tools\ui-check.mjs --width 2560
node tools\ui-check.mjs --reduced    # prefers-reduced-motion
node tools\ui-check.mjs --baseline   # re-baseline after an intended change
node tools\focus-check.mjs           # composer autofocus, tour keeps focus
node tools\font-check.mjs            # webfonts actually render; weights match
node tools\resync-check.mjs          # reconnect resync: busy notice + away marker
```

`tools/ui-check.mjs` measures rather than asserts: it composites actual rendered backgrounds, computes WCAG ratios from the pixels a user sees, and **auto-discovers every element rendering its own text** rather than checking a curated selector list.

> That last point is load-bearing. The first version probed three hand-picked selectors and reported "all contrast passes" — it had measured only synthetic message bubbles and never touched the left rail or header. A curated probe list cannot support a coverage claim.

`tools/font-check.mjs` exists because a `font-family` that resolves correctly in `getComputedStyle` proves nothing about which glyphs were drawn — a failed webfont falls back silently and the computed value is identical either way. The only reliable signal is rendered width compared against a family that deliberately does not exist.

> Same lesson in a different costume: **reading the declaration is not observing the result.** A screenshot is not much better — an 11px uppercase letter-spaced label in a grid-aligned layout reads as monospace to the eye whatever font is actually drawing it. That misread is what prompted this tool.

**Do not trust a visual change you have not measured.** Reading the CSS is not verification.
