Vendored web fonts (self-hosted so the app makes no runtime request to Google Fonts).
Families: DM Sans, Inter, JetBrains Mono, Silkscreen. All four are licensed under the SIL Open Font License 1.1 (https://openfontlicense.org).
Files: latin and latin-ext subsets as woff2, fetched once from the Google Fonts CDN (the same URLs the app used to load at runtime) on 2026-10-14.
Variable-weight files carry a weight range (for example 400-700); the @font-face rules are at the top of static/css/base.css.
