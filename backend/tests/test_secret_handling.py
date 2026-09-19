"""5-2 — can the auth token reach a log, the database, or a response body?

Measured answer: no, and for a stronger reason than careful handling. **The
backend never holds the value.** There is exactly ONE environment read in all
of backend/app:

    # startup_check.py:46
    missing = [name for name in REQUIRED_AUTH_ENV_VARS if not os.environ.get(name)]

That is a presence test whose result is a list of NAMES. `claude_cli` starts
the CLI with `asyncio.create_subprocess_exec` and no explicit `env=`, so the
child inherits the environment directly and the value never passes through a
Python variable the server can log, store, or serialise. A scan of every file
under the data directory found no credential value in the database, the logs,
or any export.

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


def test_subprocess_inherits_env_rather_than_being_handed_one():
    """No `env=` is passed to the process spawn in claude_cli.

    Inheriting is why the token never enters Python-visible state: the child
    gets the parent's environment from the OS, and the server never reads the
    value to hand it over.

    THE FIRST VERSION OF THIS TEST WAS WRONG IN TWO WAYS, and the mutation
    battery caught both by surviving. It grepped for `\\benv\\s*=\\s*dict\\(`,
    which (a) misses `_env = dict(...)` outright, because `_` is a word
    character so there is no boundary before `env`, and (b) was asserting
    against the wrong thing anyway — a dict built and never used is not a leak.
    What matters is whether an environment reaches the CALL. So this now walks
    the AST and looks at the spawn's keyword arguments, which is the actual
    invariant and cannot be dodged by naming.
    """
    path = APP_DIR / "services" / "claude_cli.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    spawns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) in
             {"create_subprocess_exec", "create_subprocess_shell", "Popen", "run"})
    ]
    # NON-EMPTY-SET GUARD: if the spawn call was renamed or moved, "no env= is
    # passed" is true of zero calls and this test passes having checked nothing.
    assert spawns, (
        "no subprocess spawn found in claude_cli.py — this test would pass over "
        "an empty set. If the spawn moved, move the test."
    )

    handed = [
        f"{path.name}:{node.lineno}"
        for node in spawns
        for kw in node.keywords
        if kw.arg == "env"
    ]
    assert not handed, (
        f"an explicit environment is handed to the subprocess at {handed}. "
        f"Inheriting is what keeps the credential out of the server's own memory."
    )
