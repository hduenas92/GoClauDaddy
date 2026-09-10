import json

from app.services.stream_parser import parse_line, strip_ansi


def test_strip_ansi_removes_escape_codes():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_empty_line_yields_nothing():
    assert parse_line("") == []
    assert parse_line("   ") == []


def test_non_json_line_becomes_text_event():
    assert parse_line("some banner output") == [{"type": "text", "text": "some banner output"}]


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
    assert {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5}} in events


def test_result_event_with_usage():
    line = json.dumps({"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50}})
    assert parse_line(line) == [{"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50}}]


def test_result_event_without_usage():
    line = json.dumps({"type": "result"})
    assert parse_line(line) == [{"type": "result", "usage": None}]


def test_unknown_event_type_yields_nothing():
    line = json.dumps({"type": "something_new"})
    assert parse_line(line) == []
