"""Deleting a conversation must take its rows with it.

WHY THIS FILE EXISTS — it is the half of golden-path step 12 that the golden
path cannot see.

tools/golden-path-check.mjs asserts, after deleting a conversation, that
`GET /api/search?q=<mark>` returns nothing. That assertion was written to catch
"the delete left the content behind, so it still turns up in search". Mutation
testing showed it CANNOT catch that, and the reason is in the search SQL: it
inner-joins `conversations`, so the moment the conversation row goes, every one
of its messages vanishes from search results whether or not the rows still
exist. Running the whole golden path with `PRAGMA foreign_keys = OFF` left 8
orphaned message rows in the database and the search assertion still reported
"0 rows match" — a clean PASS over a real leak.

The search assertion is still worth keeping: "deleted content does not surface
to the user" is a genuine property. But "deleted content is actually gone" has
to be asserted where it is visible, which is here, against the tables.

Mutation-verified: with `PRAGMA foreign_keys = OFF` in app/db/connection.py,
test_messages_go_with_the_conversation and
test_attachment_rows_go_with_the_conversation both fail.
"""

import pytest

from app.db.connection import get_connection
from app.services import conversations_service as convs


def _mk(conversation_name="doomed"):
    conv = convs.create_conversation(name=conversation_name)
    convs.add_message(conv.id, "user", "GPCASCADE question")
    convs.add_message(conv.id, "assistant", "GPCASCADE answer")
    return conv


def _count(sql, *params):
    with get_connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


def test_the_fixture_actually_wrote_rows(temp_db):
    """Guard first: every assertion below is about rows DISAPPEARING, and an
    empty table satisfies all of them. If this fails, nothing else here means
    anything."""
    conv = _mk()
    assert _count("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", conv.id) == 2


def test_messages_go_with_the_conversation(temp_db):
    conv = _mk()
    convs.delete_conversation(conv.id)
    assert _count("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", conv.id) == 0


def test_no_orphaned_messages_are_left_anywhere(temp_db):
    """The general form: no message may outlive its conversation. Stated over
    the whole table rather than over one id, because the failure mode is rows
    that keep their old conversation_id after the parent is gone."""
    conv = _mk()
    convs.delete_conversation(conv.id)
    orphans = _count(
        "SELECT COUNT(*) FROM messages m "
        "LEFT JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.id IS NULL"
    )
    assert orphans == 0


def test_attachment_rows_go_with_the_conversation(temp_db):
    conv = _mk()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO attachments (id, conversation_id, original_name, stored_path, "
            "mime_type, size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("att-1", conv.id, "n.txt", "/nowhere/n.txt", "text/plain", 1, "2026-01-01T00:00:00"),
        )
    assert _count("SELECT COUNT(*) FROM attachments WHERE conversation_id = ?", conv.id) == 1
    convs.delete_conversation(conv.id)
    assert _count("SELECT COUNT(*) FROM attachments WHERE conversation_id = ?", conv.id) == 0


def test_deleted_content_is_out_of_the_fts_index(temp_db):
    """Asserted against messages_fts DIRECTLY, not through /api/search.

    The search endpoint joins `conversations`, so it would report success here
    even with the index untouched — which is exactly the vacuity this file was
    written to cover. Querying the index on its own has no such escape hatch.
    """
    conv = _mk()
    before = _count("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH ?", "GPCASCADE")
    assert before >= 2, "the fixture's rows never reached the FTS index"
    convs.delete_conversation(conv.id)
    after = _count("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH ?", "GPCASCADE")
    assert after == 0
