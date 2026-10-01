"""Regression coverage for a real, intermittently-observed bug: characters in
`claude` output occasionally arrived corrupted (~1 in 5-10 longer responses)
because each chunk was decoded independently. This tests the exact failure
mode directly: a multi-byte UTF-8 character split across two separate reads.
"""

import json
import logging

import pytest

from app.services.claude_cli import decode_and_parse_lines


async def _chunks(*byte_pieces):
    for piece in byte_pieces:
        yield piece


def _text_event(text: str) -> bytes:
    line = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        ensure_ascii=False,
    )
    return (line + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_normal_ascii_line_decodes_fine():
    events = [ev async for ev in decode_and_parse_lines(_chunks(_text_event("hello")))]
    assert events == [{"type": "text", "text": "hello"}]


@pytest.mark.asyncio
async def test_em_dash_intact_when_not_split():
    events = [ev async for ev in decode_and_parse_lines(_chunks(_text_event("a — b")))]
    assert events == [{"type": "text", "text": "a — b"}]


@pytest.mark.asyncio
async def test_multibyte_character_split_across_two_chunks_still_decodes_correctly():
    """The actual bug: an em dash (U+2014, UTF-8 bytes E2 80 94) split so that
    the first chunk ends mid-character and the second chunk starts with the
    remaining bytes — this must NOT produce a replacement character.
    """
    full_line = _text_event("a — b")
    # Split the raw bytes at an arbitrary point that lands inside the 3-byte
    # em-dash sequence (find it and cut after its first byte).
    dash_bytes = "—".encode()
    idx = full_line.find(dash_bytes)
    split_at = idx + 1  # cut after only the first byte of the 3-byte sequence
    chunk1, chunk2 = full_line[:split_at], full_line[split_at:]

    events = [ev async for ev in decode_and_parse_lines(_chunks(chunk1, chunk2))]
    assert events == [{"type": "text", "text": "a — b"}]
    assert "�" not in events[0]["text"]


@pytest.mark.asyncio
async def test_genuinely_invalid_utf8_still_falls_back_to_replacement():
    """If the bytes are truly invalid (not just split), replacement is the
    correct, honest behavior — this confirms we didn't break that fallback.
    """
    bad_line = b'{"type": "assistant", "message": {"content": [{"type": "text", "text": "a \xff b"}]}}\n'
    events = [ev async for ev in decode_and_parse_lines(_chunks(bad_line))]
    assert len(events) == 1
    assert "�" in events[0]["text"]


@pytest.mark.asyncio
async def test_final_line_without_trailing_newline_is_still_parsed():
    line = _text_event("no trailing newline").rstrip(b"\n")
    events = [ev async for ev in decode_and_parse_lines(_chunks(line))]
    assert events == [{"type": "text", "text": "no trailing newline"}]


@pytest.mark.asyncio
async def test_legitimately_encoded_replacement_character_does_not_warn(caplog):
    """Bytes EF BF BD decode to U+FFFD legitimately; nothing was replaced."""
    text = "a � b"
    with caplog.at_level(logging.WARNING, logger="goclaudaddy.claude_cli"):
        events = [ev async for ev in decode_and_parse_lines(_chunks(_text_event(text)))]

    assert events == [{"type": "text", "text": text}]
    warning_records = [
        record
        for record in caplog.records
        if record.name == "goclaudaddy.claude_cli" and record.levelno == logging.WARNING
    ]
    assert warning_records == []


@pytest.mark.asyncio
async def test_incomplete_utf8_at_stream_end_warns_on_final_flush(caplog):
    """The final decoder flush can replace an incomplete sequence; warn then."""
    full_line = _text_event("a — b").rstrip(b"\n")
    dash_bytes = "—".encode()
    idx = full_line.find(dash_bytes)
    assert idx != -1
    truncated = full_line[: idx + 1]  # ends with only the first byte of the em dash

    with caplog.at_level(logging.WARNING, logger="goclaudaddy.claude_cli"):
        events = [ev async for ev in decode_and_parse_lines(_chunks(truncated))]

    assert events  # the replacement-tail buffer is emitted as a non-JSON notice
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "goclaudaddy.claude_cli" and record.levelno == logging.WARNING
    ]
    assert warning_messages == [
        "Non-UTF-8 byte(s) in claude output — replaced (data loss of 1 char)"
    ]
