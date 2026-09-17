from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_HOME = str(Path.home())


def test_create_and_get(temp_db):
    res = client.post("/api/projects", json={"name": "Proj", "working_dir": _HOME})
    assert res.status_code == 200
    proj = res.json()
    assert proj["name"] == "Proj"
    assert proj["working_dir"] == _HOME

    res = client.get(f"/api/projects/{proj['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == proj["id"]


def test_patch_name(temp_db):
    proj_id = client.post("/api/projects", json={"name": "Old", "working_dir": _HOME}).json()["id"]
    res = client.patch(f"/api/projects/{proj_id}", json={"name": "New"})
    assert res.status_code == 200
    assert res.json()["name"] == "New"
    # Other fields unchanged
    assert res.json()["working_dir"] == _HOME


def test_patch_system_prompt(temp_db):
    proj_id = client.post("/api/projects", json={"name": "P", "working_dir": _HOME}).json()["id"]
    res = client.patch(f"/api/projects/{proj_id}", json={"system_prompt": "You are helpful."})
    assert res.status_code == 200
    assert res.json()["system_prompt"] == "You are helpful."
    # name unchanged
    assert res.json()["name"] == "P"


def test_patch_invalid_working_dir_returns_400(temp_db):
    proj_id = client.post("/api/projects", json={"name": "P", "working_dir": _HOME}).json()["id"]
    res = client.patch(f"/api/projects/{proj_id}", json={"working_dir": "/this/path/does/not/exist/xyz"})
    assert res.status_code == 400


def test_404_on_unknown_project(temp_db):
    fake = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/projects/{fake}").status_code == 404
    assert client.patch(f"/api/projects/{fake}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/projects/{fake}").status_code == 404


def test_delete_project_nulls_conversation_project_id(temp_db):
    proj_id = client.post("/api/projects", json={"name": "P", "working_dir": _HOME}).json()["id"]
    conv_id = client.post("/api/conversations", json={"project_id": proj_id}).json()["id"]

    client.delete(f"/api/projects/{proj_id}")

    conv = client.get(f"/api/conversations/{conv_id}").json()["conversation"]
    assert conv["project_id"] is None
