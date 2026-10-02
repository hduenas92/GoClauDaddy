"""Cost must be priced by the model each message was GENERATED under.

WHY THIS FILE EXISTS. `cost.py` says, in its first line, that it is shared
"to prevent divergence between list and stats endpoints". It succeeded — there
is one formula. The divergence moved into its INPUTS: three call sites passed
`c.model`, the conversation's *current* model, while `teams.py` passed
`m.model`, the model the message was actually generated under.

So switching a conversation's model retroactively repriced every earlier
message. Haiku -> Opus is 4x input and 4x output ($1/$5 -> $4/$20 per million):
the number on screen moved without a single token being spent.

The invariant below is the one that catches it, and it is deliberately stated
as "the total does not change" rather than as an expected dollar figure — an
assertion on a hardcoded number would have to be rewritten every time the rate
card moves, and would then be rewritten to whatever the code currently emits.

Mutation-verified: reverting any of the four sites to `c.model` fails the
matching case here.
"""

from fastapi.testclient import TestClient

from app.config import MODELS
from app.main import app
from app.services import conversations_service as convs

client = TestClient(app)

HAIKU = "claude-haiku-4-5-20251001"   # $1 / $5 per million
OPUS = MODELS[1]["id"]                # Opus 5.5: $4 / $20 per million


def _conv_with_a_haiku_turn(temp_db):
    """A conversation whose one assistant message was generated on Haiku."""
    conv = convs.create_conversation(name="pricing", model=HAIKU)
    convs.add_message(conv.id, "user", "q")
    convs.add_message(
        conv.id, "assistant", "a",
        input_tokens=1_000_000, output_tokens=1_000_000, model=HAIKU,
    )
    return conv.id


def _list_cost(conv_id):
    for row in client.get("/api/conversations").json():
        if row["id"] == conv_id:
            return row["cost_usd"]
    raise AssertionError("conversation missing from the list endpoint")


def test_the_fixture_actually_costs_something(temp_db):
    """Guard first. Every assertion below is 'the number does not change', and
    0.0 == 0.0 satisfies all of them."""
    conv_id = _conv_with_a_haiku_turn(temp_db)
    assert _list_cost(conv_id) > 0, "no cost recorded; the rest of this file proves nothing"


def test_list_endpoint_keeps_the_generating_model(temp_db):
    conv_id = _conv_with_a_haiku_turn(temp_db)
    before = _list_cost(conv_id)
    client.patch(f"/api/conversations/{conv_id}/settings", json={"model": OPUS})
    after = _list_cost(conv_id)
    assert after == before, (
        f"switching the conversation to Opus repriced an existing Haiku message: "
        f"{before} -> {after}"
    )


def test_stats_endpoint_keeps_the_generating_model(temp_db):
    conv_id = _conv_with_a_haiku_turn(temp_db)
    before = client.get(f"/api/conversations/{conv_id}/stats").json()["cost_usd"]
    client.patch(f"/api/conversations/{conv_id}/settings", json={"model": OPUS})
    after = client.get(f"/api/conversations/{conv_id}/stats").json()["cost_usd"]
    assert after == before, f"stats panel repriced on model switch: {before} -> {after}"


def test_server_stats_keeps_the_generating_model(temp_db):
    conv_id = _conv_with_a_haiku_turn(temp_db)
    before = client.get("/api/server/stats").json()["monthly_cost_usd"]
    assert before > 0, "server stats reported no spend; this case would pass vacuously"
    client.patch(f"/api/conversations/{conv_id}/settings", json={"model": OPUS})
    after = client.get("/api/server/stats").json()["monthly_cost_usd"]
    assert after == before, f"right-sidebar COST repriced on model switch: {before} -> {after}"


def test_the_four_sites_agree_with_each_other(temp_db):
    """The original defect was three sites saying one thing and one saying
    another. Pin the agreement, not just each site's stability."""
    conv_id = _conv_with_a_haiku_turn(temp_db)
    listed = _list_cost(conv_id)
    stats = client.get(f"/api/conversations/{conv_id}/stats").json()["cost_usd"]
    server = client.get("/api/server/stats").json()["monthly_cost_usd"]
    assert listed == stats, f"list {listed} != stats {stats}"
    assert abs(server - listed) < 1e-6, f"server total {server} != this conversation's {listed}"
