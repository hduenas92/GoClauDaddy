from fastapi import APIRouter

from app.config import DEFAULT_MODEL, MODELS, PERMISSION_MODES

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config():
    return {
        "models": [{"value": v, "label": label} for v, label in MODELS],
        "default_model": DEFAULT_MODEL,
        "permission_modes": list(PERMISSION_MODES),
    }
