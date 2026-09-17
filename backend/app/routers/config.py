from fastapi import APIRouter

from app.config import DEFAULT_MODEL, MODELS, PERMISSION_MODES

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config():
    return {
        "models": [{"value": m["id"], "label": m["label"], "context_window": m["context_window"], "input_rate": m["input_rate"], "output_rate": m["output_rate"]} for m in MODELS],
        "default_model": DEFAULT_MODEL,
        "permission_modes": list(PERMISSION_MODES),
    }
