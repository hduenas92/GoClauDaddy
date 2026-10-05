from fastapi import APIRouter

from app.config import MODELS, PERMISSION_MODES, cli_default_model

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config():
    return {
        "models": [
            {
                "value": m["id"],
                "label": m["label"],
                "description": m["description"],
                "context_window": m["context_window"],
                "input_rate": m["input_rate"],
                "output_rate": m["output_rate"],
            }
            for m in MODELS
        ],
        "default_model": cli_default_model(),
        "permission_modes": list(PERMISSION_MODES),
    }
