"""Catalog ids match the gateway, and app cost matches the CLI's own cost (telemetry must match the CLI).

Measured 2026-10-02 with `claude -p ... --output-format json` through the gateway: the CLI reports
modelUsage keyed by the same ids as config.MODELS and a costUSD per model. compute_cost_usd must
reproduce those costs from the same token counts (to 6 decimals).
Gateway facts: claude-sonnet-4-6 and claude-opus-4-5 return HTTP 400 there, so they must not be offered.
"""
import pytest

from app import config
from app.services.cost import compute_cost_usd

CLI_MEASURED = [
    # model id, input, output, cache_read, cache_creation, CLI costUSD
    ("claude-sonnet-5-5", 2, 29, 57891, 26271, 0.0775497),
    ("claude-opus-5-5", 2, 145, 0, 84200, 0.423908),
    ("claude-haiku-4-5-20251001", 10, 115, 0, 34913, 0.04422625),
]


def test_catalog_ids_and_default():
    assert [m["id"] for m in config.MODELS] == ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"]
    assert config.DEFAULT_MODEL == "claude-sonnet-5-5"
    assert config.ASSESS_MODEL == "claude-haiku-4-5-20251001"


def test_no_gateway_rejected_ids_offered():
    ids = {m["id"] for m in config.MODELS} | {config.DEFAULT_MODEL, config.ASSESS_MODEL}
    assert not ids & {"claude-sonnet-4-6", "claude-opus-4-5"}


@pytest.mark.parametrize("model_id,i,o,cr,cc,cli_cost", CLI_MEASURED)
def test_app_cost_equals_cli_cost(model_id, i, o, cr, cc, cli_cost):
    assert compute_cost_usd(model_id, i, o, cr, cc) == pytest.approx(cli_cost, abs=1e-6)
