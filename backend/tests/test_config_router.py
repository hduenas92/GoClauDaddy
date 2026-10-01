from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_config_lists_models_and_permission_modes():
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert len(data["models"]) > 0
    assert "plan" in data["permission_modes"]
    assert data["default_model"]


def test_update_settings_rejects_invalid_permission_mode(temp_db):
    create = client.post("/api/conversations", json={})
    conv_id = create.json()["id"]
    res = client.patch(f"/api/conversations/{conv_id}/settings", json={"permission_mode": "not-a-real-mode"})
    assert res.status_code == 422


def test_update_settings_accepts_valid_permission_mode(temp_db):
    create = client.post("/api/conversations", json={})
    conv_id = create.json()["id"]
    res = client.patch(f"/api/conversations/{conv_id}/settings", json={"permission_mode": "plan"})
    assert res.status_code == 200
    assert res.json()["permission_mode"] == "plan"


def test_get_config_models_include_pricing():
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert len(data["models"]) > 0
    for model in data["models"]:
        assert "value" in model
        assert "label" in model
        assert "description" in model and model["description"]
        assert "context_window" in model
        assert "input_rate" in model
        assert "output_rate" in model
        assert isinstance(model["input_rate"], (int, float)) and model["input_rate"] > 0
        assert isinstance(model["output_rate"], (int, float)) and model["output_rate"] > 0
