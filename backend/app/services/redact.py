import os
import re


_SECRET_ENV_VARS = (
    "ANTHROPIC_AUTH_TOKEN",
    "GOCODE_API_TOKEN",
    "ANTHROPIC_API_KEY",
)
_SK_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
_BEARER_SECRET_RE = re.compile(r"(?i)(bearer )\S+")


def redact_secrets(line: str) -> str:
    """Redact credential-looking substrings from a CLI stderr line.

    Environment values are read on every call.  Values shorter than eight
    characters are skipped because replacing them would risk mangling normal
    diagnostic text.
    """
    redacted = line
    env_secrets = {v for v in map(os.environ.get, _SECRET_ENV_VARS) if v and len(v) >= 8}
    # Longest first so an overlapping shorter value cannot leave part of a longer secret in the line.
    for value in sorted(env_secrets, key=len, reverse=True):
        redacted = redacted.replace(value, "[REDACTED]")

    redacted = _SK_SECRET_RE.sub("[REDACTED]", redacted)
    # Match "bearer " case-insensitively but preserve its original spelling.
    redacted = _BEARER_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    return redacted
