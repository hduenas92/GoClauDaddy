"""Central paths and settings — the one place that knows where things live on disk."""

import json
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765

DATA_DIR = Path.home() / ".goclaudaddy"
DB_PATH = DATA_DIR / "goclaudaddy.db"
LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
ATTACHMENTS_DIR = DATA_DIR / "attachments"

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"
INDEX_HTML = FRONTEND_DIR / "index.html"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB
ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
}
RESPONSE_TIMEOUT_SECONDS = 600  # 10 min — force-kill a hung `claude` subprocess

# Full set accepted by `claude --permission-mode` (verified against `claude --help`
# on the installed CLI version — do not add values without re-checking against
# a real --help output, and do not remove this verification step later).
# Env vars the org's CaaS-backed `claude` CLI setup requires to be set
# persistently (via `setx`, not a one-off `$env:` in a single terminal) —
# GoClaudaddy only ever checks these are *present*, never reads or logs the
# values. Auth itself is entirely the CLI's own responsibility.
REQUIRED_AUTH_ENV_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")

PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")

DEFAULT_WORKING_DIR = str(Path.home())

DEFAULT_MODEL = "claude-sonnet-5-5"
ASSESS_MODEL = "claude-haiku-4-5-20251001"
# Rates are $/million tokens (Anthropic retail pricing). context_window in tokens.
MODELS = [
    {"id": "claude-sonnet-5-5",         "label": "Sonnet 5.5", "description": "Smart and efficient — reliable for most tasks", "context_window": 1_000_000, "input_rate":  2.00, "output_rate": 10.00},
    {"id": "claude-opus-5-5",           "label": "Opus 5.5",   "description": "Highly capable — advanced reasoning and analysis", "context_window": 1_000_000, "input_rate":  4.00, "output_rate": 20.00},
    {"id": "claude-haiku-4-5-20251001", "label": "Haiku 4.5",  "description": "Fastest — quick tasks, high throughput", "context_window":   200_000, "input_rate":  1.00, "output_rate":  5.00},
]


def cli_default_model() -> str:
    """The catalog id the CLI's own ~/.claude/settings.json selects.

    Read on every call — including Path.home(), which is resolved per call and
    never cached — so a settings edit applies without a restart. Read-only: it
    never writes the file. Anything missing, unreadable, malformed, non-string,
    empty or not a known id/alias falls back to DEFAULT_MODEL.
    """
    try:
        settings = json.loads(
            (Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return DEFAULT_MODEL
    model = settings.get("model") if isinstance(settings, dict) else None
    if not isinstance(model, str) or not model:
        return DEFAULT_MODEL
    ids = [m["id"] for m in MODELS]
    if model in ids:
        return model
    lowered = model.lower()
    for model_id in ids:
        if lowered in model_id.lower():
            return model_id
    return DEFAULT_MODEL


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    ATTACHMENTS_DIR.mkdir(exist_ok=True)
