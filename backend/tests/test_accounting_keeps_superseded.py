"""
Phase 3 task 4-P3: accounting must COUNT superseded rows. Regression guard.

Regenerate is a soft delete. `superseded_by` (v16) removes the old answer from
the live transcript, but the money was already spent, so every view that reports
SPEND must still count it. Two classes, deliberately disagreeing:

  hide it   transcript (list_messages, export) and search
  count it  every cost / token view

That disagreement is correct and it is fragile, because it looks like an
inconsistency. Someone tidying up could add `AND superseded_by IS NULL` to a
cost query "for consistency", your reported spend would quietly fall, and
nothing would fail.

MEASURED at the time of writing: `superseded_by` appears in exactly two places
that filter — search.py and conversations_service.py. The four accounting
queries never mention it, so they already behave correctly. But they are correct
by OMISSION, not by intent: nothing stated the rule and nothing enforced it.

`conversation_stats` was already guarded by
test_stats_still_count_a_superseded_assistant_message. These cover the three
accounting surfaces that were not:

  GET /api/conversations   per-conversation cost_usd   (conversations.py)
  GET /api/server/stats    totals + monthly cost       (server.py)
  GET /api/teams           team aggregate cost         (teams.py)

Each test asserts a STRICT INEQUALITY against a live-only baseline rather than a
hardcoded number. A test that merely asserted "cost == 0.0012" would also pass if
the superseded row were dropped and the figure happened to match; comparing
against what a superseded-filtering query WOULD return cannot.
"""
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.services import conversations_service as svc
from app.services.cost import compute_cost_usd

client = TestClient(app)

# Big enough that dropping one row moves every figure unmistakably.
OLD = {"input_tokens": 100_000, "output_tokens": 50_000}
NEW = {"input_tokens": 7, "output_tokens": 3}


def _regenerated_conversation():
    """A conversation whose first answer was superseded by a second.

    Returns the conversation. The superseded row is REAL: asserted here rather
    than assumed, because if the supersede silently did nothing, every caller
    below would compare a live row against itself and pass while testing nothing.
    """
    conv = svc.create_conversation(name="regen")
    svc.add_message(conv.id, "user", "question")
    svc.add_message(conv.id, "assistant", "first answer", **OLD)
    assert svc.delete_last_message(conv.id) is True, "supersede did not happen"
    svc.add_message(conv.id, "assistant", "replacement answer", **NEW)

    with get_connection() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND superseded_by IS NOT NULL",
            (conv.id,),
        ).fetchone()[0]
    assert n == 1, f"expected exactly 1 superseded row, found {n} — the set is wrong"

    live = [m.content for m in svc.list_messages(conv.id) if m.role == "assistant"]
    assert live == ["replacement answer"], "the transcript must still hide it"
    return conv


def test_conversation_list_cost_counts_a_superseded_answer(temp_db):
    conv = _regenerated_conversation()

    res = client.get("/api/conversations")
    assert res.status_code == 200
    row = next(c for c in res.json() if c["id"] == conv.id)

    # The FIRST version of this assertion could not fail, and the mutation run
    # is what exposed it: it re-queried SQLite itself for the token totals and
    # compared those, which tests SQLite, not the endpoint. Adding
    # "AND superseded_by IS NULL" to conversations.py left it green.
    #
    # So compare the endpoint's OWN cost_usd against the two costs it could be,
    # computed with the same function the endpoint uses. Only one of them can be
    # right, and they must differ, or the test proves nothing.
    model = svc.get_conversation(conv.id).model
    cost_all = compute_cost_usd(
        model, OLD["input_tokens"] + NEW["input_tokens"], OLD["output_tokens"] + NEW["output_tokens"], 0, 0
    )
    cost_live_only = compute_cost_usd(model, NEW["input_tokens"], NEW["output_tokens"], 0, 0)
    assert cost_all > cost_live_only, "the two candidate costs must differ, or this proves nothing"

    assert row["cost_usd"] == cost_all, (
        f"the list endpoint reported {row['cost_usd']}; counting all billed rows gives "
        f"{cost_all} and filtering superseded rows gives {cost_live_only}. The money was "
        "spent, so superseded rows must still be counted."
    )


def test_server_stats_count_a_superseded_answer(temp_db):
    _regenerated_conversation()

    res = client.get("/api/server/stats")
    assert res.status_code == 200
    body = res.json()

    total = OLD["input_tokens"] + NEW["input_tokens"]
    assert body["total_input"] == total, (
        f"server totals dropped a superseded row: {body['total_input']} != {total}"
    )
    assert body["total_output"] == OLD["output_tokens"] + NEW["output_tokens"]
    assert body["message_count"] == 2, "both assistant turns were billed"
    assert body["monthly_input"] == total, "monthly totals must not filter either"
    assert body["monthly_cost_usd"] > 0


def test_team_cost_counts_a_superseded_answer(temp_db):
    conv = _regenerated_conversation()

    # Membership is set at creation; there is no attach route.
    created = client.post("/api/teams", json={"name": "T1", "conversation_ids": [conv.id]})
    assert created.status_code in (200, 201), created.text
    team_id = created.json()["id"]

    listed = client.get("/api/teams")
    assert listed.status_code == 200
    team = next(t for t in listed.json() if t["id"] == team_id)

    # `> 0` would pass even with the superseded row dropped, because the live row
    # is billed too. Compare against both candidate totals instead.
    model = svc.get_conversation(conv.id).model
    cost_all = (
        compute_cost_usd(model, OLD["input_tokens"], OLD["output_tokens"], 0, 0)
        + compute_cost_usd(model, NEW["input_tokens"], NEW["output_tokens"], 0, 0)
    )
    cost_live_only = compute_cost_usd(model, NEW["input_tokens"], NEW["output_tokens"], 0, 0)
    assert cost_all > cost_live_only, "the two candidate costs must differ, or this proves nothing"

    assert team["cost_usd"] == cost_all, (
        f"team aggregate reported {team['cost_usd']}; all billed rows give {cost_all} "
        f"and filtering superseded rows gives {cost_live_only}"
    )
