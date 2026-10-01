"""5-2 — can the auth token reach a log, the database, or a response body?

Measured answer: no, and for a stronger reason than careful handling. **The
backend never holds the value.** There is exactly ONE environment read in all
of backend/app:

    # startup_check.py:46
    missing = [name for name in REQUIRED_AUTH_ENV_VARS if not os.environ.get(name)]

That is a presence test whose result is a list of NAMES. `claude_cli` starts
the CLI with `asyncio.create_subprocess_exec` and an env built as
`{**os.environ, **build_env(...)}` — the documented token controls must be
merged in because CLI 2.1.286 rejects --max-tokens/--thinking. The credential
still reaches the child by inheriting the parent environment; it is never read
into a named variable, logged, or serialised. A scan of every file under the
data directory found no credential value in the database, the logs, or any
export.

ONE FALSE-POSITIVE CLASS, recorded so the next sweep does not re-raise it: a
regex for /token/ matches 96 lines in backend/app, and almost every one is
`input_tokens`, `output_tokens`, `max_tokens`, `cache_read_tokens` — billing
counters. Reporting 96 hits would have made a clean result look alarming.

WHAT THESE TESTS PIN. Not "no secret leaked today" — a test cannot know that.
They pin the two properties a future change would break: that the startup
failure path names variables rather than values, and that the environment is
still only ever tested for presence.
"""
import ast
import os
import pathlib
import re

import pytest

from app import startup_check
from app.config import REQUIRED_AUTH_ENV_VARS
from app.services import claude_cli

APP_DIR = pathlib.Path(startup_check.__file__).resolve().parent

# `input_tokens` and friends are usage counters, not credentials. Excluded by
# name rather than by weakening the pattern, so a genuinely new `*_token`
# identifier still trips the sweep.
BILLING = re.compile(
    r'\b(?:input|output|max|cache_read|cache_creation|cache_read_input|'
    r'cache_creation_input|thinking)_tokens\b|\btokens?\b(?=[ _](?:in|out|used))',
    re.IGNORECASE)
CREDENTIAL = re.compile(
    r'\b\w*(?:auth_token|api_?key|secret|password|credential|bearer)\w*\b', re.IGNORECASE)
LOG_CALL = re.compile(r'\b(?:log|logger|logging)\.\w+\(|(?<![\w.])print\(')


def _py_files():
    return sorted(APP_DIR.rglob("*.py"))


def test_startup_failure_names_the_variable_never_its_value(monkeypatch):
    """The one place a missing credential produces a user-facing message.

    A message that helpfully echoed what it found would put the token in a
    console, a screenshot, and a bug report. So: the NAME must be present (or
    the message is useless) and no VALUE may be.
    """
    var = REQUIRED_AUTH_ENV_VARS[0]
    sentinel = "sk-THIS-IS-THE-SECRET-VALUE-0123456789"

    # One var present with a secret-looking value, the other missing, so the
    # failure path runs while a real value is sitting in the environment next
    # to it — the exact situation where an over-helpful message leaks.
    monkeypatch.setenv(var, sentinel)
    for other in REQUIRED_AUTH_ENV_VARS[1:]:
        monkeypatch.delenv(other, raising=False)

    with pytest.raises(startup_check.StartupCheckError) as exc:
        startup_check._check_auth_env_vars()

    msg = str(exc.value)
    assert REQUIRED_AUTH_ENV_VARS[1] in msg, "the message must name the missing variable"
    assert sentinel not in msg, "a credential VALUE reached the startup error message"


def test_environment_is_only_ever_tested_for_presence():
    """No os.environ / os.getenv result is bound to a name in backend/app.

    This is the invariant, not the count: the moment a value is assigned, it
    can be logged, embedded in an exception, or returned. A presence test
    cannot be. Checked with `ast` so that formatting cannot fool it.
    """
    reads, bound = [], []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        for node in ast.walk(tree):
            is_read = (
                (isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {"get", "getenv"}
                 and "environ" in ast.dump(node.func.value) + node.func.attr)
                or (isinstance(node, ast.Subscript)
                    and "environ" in ast.dump(node.value))
            )
            if not is_read:
                continue
            where = f"{path.name}:{node.lineno}"
            reads.append(where)
            # Bound = the value itself becomes a name or part of a string.
            p = parents.get(node)
            if isinstance(p, (ast.Assign, ast.AnnAssign, ast.NamedExpr,
                              ast.FormattedValue, ast.JoinedStr, ast.Return)):
                bound.append(where)

    # NON-EMPTY-SET GUARD. "nothing binds an env value" is vacuously true of a
    # codebase with no env reads at all — which is also what a renamed module
    # or a failed parse looks like from here.
    assert reads, (
        "no os.environ/os.getenv read found anywhere in backend/app — this "
        "test would pass having checked nothing. If the code moved, move the test."
    )
    assert not bound, (
        f"an environment value is bound to a name or interpolated into a "
        f"string at {bound}. Presence tests cannot leak; values can."
    )


def test_no_log_or_print_mentions_a_credential_identifier():
    """Sweep every logging call for a credential-shaped name.

    Usage counters (`input_tokens` and friends) are stripped first — they are
    the false-positive class that makes this sweep look alarming when it is
    clean. A new `*_api_key` or `*_auth_token` in a log line still trips it.
    """
    offenders, swept = [], 0
    for path in _py_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not LOG_CALL.search(line):
                continue
            swept += 1
            if CREDENTIAL.search(BILLING.sub("", line)):
                offenders.append(f"{path.name}:{i}  {line.strip()[:100]}")

    assert swept, "no logging calls found at all — the sweep measured nothing"
    assert not offenders, (
        f"a logging call references a credential-shaped identifier:\n"
        + "\n".join(offenders)
    )


@pytest.mark.asyncio
async def test_subprocess_env_is_parent_environment_plus_only_token_overrides(monkeypatch):
    """run() hands the child the parent env plus ONLY the token-control overrides.

    The previous version of this test asserted the spawn passed no `env=` at all,
    because inheriting keeps every credential value out of Python-visible memory.
    That invariant is no longer possible: CLI 2.1.286 rejects
    `--max-tokens`/`--thinking`, so the two documented controls
    (CLAUDE_CODE_MAX_OUTPUT_TOKENS, MAX_THINKING_TOKENS) must be merged into
    the child environment. This pins what still matters: the credential is
    still inherited from the parent environment, and the merge adds ONLY the
    overrides build_env() returns — an arbitrary extra key at the spawn site
    would still trip this test.
    """
    sentinel = "sk-THIS-IS-THE-SECRET-VALUE-0123456789"
    monkeypatch.setenv(REQUIRED_AUTH_ENV_VARS[0], sentinel)
    monkeypatch.delenv("MAX_THINKING_TOKENS", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", raising=False)

    captured: dict = {}

    class _EmptyStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _FakeProc:
        returncode = 0
        stdin = None
        stderr = None

        def __init__(self):
            self.stdout = _EmptyStream()

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return _FakeProc()

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)

    events = [
        ev async for ev in claude_cli.run(
            prompt="hi", model="m", cwd=".", thinking_budget=0, max_tokens=4096
        )
    ]
    assert events[-1] == {"type": "done"}

    env = captured["env"]
    # The credential still reaches the child: inherited, not injected by name.
    assert env[REQUIRED_AUTH_ENV_VARS[0]] == sentinel
    # Exactly the documented overrides were added; nothing else.
    extra = set(env) - set(os.environ)
    assert extra == {"MAX_THINKING_TOKENS", "CLAUDE_CODE_MAX_OUTPUT_TOKENS"}, extra
    assert env["MAX_THINKING_TOKENS"] == "0"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
