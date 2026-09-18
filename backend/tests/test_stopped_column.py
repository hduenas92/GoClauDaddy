"""
Phase 3 task 4-P1: a stopped turn is a column, not a marker inside content.

WHY THIS FILE EXISTS AT ALL, stated first because it is the whole point.

v15 added `messages.stopped` and backfilled it. Nothing ever wrote it
afterwards and nothing ever read it: `add_message()` had no parameter for it,
`Message.from_row` did not read it, and no SELECT in `app/` mentioned it. The
only code that knew a turn had been stopped was `_strip_stopped()`, which
scraped an HTML comment back out of `content` at export time.

MEASURED before any of this was written: of 94 messages in the real database,
0 had `stopped = 1` and 0 carried the marker. The set was EMPTY. So every
assertion anyone might write about stopped messages against that data could not
fail, and the brief's claim that the table "currently holds two competing
representations" was not true of the data — it was true of the CODE. That is
why each test below CREATES its stopped message instead of looking for one, and
why `test_the_set_is_not_empty` exists as a guard rather than as a courtesy.

The marker was not merely redundant. v18 indexes `messages.content` verbatim
into `messages_fts`, so the literal text `<!-- claudioui:stopped -->` went into
the full-text search index of every stopped message — searching `claudioui`
would have returned exactly the stopped turns. That is what makes the column
authoritative by force rather than by preference, and it is asserted directly
in `test_marker_text_is_not_in_the_search_index`.
"""
import sqlite3

from app.db.connection import get_connection
from app.services import conversations_service as convs
from app.services.conversations_service import create_conversation

MARKER_TEXT = "claudioui:stopped"


def _row(message_id: str) -> sqlite3.Row:
    with get_connection() as c:
        return c.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()


# --- the guard -------------------------------------------------------------

def test_the_set_is_not_empty(temp_db):
    """Anti-vacuity. Every assertion below is about a stopped message, so if
    creating one silently produced an ordinary message instead, they would all
    pass while testing nothing. This fails loudly in that case."""
    conv = create_conversation().id
    m = convs.add_message(conv, "assistant", "half a thoug", stopped=True)
    row = _row(m.id)
    assert row is not None, "the message was not written at all"
    assert row["stopped"] == 1, (
        "add_message(stopped=True) did not set the column — every other test in "
        "this file would pass vacuously"
    )


# --- the column is the representation --------------------------------------

def test_stopped_is_persisted_and_read_back(temp_db):
    conv = create_conversation().id
    m = convs.add_message(conv, "assistant", "half a thoug", stopped=True)
    assert m.stopped is True, "the returned Message must carry it"
    assert _row(m.id)["stopped"] == 1
    again = [x for x in convs.list_messages(conv) if x.id == m.id][0]
    assert again.stopped is True, "Message.from_row must read the column back"


def test_ordinary_messages_are_not_stopped(temp_db):
    """The false-positive guard. Without this, a `stopped` that was always True
    would satisfy every other assertion here."""
    conv = create_conversation().id
    m = convs.add_message(conv, "assistant", "a complete thought")
    assert m.stopped is False
    assert _row(m.id)["stopped"] == 0
    again = [x for x in convs.list_messages(conv) if x.id == m.id][0]
    assert again.stopped is False


def test_stopped_content_is_clean(temp_db):
    """The content column holds what the model said and nothing else."""
    conv = create_conversation().id
    m = convs.add_message(conv, "assistant", "half a thoug", stopped=True)
    stored = _row(m.id)["content"]
    assert stored == "half a thoug"
    assert MARKER_TEXT not in stored
    assert "<!--" not in stored


# --- the reason the column wins --------------------------------------------

def test_marker_text_is_not_in_the_search_index(temp_db):
    """v18's trigger indexes `content` verbatim. When stoppedness lived inside
    content, the marker went into the FTS index, so a search for `claudioui`
    matched precisely the stopped turns. Asserted against the index itself, not
    against the API, so it cannot be satisfied by query-time filtering."""
    conv = create_conversation().id
    convs.add_message(conv, "assistant", "half a thoug", stopped=True)
    with get_connection() as c:
        # Quoted: unquoted `claudioui:stopped` is FTS5 COLUMN-FILTER syntax
        # ("column claudioui contains stopped"), which raises rather than
        # searching. `claudioui` on its own is the token that would actually be
        # indexed if the marker were still embedded in content.
        hits = c.execute(
            "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH ?",
            ('"claudioui"',),
        ).fetchone()[0]
    assert hits == 0, (
        f"{hits} row(s) in the FTS index contain the stopped marker — stoppedness "
        "is leaking into full-text search"
    )


# --- the export keeps working, driven off the column -----------------------

def test_export_marks_stopped_turns_from_the_column(temp_db):
    conv = create_conversation().id
    convs.add_message(conv, "user", "go on then")
    convs.add_message(conv, "assistant", "half a thoug", stopped=True)
    out = convs.export_as_markdown(conv)
    assert "_(stopped)_" in out, "the export must still say a turn was stopped"
    assert MARKER_TEXT not in out, "but must not leak the old marker into the file"
    assert "half a thoug" in out


def test_export_does_not_mark_complete_turns(temp_db):
    conv = create_conversation().id
    convs.add_message(conv, "user", "go on then")
    convs.add_message(conv, "assistant", "a complete thought")
    out = convs.export_as_markdown(conv)
    assert "_(stopped)_" not in out
