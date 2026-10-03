"""Seed a throwaway staging DB for a GoClaudaddy clone.

Creates <home>/.goclaudaddy/goclaudaddy.db by running the app's own numbered
migrations (never a hand-written schema), then inserts a small sample data set
(a few conversations with messages) so the UI has something to render.

The caller owns <home>: this script sets USERPROFILE and HOME to it BEFORE
importing the app, so app/config.py DATA_DIR = Path.home() / ".goclaudaddy"
resolves inside the clone. It starts no server — the clone runner owns that.

Usage:  python tools\\seed-staging.py --home <dir>
Exit:   0 = seeded (one JSON line on stdout), 3 = refused (nothing written)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"


def _comparable(path: str) -> str:
    """Case-insensitive, normalized form used only for the safety comparison."""
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _refuse(reason: str) -> int:
    print(f"refusing: {reason}")
    return 3


# (conversation name, [(role, content), ...])
_SAMPLE = [
    (
        "Staging smoke: backend overview",
        [
            ("user", "What does the GoClaudaddy backend expose?"),
            (
                "assistant",
                "It serves the frontend at / and a REST API under /api: config, "
                "conversations, projects, flow templates, server stats, and search.",
            ),
        ],
    ),
    (
        "Staging smoke: message rendering",
        [
            ("user", "Show me a markdown reply with a list."),
            (
                "assistant",
                "Here is a list to render:\n\n1. Headings and paragraphs\n"
                "2. Fenced code blocks\n3. Inline `code` and **bold** text",
            ),
            ("user", "Thanks."),
        ],
    ),
    (
        "Staging smoke: empty-ish short chat",
        [
            ("user", "Ping"),
            ("assistant", "Pong — this conversation exists to give the sidebar more than one row."),
        ],
    ),
]


def _seed(conversations_service) -> int:
    rows = 0
    for name, messages in _SAMPLE:
        conversation = conversations_service.create_conversation(name=name)
        rows += 1
        for role, content in messages:
            conversations_service.add_message(conversation.id, role, content)
            rows += 1
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="clone home dir; the DB lands in <home>\\.goclaudaddy")
    args = parser.parse_args(argv)

    if not args.home:
        return _refuse("--home is required (pass the clone's temp home dir)")

    home = os.path.abspath(os.path.normpath(args.home))
    # Refuse this process's own profile folder even when GCA_REAL_HOME is unset:
    # seeding there would write sample rows into the live app's database.
    own_home = os.path.expanduser("~")
    if _comparable(home) == _comparable(own_home):
        return _refuse(f"--home '{home}' is this user's real profile folder")
    real_home = os.environ.get("GCA_REAL_HOME")
    if real_home and _comparable(home) == _comparable(real_home):
        return _refuse(
            f"--home '{home}' is the real profile folder (GCA_REAL_HOME='{real_home}')"
        )

    # Point Path.home() at the clone BEFORE importing the app (app/config.py:8).
    Path(home).mkdir(parents=True, exist_ok=True)
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    from app.config import DB_PATH, ensure_dirs
    from app.db.migrations import apply_migrations
    from app.services import conversations_service

    ensure_dirs()
    apply_migrations()
    rows = _seed(conversations_service)

    print(json.dumps({"db": str(DB_PATH), "rows": rows}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
