from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.services.conversations_service import add_message, create_conversation

client = TestClient(app)


def test_plain_single_word_match_returns_the_message(temp_db):
    conv = create_conversation(name="C1")
    msg = add_message(conv.id, "user", "the quick brown fox jumps")

    res = client.get("/api/search", params={"q": "brown"})
    assert res.status_code == 200
    results = res.json()["results"]
    assert len(results) == 1
    assert results[0]["message_id"] == msg.id
    assert results[0]["conversation_id"] == conv.id
    assert results[0]["conversation_name"] == "C1"


def test_word_in_no_message_returns_empty(temp_db):
    conv = create_conversation()
    add_message(conv.id, "user", "hello world")

    res = client.get("/api/search", params={"q": "zzznonexistentzzz"})
    assert res.status_code == 200
    assert res.json()["results"] == []


def test_multi_word_query_is_and_not_phrase(temp_db):
    conv = create_conversation()
    # words present but not adjacent / in reverse order -> AND should still match,
    # a phrase search would not.
    msg = add_message(conv.id, "user", "brown things and a quick fox")

    res = client.get("/api/search", params={"q": "quick brown"})
    results = res.json()["results"]
    assert len(results) == 1
    assert results[0]["message_id"] == msg.id


def test_conversation_id_scoping_excludes_other_conversations(temp_db):
    conv_a = create_conversation()
    conv_b = create_conversation()
    msg_a = add_message(conv_a.id, "user", "unique_scope_term here")
    add_message(conv_b.id, "user", "unique_scope_term here too")

    res = client.get("/api/search", params={"q": "unique_scope_term", "conversation_id": conv_a.id})
    results = res.json()["results"]
    assert len(results) == 1
    assert results[0]["message_id"] == msg_a.id
    assert results[0]["conversation_id"] == conv_a.id


def test_superseded_message_never_appears(temp_db):
    conv = create_conversation()
    msg = add_message(conv.id, "user", "supersededterm should vanish")

    # Prove it matches before superseding it.
    pre = client.get("/api/search", params={"q": "supersededterm"}).json()["results"]
    assert len(pre) == 1
    assert pre[0]["message_id"] == msg.id

    replacement = add_message(conv.id, "user", "supersededterm replacement")
    with get_connection() as conn:
        conn.execute("UPDATE messages SET superseded_by = ? WHERE id = ?", (replacement.id, msg.id))

    post = client.get("/api/search", params={"q": "supersededterm"}).json()["results"]
    ids = [r["message_id"] for r in post]
    assert msg.id not in ids
    assert replacement.id in ids


def test_hostile_inputs_never_500(temp_db):
    conv = create_conversation()
    add_message(conv.id, "user", "some ordinary content for the corpus")

    hostile_inputs = [
        '"',
        '""',
        'foo"bar',
        'NEAR(a b)',
        'a OR b',
        'a AND b',
        'NOT a',
        '*',
        '^foo',
        'content: x',
        'a*',
        '(',
        ')',
        'x' * 5000,
        "'; DROP TABLE messages; --",
    ]
    for q in hostile_inputs:
        res = client.get("/api/search", params={"q": q})
        assert res.status_code in (200, 400), f"q={q!r} returned {res.status_code}"


def test_hostile_inputs_do_not_mutate_the_database(temp_db):
    conv = create_conversation()
    add_message(conv.id, "user", "some ordinary content for the corpus")

    with get_connection() as conn:
        before_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        before_fts = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]

    hostile_inputs = [
        '"', '""', 'foo"bar', 'NEAR(a b)', 'a OR b', 'a AND b', 'NOT a',
        '*', '^foo', 'content: x', 'a*', '(', ')', 'x' * 5000,
        "'; DROP TABLE messages; --",
    ]
    for q in hostile_inputs:
        client.get("/api/search", params={"q": q})

    with get_connection() as conn:
        after_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        after_fts = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]

    assert after_messages == before_messages
    assert after_fts == before_fts


def test_empty_and_whitespace_q_return_400(temp_db):
    create_conversation()
    assert client.get("/api/search", params={"q": ""}).status_code == 400
    assert client.get("/api/search", params={"q": "   "}).status_code == 400


def test_limit_respected_and_truncated_flag(temp_db):
    conv = create_conversation()
    for i in range(5):
        add_message(conv.id, "user", f"limittestterm entry number {i}")

    res = client.get("/api/search", params={"q": "limittestterm", "limit": 3})
    data = res.json()
    assert len(data["results"]) == 3
    assert data["truncated"] is True

    res = client.get("/api/search", params={"q": "limittestterm", "limit": 10})
    data = res.json()
    assert len(data["results"]) == 5
    assert data["truncated"] is False
