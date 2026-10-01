"""Agent evaluation: Haiku assessment of a user message before it's sent."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import ASSESS_MODEL
from app.logging_setup import get_logger

router = APIRouter(prefix="/api/conversations", tags=["assess"])
log = get_logger("assess")

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_TIMEOUT = 8

_SYSTEM = (
    "You are a task risk assessor. The user will send a message they intend to give "
    "to an AI coding assistant. Analyze the message and respond with ONLY valid JSON "
    'in this exact format: {"level":"low","summary":"one sentence","concerns":["..."]} '
    "Level meanings — low: safe, routine; medium: potentially destructive or irreversible; "
    "high: clearly risky (mass deletions, privilege escalation, data wipes, credentials). "
    "Concerns list should be empty for low. No markdown, no text outside the JSON object."
)

_FALLBACK = {"level": "low", "summary": "Assessment unavailable.", "concerns": [], "timed_out": True}


class AssessBody(BaseModel):
    message: str
    conversation_id: str | None = None


@router.post("/assess")
async def assess(body: AssessBody):
    if not body.message.strip():
        return {"level": "low", "summary": "Empty message.", "concerns": []}

    resolved = shutil.which("claude") or "claude"
    cmd = [
        resolved, "-p",
        "--output-format", "json",
        "--model", ASSESS_MODEL,
        "--system-prompt", _SYSTEM,
        body.message[:2000],
    ]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
            creationflags=_CREATE_NO_WINDOW,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
    except Exception as exc:
        log.warning("assess failed: %s", exc)
        if proc:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            except Exception:
                log.warning("assess: failed to kill subprocess after assessment failure", exc_info=True)
        return _FALLBACK

    try:
        outer = json.loads(stdout)
        text = outer.get("result", stdout.decode("utf-8", errors="replace"))
        text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(text)
    except Exception as exc:
        log.warning("assess: could not parse LLM response: %s | raw=%r", exc, stdout[:200])
        return _FALLBACK
