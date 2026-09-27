"""G4 — item 6: CTX% per model.

The percentage itself is computed in frontend JS
(frontend/static/js/ui/right_sidebar.js:152 — `(inputTokens / ctxMax) * 100`).
The backend supplies both operands:

  * `ctxMax` per model from /api/config (app/config.py MODELS.context_window)
  * `inputTokens` from the latest assistant message with input_tokens > 0,
    served by the conversation API

This file tests the backend data the frontend depends on, not the JS math.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import conversations_service as convs

client = TestClient(app)

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-5"


def _config_windows():
    cfg = client.get("/api/config").json()
    return {m["value"]: m["context_window"] for m in cfg["models"]}


def test_config_publishes_haiku_200k_and_sonnet_opus_1m():
    windows = _config_windows()
    assert windows[HAIKU] == 200_000, f"Haiku window {windows[HAIKU]} != 200_000"
    assert windows[SONNET] == 1_000_000, f"Sonnet window {windows[SONNET]} != 1_000_000"
    assert windows[OPUS] == 1_000_000, f"Opus window {windows[OPUS]} != 1_000_000"


# Worked example per model: used = 12_345 tokens.
#   Haiku:  12345 / 200_000  * 100 = 6.1725%
#   Sonnet: 12345 / 1_000_000 * 100 = 1.2345%
#   Opus:   12345 / 1_000_000 * 100 = 1.2345%
@pytest.mark.parametrize(
    "model,expected_window,expected_pct",
    [
        (HAIKU, 200_000, 6.1725),
        (SONNET, 1_000_000, 1.2345),
        (OPUS, 1_000_000, 1.2345),
    ],
)
def test_worked_example_used_over_total_per_model(temp_db, model, expected_window, expected_pct):
    used = 12_345
    conv_id = convs.create_conversation(model=model).id
    convs.add_message(conv_id, "user", "q")
    convs.add_message(conv_id, "assistant", "a", input_tokens=used, output_tokens=1, model=model)

    api = client.get(f"/api/conversations/{conv_id}").json()
    assistant = [m for m in api["messages"] if m["role"] == "assistant"][0]
    assert assistant["input_tokens"] == used, (
        f"API reported input_tokens {assistant['input_tokens']} != {used} — the frontend's `used` operand"
    )

    window = _config_windows()[model]
    assert window == expected_window, f"{model} context_window {window} != {expected_window}"

    # The frontend computes exactly this fraction; pin the arithmetic here.
    assert used / window * 100 == pytest.approx(expected_pct, rel=1e-9)
