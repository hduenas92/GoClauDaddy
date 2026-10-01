"""Credential redaction tests for claude_cli stderr handling.

These call the real redact_secrets and drain_stderr.  Fake credentials are
built at runtime so no key-shaped literal has to live in the test file.
"""

import pytest

from app.services.claude_cli import drain_stderr, redact_secrets


async def _fake_stream(*lines):
    for line in lines:
        yield (line + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_env_secret_values_are_redacted(monkeypatch, caplog):
    fake_secrets = {
        "ANTHROPIC_AUTH_TOKEN": "auth-" + "a" * 20,
        "GOCODE_API_TOKEN": "gocode-" + "b" * 20,
        "ANTHROPIC_API_KEY": "api-" + "c" * 20,
    }
    for name, value in fake_secrets.items():
        monkeypatch.setenv(name, value)

    line = "prefix " + " ".join(fake_secrets.values()) + " suffix"
    expected = "prefix [REDACTED] [REDACTED] [REDACTED] suffix"

    assert redact_secrets(line) == expected

    sink: list[str] = []
    with caplog.at_level("WARNING"):
        await drain_stderr(_fake_stream(line), sink)

    assert sink == [expected]
    for value in fake_secrets.values():
        assert value not in caplog.text


@pytest.mark.asyncio
async def test_sk_style_secrets_are_redacted(caplog):
    fake_key = "-".join(["sk", "ant", "x" * 20])
    line = f"claude key={fake_key}."
    expected = "claude key=[REDACTED]."

    assert redact_secrets(line) == expected

    sink: list[str] = []
    with caplog.at_level("WARNING"):
        await drain_stderr(_fake_stream(line), sink)

    assert sink == [expected]
    assert fake_key not in caplog.text


@pytest.mark.asyncio
async def test_bearer_tokens_are_redacted_case_insensitively(caplog):
    fake_token = "bearer-token-" + "d" * 20
    line = f"Authorization: bEaReR {fake_token} trailing"
    expected = "Authorization: bEaReR [REDACTED] trailing"

    assert redact_secrets(line) == expected

    sink: list[str] = []
    with caplog.at_level("WARNING"):
        await drain_stderr(_fake_stream(line), sink)

    assert sink == [expected]
    assert fake_token not in caplog.text


@pytest.mark.asyncio
async def test_ordinary_line_is_unchanged(caplog):
    line = "Error: something actually broke"

    assert redact_secrets(line) == line

    sink: list[str] = []
    with caplog.at_level("WARNING"):
        await drain_stderr(_fake_stream(line), sink)

    assert sink == [line]
    assert line in caplog.text