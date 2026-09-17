# Token contract + migration mapping for `base.css`

**Authored by Opus 2026-09-16. Stage 1 tasks 1-1 and 1-2 of `plans/goclaudaddy-v1-plan.md`.**

This is the authoritative mapping. Apply it mechanically — do not invent tokens, do not
re-decide a mapping, do not "improve" a value. If any value in `base.css` is not covered by a
rule below, **stop and report it** rather than guessing.

---

## Why channel triplets

`base.css` holds 218 `rgba()`/`rgb()` calls but only **14 distinct base triplets**. 132 of them are
two teals at varying alpha. A plain hex token cannot express "accent at 15%", so a theme switch
would leave 218 of 248 color values unchanged. Channel triplets fix that:

```css
:root { --accent-rgb: 0 163 165; }
.thing { border: 1px solid rgba(var(--accent-rgb) / .15); }   /* composes */
[data-theme="other"] { --accent-rgb: 220 38 38; }             /* one line retints everything */
```

Space-separated triplet + `/ alpha` is the modern `rgb()`/`rgba()` syntax and is supported in
every browser this app targets (Chromium via the local server).

---

## Rule 1 — alpha is NEVER allowed on text

Measured with `tools/ui-check.mjs`: **13 of 49** rendered text elements fail WCAG AA, and every
single failure is an alpha-faded `color:`. The base colors pass at full opacity; fading them for
text is what breaks legibility (worst: `1.71:1` on "No agents running").

- **`color:` must always resolve to a solid, AA-passing token.** No `/ alpha` on a text color, ever.
- Secondary and muted text get their **own solid tokens** — they are not the primary color faded.
- Alpha remains correct and expected for `background`, `border*`, `box-shadow`, `text-shadow`,
  `background-image` and `filter`.

---

## Rule 2 — the token contract

Replace the existing `:root` block with exactly this. Keep the old token names as aliases where
listed so nothing breaks mid-migration.

```css
:root {
  /* ---- channel triplets: the themeable surface ---- */
  --ground-rgb:        12 12 15;      /* Void Ground   #0C0C0F */
  --panel-rgb:         17 17 24;      /* Panel Night   #111118 */
  --surface-rgb:       24 24 31;      /* Surface Ink   #18181F */
  --hover-rgb:         31 31 42;      /* Hover Layer   #1F1F2A */
  --border-quiet-rgb:  39 39 58;      /* Border Quiet  #27273A */
  --border-strong-rgb: 60 60 86;      /* Border Strong #3C3C56 */

  --text-primary-rgb:   240 240 248;  /* #F0F0F8  17.2:1 on ground */
  --text-secondary-rgb: 148 148 172;  /* #9494AC   5.9:1 on surface */
  --text-muted-rgb:     128 128 160;  /* #8080A0   4.6:1 on surface  — see Rule 3 */

  --accent-rgb:        0 163 165;     /* Signal Teal   #00A3A5  5.65:1 on surface */
  --accent-hi-rgb:     45 212 191;    /* brighter accent step — see Rule 4 */
  --cta-rgb:           240 125 40;    /* Act Orange    #F07D28 */
  --working-rgb:       255 207 23;    /* Working Yellow #FFCF17 */
  --success-rgb:       108 184 40;    /* Settled Green #6CB828 */
  --warning-rgb:       229 161 63;    /* Halted Amber  #E5A13F */
  --error-rgb:         226 86 75;     /* Fault Red     #E2564B */
  --shadow-rgb:        0 0 0;

  /* ---- solid convenience colors (use these for `color:`) ---- */
  --ground:        rgb(var(--ground-rgb));
  --panel:         rgb(var(--panel-rgb));
  --surface:       rgb(var(--surface-rgb));
  --hover:         rgb(var(--hover-rgb));
  --border-quiet:  rgb(var(--border-quiet-rgb));
  --border-strong: rgb(var(--border-strong-rgb));
  --text:          rgb(var(--text-primary-rgb));
  --text-secondary:rgb(var(--text-secondary-rgb));
  --text-muted:    rgb(var(--text-muted-rgb));
  --accent:        rgb(var(--accent-rgb));
  --accent-hi:     rgb(var(--accent-hi-rgb));
  --cta:           rgb(var(--cta-rgb));
  --working:       rgb(var(--working-rgb));
  --success:       rgb(var(--success-rgb));
  --warning:       rgb(var(--warning-rgb));
  --error:         rgb(var(--error-rgb));

  /* ---- type ---- */
  --font-ui:    'DM Sans', 'Segoe UI', system-ui, sans-serif;
  --font-shell: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;

  /* ---- 4px spacing grid (spec §5) ---- */
  --space-1: 4px;  --space-2: 8px;  --space-3: 12px; --space-4: 16px;
  --space-6: 24px; --space-8: 32px; --space-12: 48px;
  --radius:    4px;   /* spec: <=4px, no pills for primary actions */
  --radius-lg: 8px;

  /* ---- back-compat aliases: keep until the migration is fully applied ---- */
  --bg:     var(--ground);
  --bg2:    var(--panel);
  --surf:   var(--surface);
  --elev:   var(--hover);
  --teal:   var(--accent);
  --teal-b: var(--accent-hi);
  --text2:  var(--text-secondary);
  --border: rgba(var(--accent-rgb) / .45);
  --amber:  var(--working);
  --rose:   var(--error);
  --emerald:var(--success);
}
```

---

## Rule 3 — `--text-muted` deviates from the Stitch spec deliberately

The spec assigns `#52526A` to "disabled states, faint annotations." Measured on Surface Ink
`#18181F` that is **2.35:1** — well below the 4.5:1 AA minimum. Disabled controls are exempt from
contrast minimums; "faint annotations" are not.

Per the standing rule that **usability outranks design fidelity**, `--text-muted` is `#8080A0`
(**≈4.59:1**), which keeps the spec's violet-leaning neutral character while passing AA. The Stitch
`designMd` gets amended to match in task 1-13. `#52526A` may still be used for genuinely disabled
controls via a separate `--text-disabled` token if one turns out to be needed.

---

## Rule 4 — two accent steps, not one

The spec defines a single Signal Teal. The build uses two teals — `#14b8a6` (70×) and `#2dd4bf`
(62×) — as a base/emphasis pair, and collapsing them would flatten the design. So the contract
keeps `--accent-rgb` (the spec's `#00A3A5`) and `--accent-hi-rgb` (the brighter step). Recorded as
an amendment in task 1-13.

---

## Rule 5 — the substitution table

Apply in order. `A` means "keep the existing alpha value exactly as written."
Whitespace inside `rgba(...)` varies in the source — match tolerantly, emit the canonical form.

### 5a. rgba/rgb → triplet composition

| Find (any spacing) | Replace with |
|---|---|
| `rgba(20, 184, 166, A)` · `rgb(20, 184, 166)` | `rgba(var(--accent-rgb) / A)` · `var(--accent)` |
| `rgba(45, 212, 191, A)` · `rgb(45, 212, 191)` | `rgba(var(--accent-hi-rgb) / A)` · `var(--accent-hi)` |
| `rgba(0, 0, 0, A)` | `rgba(var(--shadow-rgb) / A)` |
| `rgba(251, 191, 36, A)` | `rgba(var(--working-rgb) / A)` |
| `rgba(148, 163, 184, A)` | `rgba(var(--text-secondary-rgb) / A)` — **but see 5c if the property is `color`** |
| `rgba(244, 63, 94, A)` | `rgba(var(--error-rgb) / A)` |
| `rgba(167, 139, 250, A)` · `rgba(139, 92, 246, A)` | `rgba(var(--accent-hi-rgb) / A)` — purple is spec-banned; fold into accent |
| `rgba(15, 15, 23, A)` | `rgba(var(--panel-rgb) / A)` |
| `rgba(52, 211, 153, A)` | `rgba(var(--success-rgb) / A)` |
| `rgba(9, 9, 13, A)` | `rgba(var(--ground-rgb) / A)` |
| `rgba(11, 11, 20, A)` | `rgba(var(--ground-rgb) / A)` |
| `rgba(56, 189, 248, A)` | `rgba(var(--accent-hi-rgb) / A)` |
| `rgba(5, 5, 8, A)` | `rgba(var(--ground-rgb) / A)` |

### 5b. hex → token

| Find | Replace |
|---|---|
| `#09090d` | `var(--ground)` |
| `#0f0f17` | `var(--panel)` |
| `#141320` | `var(--surface)` |
| `#1a192a` | `var(--hover)` |
| `#050508` | `var(--ground)` |
| `#14b8a6` | `var(--accent)` |
| `#2dd4bf` | `var(--accent-hi)` |
| `#f8fafc` | `var(--text)` |
| `#94a3b8` | `var(--text-secondary)` |
| `#fbbf24` | `var(--working)` |
| `#f43f5e` · `#fb7185` | `var(--error)` |
| `#34d399` | `var(--success)` |
| `#a78bfa` | `var(--accent-hi)` |
| `#38bdf8` | `var(--accent-hi)` |
| `#fde68a` | `var(--working)` |
| `#fff` · `#ffffff` | `var(--text)` — spec bans pure white |

### 5c. `color:` declarations — strip the alpha (Rule 1)

Any `color:` whose value is `rgba(...)` becomes the **solid** token. Do not preserve alpha.

| Find | Replace |
|---|---|
| `color: rgba(148, 163, 184, ≤0.55)` | `color: var(--text-muted)` |
| `color: rgba(148, 163, 184, >0.55)` | `color: var(--text-secondary)` |
| `color: rgba(45, 212, 191, any)` | `color: var(--accent-hi)` |
| `color: rgba(20, 184, 166, any)` | `color: var(--accent)` |
| `color: rgba(<anything else>, any)` | solid token for that triplet per 5a/5b |

### 5d. font-size floor — 11px

45 declarations sit below the floor. Raise each to `11px`:

| Find | Replace |
|---|---|
| `font-size: 7px` · `8px` · `9px` · `10px` | `font-size: 11px` |

Leave 11px and above untouched. This is a density change in the right sidebar — the harness
checks for overflow and layout shift afterwards.

---

## Constraints while applying

- **Touch only `frontend/static/css/base.css`.** Nothing else in this task.
- **Do not reorder, reformat, reindent, or reflow.** One value swapped per site, nothing else.
- **Do not delete or merge any rule**, including the `box-shadow` and `text-shadow` declarations.
  They are being tokenized, not removed — that was an explicit user decision (D3).
- **Do not change any alpha value** except where Rule 5c strips it from a `color:`.
- Anything not covered above: **leave it and report it.** A reported unknown is cheap; a guessed
  mapping is expensive.

## Acceptance

```powershell
# no raw color literal may remain outside :root / [data-theme] blocks
cd C:\Users\hduenas\LLMs\Claude\Projects\ClaudioUI
node tools\ui-check.mjs        # contrast sweep must show 0 below AA; console clean
```

- `grep -E '#[0-9a-fA-F]{3,8}|rgba?\([0-9]'` on `base.css` → matches only inside the `:root` block
- `node tools/ui-check.mjs` → **0 of N text elements below AA** (was 13 of 49)
- No new console errors, no layout shift beyond the intended 11px density change
