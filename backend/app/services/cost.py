"""Shared cost calculation to prevent divergence between list and stats endpoints."""

from app.config import MODELS as _MODELS

_RATES: dict[str, tuple[float, float]] = {
    m["id"]: (m["input_rate"], m["output_rate"]) for m in _MODELS
}
_DEFAULT = (3.00, 15.00)


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> float:
    in_rate, out_rate = _RATES.get(model or "", _DEFAULT)
    cost = input_tokens / 1_000_000 * in_rate
    cost += output_tokens / 1_000_000 * out_rate
    cost += cache_read / 1_000_000 * in_rate * 0.1
    cost += cache_creation / 1_000_000 * in_rate * 1.25
    return round(cost, 6)
