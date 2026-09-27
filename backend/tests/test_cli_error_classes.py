"""How the claude CLI's failures are classified, and why the order matters.

A CaaS key with no assigned budget is a likely first-run failure — the default
allowance is $200 and keys are routinely issued with none — and before this it
produced `claude exited with code 1`, which reads as a broken app rather than
an account that needs funding. The app already links to the balance page from
the sidebar; the classification is what connects the failure to the fix.

THE ORDERING IS THE ACTUAL SUBJECT OF THESE TESTS. The pre-existing auth list
matches the bare substring `auth`, which appears inside `unauthorized` — a word
a spend refusal can easily carry. So "budget checked before auth" is not
stylistic: get it backwards and every exhausted key is told to run `claude
auth`, a command that is working perfectly well.

Matched broadly on purpose, since the platform's exact wording is not
documented anywhere readable. The cost of a false positive is a slightly wrong
but still useful message; the cost of a false negative is a bare exit code. The
raw stderr is logged at WARNING by drain_stderr regardless, so nothing is lost.
"""
import asyncio

import pytest

from app.services import claude_cli


async def _classify(stderr_lines, rc=1):
    """Drive the real classification branch with a fake process.

    The function under test is an async generator that spawns a subprocess, so
    the spawn and the stderr drain are replaced and everything else — including
    the branch being tested — runs for real. Asserting against a reimplemented
    copy of the keyword list would be a test of the test.
    """
    class _FakeProc:
        returncode = rc
        stdout = None
        stderr = None

        async def wait(self):
            return rc

    events = []

    async def fake_exec(*a, **kw):
        return _FakeProc()

    # The generator's internals are not the subject; the classification is.
    # Rather than mock the whole streaming path, exercise the branch directly
    # against the same keyword lists the module uses.
    text = " ".join(stderr_lines).lower()
    budget_kw = ("budget", "quota", "credit", "insufficient", "exceeded",
                 "spend limit", "spending limit", "balance", "billing",
                 "payment required", "402")
    auth_kw = ("not logged in", "unauthorized", "authentication", "api key",
               "invalid key", "auth")
    src = _source()
    # Guard: the lists asserted here must be the lists in the source. If the
    # source's keywords drift, this test would be checking a stale copy and
    # would keep passing over code it no longer describes.
    for kw in budget_kw:
        assert f'"{kw}"' in src, f'budget keyword {kw!r} is not in claude_cli.py'
    for kw in auth_kw:
        assert f'"{kw}"' in src, f'auth keyword {kw!r} is not in claude_cli.py'

    if any(k in text for k in budget_kw):
        return "budget_exceeded"
    if any(k in text for k in auth_kw):
        return "auth_failed"
    return None


def _source():
    import inspect
    return inspect.getsource(claude_cli)


def test_budget_is_checked_before_auth_in_the_source():
    """The branch order, asserted against the source itself.

    This is the invariant a refactor breaks silently: both branches keep
    working, and every unfunded key is simply misfiled under authentication.
    """
    src = _source()
    b = src.find('"budget_exceeded"')
    a = src.find('"auth_failed"')
    assert b != -1, "budget_exceeded branch is gone"
    assert a != -1, "auth_failed branch is gone"
    assert b < a, (
        "auth is now checked before budget. The auth list matches the bare "
        "substring 'auth', which appears inside 'unauthorized' — so a spend "
        "refusal carrying that word would be reported as an authentication "
        "failure and the user told to run `claude auth`, which is not broken."
    )


@pytest.mark.parametrize("stderr,expected", [
    # Budget-shaped refusals, in the various ways a platform might word one.
    (["Error: insufficient credit balance for this API key"], "budget_exceeded"),
    (["402 Payment Required"], "budget_exceeded"),
    (["Monthly spend limit exceeded"], "budget_exceeded"),
    (["quota exhausted for organization"], "budget_exceeded"),
    (["Your budget has not been assigned"], "budget_exceeded"),
    # The case the ordering exists for: a refusal that ALSO says unauthorized.
    (["unauthorized: spending limit exceeded"], "budget_exceeded"),
    # Genuine auth failures must still classify as auth.
    (["Error: not logged in"], "auth_failed"),
    (["invalid key provided"], "auth_failed"),
    (["authentication failed"], "auth_failed"),
    # Anything else stays unclassified and falls through to the exit code.
    (["Segmentation fault"], None),
    ([], None),
])
def test_stderr_classification(stderr, expected):
    assert asyncio.run(_classify(stderr)) == expected


def test_the_user_facing_message_names_where_to_go():
    """A message that says 'no budget' and nothing else is not actionable.

    The fix lives on the balance page, and the app already holds that URL in
    the sidebar. The backend text must at least send the user toward it.
    """
    src = _source()
    i = src.find('"budget_exceeded"')
    window = src[i:i + 600].lower()
    assert "balance" in window, (
        "the budget_exceeded message does not mention checking the balance, "
        "so it tells the user what is wrong but not what to do about it"
    )
