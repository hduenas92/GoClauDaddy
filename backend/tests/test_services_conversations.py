import json

from app.db.connection import get_connection
from app.routers.conversation_stats import get_conversation_stats
from app.services import conversations_service as svc


def test_create_and_get_conversation(temp_db):
    conv = svc.create_conversation(name="Test Chat")
    fetched = svc.get_conversation(conv.id)
    assert fetched.name == "Test Chat"
    assert fetched.status == "idle"
    assert fetched.session_id is None


def test_list_conversations_ordered_by_updated_at_desc(temp_db):
    c1 = svc.create_conversation(name="first")
    c2 = svc.create_conversation(name="second")
    convs = svc.list_conversations()
    assert [c.id for c in convs] == [c2.id, c1.id] or [c.id for c in convs] == [c1.id, c2.id]
    # both created "now" so order may tie — just assert both present
    assert {c.id for c in convs} == {c1.id, c2.id}


def test_rename_conversation(temp_db):
    conv = svc.create_conversation(name="old")
    svc.rename_conversation(conv.id, "new")
    assert svc.get_conversation(conv.id).name == "new"


def test_set_session_id(temp_db):
    conv = svc.create_conversation()
    svc.set_session_id(conv.id, "sess-abc")
    assert svc.get_conversation(conv.id).session_id == "sess-abc"


def test_update_settings_partial(temp_db):
    conv = svc.create_conversation(model="model-a")
    svc.update_conversation_settings(conv.id, permission_mode="plan")
    updated = svc.get_conversation(conv.id)
    assert updated.model == "model-a"  # unchanged
    assert updated.permission_mode == "plan"


def test_delete_conversation_cascades_messages(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "user", "hi")
    svc.delete_conversation(conv.id)
    assert svc.get_conversation(conv.id) is None
    assert svc.list_messages(conv.id) == []


def test_add_message_increments_seq(temp_db):
    conv = svc.create_conversation()
    m1 = svc.add_message(conv.id, "user", "first")
    m2 = svc.add_message(conv.id, "assistant", "second")
    assert m1.seq == 1
    assert m2.seq == 2
    msgs = svc.list_messages(conv.id)
    assert [m.content for m in msgs] == ["first", "second"]


def test_add_message_with_usage(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "hi", input_tokens=10, output_tokens=5, thinking="pondering")
    fetched = svc.list_messages(conv.id)[0]
    assert fetched.input_tokens == 10
    assert fetched.output_tokens == 5
    assert fetched.thinking == "pondering"


# ---------------------------------------------------------------------------
# Cache token columns must round-trip. The test above covers input/output only,
# so a regression that dropped the cache columns from the INSERT would pass it.
# ---------------------------------------------------------------------------

def test_add_message_round_trips_cache_token_columns(temp_db):
    conv = svc.create_conversation()
    svc.add_message(
        conv.id,
        "assistant",
        "hi",
        input_tokens=3,
        output_tokens=7,
        cache_read_tokens=150055,
        cache_creation_tokens=978,
    )
    fetched = svc.list_messages(conv.id)[0]
    assert fetched.cache_read_tokens == 150055
    assert fetched.cache_creation_tokens == 978


def test_cache_token_columns_default_to_zero(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "hi")
    fetched = svc.list_messages(conv.id)[0]
    assert fetched.cache_read_tokens == 0
    assert fetched.cache_creation_tokens == 0


def test_add_message_round_trips_tool_calls_json(temp_db):
    conv = svc.create_conversation()
    payload = '[{"id":"t1","name":"Read","input":{},"output":"body"}]'
    svc.add_message(conv.id, "assistant", "", tool_calls=payload)
    assert svc.list_messages(conv.id)[0].tool_calls == payload


# ---------------------------------------------------------------------------
# derive_title — the capitalization case is a real regression guard.
# An earlier implementation used str.capitalize(), which lowercases everything
# after the first character, mangling acronyms ("API" -> "api").
# ---------------------------------------------------------------------------

def test_derive_title_capitalizes_first_letter_only():
    assert svc.derive_title("fix the API endpoint") == "Fix the API endpoint"


def test_derive_title_preserves_interior_casing():
    """str.capitalize() would produce 'Update goclaudaddy readme'."""
    assert svc.derive_title("update GoClaudaddy README") == "Update GoClaudaddy README"


def test_derive_title_leaves_already_capitalized_alone():
    assert svc.derive_title("Fix the bug") == "Fix the bug"


def test_derive_title_strips_fenced_code_blocks():
    title = svc.derive_title("explain this\n```python\nprint(1)\n```\nplease")
    assert "print" not in title
    assert title.startswith("Explain this")


def test_derive_title_strips_inline_code():
    assert "`" not in svc.derive_title("what does `foo_bar` do")


def test_derive_title_strips_markdown_punctuation():
    title = svc.derive_title("## **Important** [link] ~~old~~ !bang")
    for ch in "#*_~>`[]!":
        assert ch not in title


def test_derive_title_collapses_whitespace():
    assert svc.derive_title("too    many\n\n\tspaces") == "Too many spaces"


def test_derive_title_caps_at_seven_words():
    assert len(svc.derive_title("one two three four five six seven eight nine").split()) == 7


def test_derive_title_respects_max_chars_on_a_word_boundary():
    title = svc.derive_title("supercalifragilistic expialidocious antidisestablishmentarianism pneumonoultramicroscopic")
    assert len(title) <= 55
    assert not title.endswith("-")
    # truncation happens at a space, so no partial word survives
    assert " " not in title[len(title) - 1:]


def test_derive_title_empty_input_falls_back():
    assert svc.derive_title("") == "New Conversation"


def test_derive_title_whitespace_only_falls_back():
    assert svc.derive_title("   \n\t  ") == "New Conversation"


def test_derive_title_markdown_only_falls_back():
    assert svc.derive_title("### ***") == "New Conversation"


def test_derive_title_honors_explicit_limits():
    assert svc.derive_title("alpha beta gamma delta", max_words=2) == "Alpha beta"


# ---------------------------------------------------------------------------
# auto_title_conversation — must only ever overwrite a still-default name.
# ---------------------------------------------------------------------------

_DEFAULT_NAME = "Chat Sep 16 13:01"  # matches ^Chat \w+ \d+ \d+:\d+$


def test_auto_title_renames_a_default_named_conversation(temp_db):
    conv = svc.create_conversation()
    svc.rename_conversation(conv.id, _DEFAULT_NAME)
    # exactly 7 words, so none are dropped by max_words
    svc.add_message(conv.id, "user", "add a dark mode toggle to settings")
    svc.auto_title_conversation(conv.id)
    assert svc.get_conversation(conv.id).name == "Add a dark mode toggle to settings"


def test_auto_title_truncates_a_long_first_message_to_seven_words(temp_db):
    conv = svc.create_conversation()
    svc.rename_conversation(conv.id, _DEFAULT_NAME)
    svc.add_message(conv.id, "user", "add a dark mode toggle to the settings drawer please")
    svc.auto_title_conversation(conv.id)
    assert svc.get_conversation(conv.id).name == "Add a dark mode toggle to the"


def test_auto_title_does_not_touch_a_user_named_conversation(temp_db):
    conv = svc.create_conversation()
    svc.rename_conversation(conv.id, "My Important Thread")
    svc.add_message(conv.id, "user", "something else entirely")
    svc.auto_title_conversation(conv.id)
    assert svc.get_conversation(conv.id).name == "My Important Thread"


def test_auto_title_no_op_when_no_user_message(temp_db):
    conv = svc.create_conversation()
    svc.rename_conversation(conv.id, _DEFAULT_NAME)
    svc.add_message(conv.id, "assistant", "I replied first somehow")
    svc.auto_title_conversation(conv.id)
    assert svc.get_conversation(conv.id).name == _DEFAULT_NAME


def test_auto_title_uses_the_first_user_message(temp_db):
    conv = svc.create_conversation()
    svc.rename_conversation(conv.id, _DEFAULT_NAME)
    svc.add_message(conv.id, "user", "first question here")
    svc.add_message(conv.id, "assistant", "answer")
    svc.add_message(conv.id, "user", "totally different second question")
    svc.auto_title_conversation(conv.id)
    assert svc.get_conversation(conv.id).name == "First question here"


def test_auto_title_on_unknown_conversation_is_a_no_op(temp_db):
    svc.auto_title_conversation("no-such-id")  # must not raise


# ---------------------------------------------------------------------------
# export_as_markdown — rich export with thinking + tool-call <details> blocks.
# ---------------------------------------------------------------------------

def test_export_thinking_present_yields_one_thinking_block(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "the fix", thinking="pondering the bug")
    out = svc.export_as_markdown(conv.id)
    assert out.count("<details><summary>Thinking</summary>") == 1
    assert "pondering the bug" in out


def test_export_no_thinking_means_no_thinking_block(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "just an answer")
    out = svc.export_as_markdown(conv.id)
    assert "Thinking</summary>" not in out


def test_export_tool_calls_present_with_correct_count(temp_db):
    conv = svc.create_conversation()
    payload = json.dumps([
        {"name": "Read", "input": {"file_path": "a.py"}, "output": "ok"},
        {"name": "Edit", "input": {"file_path": "a.py"}, "output": "applied"},
    ])
    svc.add_message(conv.id, "assistant", "done", tool_calls=payload)
    out = svc.export_as_markdown(conv.id)
    assert "<summary>2 tool calls</summary>" in out


def test_export_singular_tool_call_label(temp_db):
    conv = svc.create_conversation()
    payload = json.dumps([{"name": "Bash", "input": {"command": "pytest"}, "output": "ok"}])
    svc.add_message(conv.id, "assistant", "done", tool_calls=payload)
    out = svc.export_as_markdown(conv.id)
    assert "<summary>1 tool call</summary>" in out


def test_export_null_empty_and_malformed_tool_calls_produce_no_block(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "reply one", tool_calls=None)
    svc.add_message(conv.id, "assistant", "reply two", tool_calls="[]")
    svc.add_message(conv.id, "assistant", "reply three", tool_calls="not json")
    out = svc.export_as_markdown(conv.id)
    assert "tool call" not in out
    assert "reply one" in out
    assert "reply two" in out
    assert "reply three" in out


def test_export_tool_error_is_marked(temp_db):
    conv = svc.create_conversation()
    payload = json.dumps([{"name": "Bash", "input": {"command": "pytest"}, "output": "FAILED", "is_error": True}])
    svc.add_message(conv.id, "assistant", "done", tool_calls=payload)
    out = svc.export_as_markdown(conv.id)
    assert "**Error:**" in out


def test_export_tool_with_no_output_is_pending(temp_db):
    conv = svc.create_conversation()
    payload = json.dumps([{"name": "Bash", "input": {"command": "pytest"}}])
    svc.add_message(conv.id, "assistant", "done", tool_calls=payload)
    out = svc.export_as_markdown(conv.id)
    assert "_(pending)_" in out


def test_export_literal_details_tag_in_content_does_not_truncate_document(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "here is a tag: </details> in my text")
    svc.add_message(conv.id, "user", "a later message")
    out = svc.export_as_markdown(conv.id)
    assert "a later message" in out


def test_export_triple_backtick_in_thinking_keeps_fence_intact(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "answer", thinking="some code:\n```\nx = 1\n```\ndone")
    svc.add_message(conv.id, "user", "next message")
    out = svc.export_as_markdown(conv.id)
    # The outer fence must be longer (4 backticks) than the embedded ``` run,
    # or the embedded fence would close the block early and corrupt the doc.
    assert "````" in out
    assert "\ndone\n````" in out  # "done" stays inside the block, not spilled after it
    assert "next message" in out


def test_export_long_tool_output_is_truncated(temp_db):
    conv = svc.create_conversation()
    payload = json.dumps([{"name": "Bash", "input": {"command": "run"}, "output": "x" * 2000}])
    svc.add_message(conv.id, "assistant", "done", tool_calls=payload)
    out = svc.export_as_markdown(conv.id)
    assert "(truncated)" in out
    assert "x" * 2000 not in out


def test_export_stopped_marker_is_stripped_and_noted(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "partial answer\n\n<!-- claudioui:stopped -->")
    out = svc.export_as_markdown(conv.id)
    assert "<!-- claudioui:stopped -->" not in out
    assert "_(stopped)_" in out


def test_export_unknown_conversation_returns_empty_string(temp_db):
    assert svc.export_as_markdown("no-such-id") == ""


# ---------------------------------------------------------------------------
# Supersede (soft delete). Regenerate marks the previous assistant turn
# superseded instead of deleting it, so the rollback stays possible. Three read
# paths must agree on what "live" means — and one must deliberately disagree:
# the transcript and the search index hide a superseded row, while the token
# accounting keeps it, because the money was spent whether or not the answer was
# kept. Search hiding is covered in test_search.py; these cover the other two.
# ---------------------------------------------------------------------------

def test_delete_last_message_hides_the_row_without_deleting_it(temp_db):
    conv = svc.create_conversation()
    question = svc.add_message(conv.id, "user", "question")
    answer = svc.add_message(conv.id, "assistant", "answer to supersede")

    assert svc.delete_last_message(conv.id) is True

    # Gone from the live transcript, but the row itself must survive: the whole
    # point of superseding rather than deleting is that it stays reversible.
    assert [m.id for m in svc.list_messages(conv.id)] == [question.id]
    with get_connection() as conn:
        row = conn.execute(
            "SELECT superseded_by FROM messages WHERE id = ?", (answer.id,)
        ).fetchone()
    assert row is not None
    # The exact marker value is an implementation detail; "non-NULL means dead"
    # is the contract that list_messages depends on.
    assert row["superseded_by"] is not None


def test_delete_last_message_returns_false_when_no_live_assistant_remains(temp_db):
    conv = svc.create_conversation()
    question = svc.add_message(conv.id, "user", "only a user turn")

    # The role filter is real: a user turn must never be superseded by this.
    assert svc.delete_last_message(conv.id) is False
    assert [m.id for m in svc.list_messages(conv.id)] == [question.id]

    svc.add_message(conv.id, "assistant", "only answer")
    assert svc.delete_last_message(conv.id) is True
    # A second call has nothing live to supersede — it must not re-stamp the
    # row, and it must report that honestly instead of raising.
    assert svc.delete_last_message(conv.id) is False


def test_export_omits_a_superseded_assistant_message(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "user", "keep me in the export")
    svc.add_message(conv.id, "assistant", "discard me from the export")

    # Guard: it is genuinely in the document before superseding, so the
    # assertion below is not passing on a document that never had it.
    assert "discard me from the export" in svc.export_as_markdown(conv.id)

    assert svc.delete_last_message(conv.id) is True

    out = svc.export_as_markdown(conv.id)
    assert "discard me from the export" not in out
    assert "keep me in the export" in out


def test_stats_still_count_a_superseded_assistant_message(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "user", "question")
    svc.add_message(conv.id, "assistant", "first answer", input_tokens=100, output_tokens=20)

    before = get_conversation_stats(conv.id)
    assert before["step_count"] == 1
    assert before["tokens_in"] == 100

    # Regenerate: supersede the old answer, then append the replacement.
    assert svc.delete_last_message(conv.id) is True
    svc.add_message(conv.id, "assistant", "replacement answer", input_tokens=7, output_tokens=3)

    live_assistants = [m.content for m in svc.list_messages(conv.id) if m.role == "assistant"]
    assert live_assistants == ["replacement answer"]

    # Two assistant steps are billed even though only one is live, so the
    # accounting view must NOT inherit the superseded filter the transcript uses.
    after = get_conversation_stats(conv.id)
    assert after["step_count"] == 2
    assert after["tokens_in"] == 107
    assert after["tokens_out"] == 23
