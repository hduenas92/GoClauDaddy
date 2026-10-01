import json

from app.services.stream_parser import parse_line, strip_ansi


def test_strip_ansi_removes_escape_codes():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_empty_line_yields_nothing():
    assert parse_line("") == []
    assert parse_line("   ") == []


def test_non_json_line_becomes_notice_event():
    # Houston 2026-09-25: non-JSON stdout is an error to surface, NOT Claude's
    # reply. It must never become assistant text (the old `text` event did).
    assert parse_line("some banner output") == [{"type": "notice", "text": "some banner output"}]


def test_system_init_yields_session_event():
    line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc-123"})
    assert parse_line(line) == [{"type": "session", "session_id": "abc-123"}]


def test_system_init_without_session_id_yields_nothing():
    line = json.dumps({"type": "system", "subtype": "init"})
    assert parse_line(line) == []


def test_assistant_text_block():
    line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}})
    assert parse_line(line) == [{"type": "text", "text": "hello"}]


def test_assistant_thinking_then_text_blocks_both_emitted():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "pondering"},
                    {"type": "text", "text": "answer"},
                ]
            },
        }
    )
    assert parse_line(line) == [
        {"type": "thinking", "thinking": "pondering"},
        {"type": "text", "text": "answer"},
    ]


def test_assistant_with_usage_emits_usage_event_too():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )
    events = parse_line(line)
    assert {"type": "text", "text": "hi"} in events
    assert {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}} in events


def test_result_event_with_usage():
    line = json.dumps({"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50}})
    assert parse_line(line) == [{"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}]


def test_result_event_without_usage():
    line = json.dumps({"type": "result"})
    assert parse_line(line) == [{"type": "result", "usage": None}]


def test_unknown_event_type_yields_nothing():
    line = json.dumps({"type": "something_new"})
    assert parse_line(line) == []


# ---------------------------------------------------------------------------
# Cache tokens must survive with their real values.
#
# The two usage tests above only ever pass ZERO for the cache fields, so they
# prove the keys exist but not that a real value is carried. A regression that
# hardcoded 0, or dropped the field on a resumed turn, would still pass them.
# On a --resume'd conversation cache reads are the bulk of the input, so losing
# them understates both cost and CTX% by multiples.
# ---------------------------------------------------------------------------

def test_assistant_usage_carries_nonzero_cache_values():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hi"}],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 150055,
                    "cache_creation_input_tokens": 978,
                },
            },
        }
    )
    usage = next(e["usage"] for e in parse_line(line) if e["type"] == "usage")
    assert usage["cache_read_input_tokens"] == 150055
    assert usage["cache_creation_input_tokens"] == 978
    assert usage["input_tokens"] == 3
    assert usage["output_tokens"] == 7


def test_result_usage_carries_nonzero_cache_values():
    line = json.dumps(
        {
            "type": "result",
            "usage": {
                "input_tokens": 4,
                "output_tokens": 760,
                "cache_read_input_tokens": 150055,
                "cache_creation_input_tokens": 978,
            },
        }
    )
    usage = parse_line(line)[0]["usage"]
    assert usage["cache_read_input_tokens"] == 150055
    assert usage["cache_creation_input_tokens"] == 978


def test_usage_dict_has_exactly_the_four_token_fields():
    """Guards the shape chat_socket and cost.py both depend on."""
    line = json.dumps(
        {"type": "result", "usage": {"input_tokens": 1, "output_tokens": 2}}
    )
    usage = parse_line(line)[0]["usage"]
    assert set(usage) == {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }


# ---------------------------------------------------------------------------
# Tool calls — untested until now, and Phase 4-A (tool calls surviving a page
# reload) depends entirely on these events being shaped correctly.
# ---------------------------------------------------------------------------

def test_assistant_tool_use_becomes_tool_call_event():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_abc",
                        "name": "Read",
                        "input": {"file_path": "/tmp/x.txt"},
                    }
                ]
            },
        }
    )
    assert parse_line(line) == [
        {
            "type": "tool_call",
            "id": "toolu_abc",
            "name": "Read",
            "input": {"file_path": "/tmp/x.txt"},
        }
    ]


def test_text_and_tool_use_in_one_message_both_emitted_in_order():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "let me look"},
                    {"type": "tool_use", "id": "t1", "name": "Grep", "input": {}},
                ]
            },
        }
    )
    events = parse_line(line)
    assert [e["type"] for e in events] == ["text", "tool_call"]


def test_user_tool_result_string_content():
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_abc", "content": "file body"}
                ]
            },
        }
    )
    assert parse_line(line) == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_abc",
            "content": "file body",
            "is_error": False,
        }
    ]


def test_user_tool_result_list_content_joins_text_blocks():
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [
                            {"type": "text", "text": "line one\n"},
                            {"type": "text", "text": "line two"},
                        ],
                    }
                ]
            },
        }
    )
    assert parse_line(line)[0]["content"] == "line one\nline two"


def test_user_tool_result_preserves_is_error():
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "boom",
                        "is_error": True,
                    }
                ]
            },
        }
    )
    assert parse_line(line)[0]["is_error"] is True


def test_result_with_is_error_becomes_error_event():
    line = json.dumps({"type": "result", "is_error": True, "result": "the CLI blew up"})
    assert parse_line(line) == [{"type": "error", "error": "Claude reported an error. Try again; if it keeps failing, open the LOG panel."}]


def test_result_with_is_error_and_no_message_still_errors():
    line = json.dumps({"type": "result", "is_error": True})
    events = parse_line(line)
    assert events[0]["type"] == "error"
    assert events[0]["error"]
