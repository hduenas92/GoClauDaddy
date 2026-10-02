"""Central paths and settings — the one place that knows where things live on disk."""

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
# ClaudioUI only ever checks these are *present*, never reads or logs the
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


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    ATTACHMENTS_DIR.mkdir(exist_ok=True)
