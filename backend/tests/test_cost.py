"""Cost arithmetic and the published rate table.

Why this file exists: `compute_cost_usd` had no test at all, and the only
config-router test asserted `len(models) > 0` without checking a single rate —
so silently changing Haiku from $1.00 to $0.80 per MTok passed green. These
figures are billed against a real CaaS budget, so they are pinned here on
purpose: if someone edits config.MODELS, this file fails and says so.

Rates are $/MTok. Cache is priced off the input rate: reads at 0.10x,
creations at 1.25x.
"""

import pytest

from app.config import MODELS
from app.services.cost import compute_cost_usd

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-4-5-20251001"

# (id, input_rate, output_rate, context_window)
EXPECTED_MODELS = [
    (SONNET, 2.00, 10.00, 1_000_000),
    (OPUS, 4.00, 20.00, 1_000_000),
    (HAIKU, 1.00, 5.00, 200_000),
]


# --- the rate table itself -------------------------------------------------

def test_model_table_has_exactly_the_expected_models():
    assert [m["id"] for m in MODELS] == [m[0] for m in EXPECTED_MODELS]


@pytest.mark.parametrize("model_id,input_rate,output_rate,ctx", EXPECTED_MODELS)
def test_published_rates_and_context_windows(model_id, input_rate, output_rate, ctx):
    entry = next(m for m in MODELS if m["id"] == model_id)
    assert entry["input_rate"] == input_rate
    assert entry["output_rate"] == output_rate
    assert entry["context_window"] == ctx


@pytest.mark.parametrize("field", ["id", "label", "description", "context_window", "input_rate", "output_rate"])
def test_every_model_carries_every_field(field):
    for m in MODELS:
        assert field in m, f"{m.get('id', m)} missing {field}"


# --- input/output arithmetic ----------------------------------------------

def test_zero_tokens_costs_nothing():
    assert compute_cost_usd(HAIKU, 0, 0) == 0.0


def test_haiku_1000_in_100_out():
    # 1000/1e6*1.00 + 100/1e6*5.00 = 0.0010 + 0.0005
    assert compute_cost_usd(HAIKU, 1000, 100) == pytest.approx(0.0015, abs=1e-9)


def test_sonnet_1000_in_100_out():
    # 1000/1e6*2.00 + 100/1e6*10.00 = 0.0020 + 0.0010
    assert compute_cost_usd(SONNET, 1000, 100) == pytest.approx(0.0030, abs=1e-9)


def test_opus_1000_in_100_out():
    # 1000/1e6*4.00 + 100/1e6*20.00 = 0.0040 + 0.0020
    assert compute_cost_usd(OPUS, 1000, 100) == pytest.approx(0.0060, abs=1e-9)


def test_one_million_input_tokens_equals_the_input_rate():
    for model_id, input_rate, _, _ in EXPECTED_MODELS:
        assert compute_cost_usd(model_id, 1_000_000, 0) == pytest.approx(input_rate, abs=1e-9)


def test_one_million_output_tokens_equals_the_output_rate():
    for model_id, _, output_rate, _ in EXPECTED_MODELS:
        assert compute_cost_usd(model_id, 0, 1_000_000) == pytest.approx(output_rate, abs=1e-9)


# --- cache pricing --------------------------------------------------------

def test_cache_read_is_ten_percent_of_input_rate():
    for model_id, input_rate, _, _ in EXPECTED_MODELS:
        got = compute_cost_usd(model_id, 0, 0, cache_read=1_000_000)
        assert got == pytest.approx(input_rate * 0.1, abs=1e-9)


def test_cache_creation_is_125_percent_of_input_rate():
    for model_id, input_rate, _, _ in EXPECTED_MODELS:
        got = compute_cost_usd(model_id, 0, 0, cache_creation=1_000_000)
        assert got == pytest.approx(input_rate * 1.25, abs=1e-9)


def test_cache_read_is_cheaper_than_fresh_input():
    """The whole point of caching. If this inverts, pricing is wrong."""
    fresh = compute_cost_usd(HAIKU, 1_000_000, 0)
    cached = compute_cost_usd(HAIKU, 0, 0, cache_read=1_000_000)
    assert cached < fresh


def test_cache_creation_is_more_expensive_than_fresh_input():
    fresh = compute_cost_usd(HAIKU, 1_000_000, 0)
    created = compute_cost_usd(HAIKU, 0, 0, cache_creation=1_000_000)
    assert created > fresh


def test_all_four_token_kinds_sum():
    # Haiku: in 1.00, out 5.00, cache_read 0.10, cache_creation 1.25
    got = compute_cost_usd(HAIKU, 1_000_000, 1_000_000, cache_read=1_000_000, cache_creation=1_000_000)
    assert got == pytest.approx(1.00 + 5.00 + 0.10 + 1.25, abs=1e-9)


def test_cache_defaults_to_zero_when_omitted():
    assert compute_cost_usd(HAIKU, 1000, 100) == compute_cost_usd(HAIKU, 1000, 100, 0, 0)


# --- unknown / missing model ---------------------------------------------

def test_unknown_model_falls_back_to_sonnet_rates():
    assert compute_cost_usd("no-such-model", 1_000_000, 0) == pytest.approx(3.00, abs=1e-9)


@pytest.mark.parametrize("bad", ["", None])
def test_empty_model_does_not_raise(bad):
    assert compute_cost_usd(bad, 1000, 100) == pytest.approx(0.0045, abs=1e-9)


# --- shape ----------------------------------------------------------------

def test_result_is_rounded_to_six_places():
    got = compute_cost_usd(HAIKU, 1, 1)
    assert got == round(got, 6)


def test_cost_is_never_negative():
    assert compute_cost_usd(HAIKU, 0, 0, 0, 0) >= 0.0
