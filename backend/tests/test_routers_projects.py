from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services import dir_picker

client = TestClient(app)

_HOME = str(Path.home())


def test_create_project_returns_the_created_row(temp_db):
    res = client.post("/api/projects", json={"name": "Proj", "working_dir": _HOME})
    assert res.status_code == 200
    proj = res.json()
    assert proj["name"] == "Proj"
    assert proj["working_dir"] == _HOME


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
    assert client.patch(f"/api/projects/{fake}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/api/projects/{fake}").status_code == 404


def test_delete_project_nulls_conversation_project_id(temp_db):
    proj_id = client.post("/api/projects", json={"name": "P", "working_dir": _HOME}).json()["id"]
    conv_id = client.post("/api/conversations", json={"project_id": proj_id}).json()["id"]

    client.delete(f"/api/projects/{proj_id}")

    conv = client.get(f"/api/conversations/{conv_id}").json()["conversation"]
    assert conv["project_id"] is None


# ---------------------------------------------------------------------------
# POST /api/projects/browse-directory  (task 4-D6)
#
# NOTHING here opens a real dialog. pick_directory blocks on a native OS window
# until a human clicks it; a test that called it for real would hang forever on
# a desktop and raise TclError on CI. The picker is monkeypatched in every case
# and the assertions are about the ROUTER's contract, which is all the router
# is responsible for.
# ---------------------------------------------------------------------------


def test_browse_directory_unavailable_is_503_not_500(temp_db, monkeypatch):
    """A machine with no display must fail by name, not as a generic 500.

    Before 4-D6 the TclError escaped to the global handler, so the user was
    told "Something went wrong. Check the logs folder for details." — sending
    whoever installs this on a headless box hunting a fault that does not
    exist. The status code is the part that carries the meaning: 503 says the
    mechanism is unavailable here, 500 says the server is broken.
    """

    def _no_display(initial_dir=None):
        raise dir_picker.DirPickerUnavailable("no display here")

    monkeypatch.setattr(dir_picker, "pick_directory", _no_display)

    res = client.post("/api/projects/browse-directory")
    assert res.status_code == 503, f"expected 503, got {res.status_code}: {res.text}"
    # And the reason must survive to the body — an empty 503 is no more
    # diagnosable than the 500 it replaced.
    assert "no display here" in res.text


def test_browse_directory_cancel_returns_empty_string(temp_db, monkeypatch):
    """Cancel is "" and stays "" — not null, not an error, not a 404.

    The client's guard against blanking a path the user already had is written
    against exactly this shape, so the shape is pinned here.
    """
    monkeypatch.setattr(dir_picker, "pick_directory", lambda initial_dir=None: "")

    res = client.post("/api/projects/browse-directory")
    assert res.status_code == 200
    assert res.json() == {"path": ""}


def test_browse_directory_forwards_initial_dir(temp_db, monkeypatch):
    """?initial_dir= reaches the picker verbatim, and its pick comes back.

    Asserting on the captured argument rather than only on the response is what
    makes this case capable of failing: a router that ignored the query string
    entirely would still return a path.
    """
    seen = {}

    def _capture(initial_dir=None):
        seen["initial_dir"] = initial_dir
        return _HOME

    monkeypatch.setattr(dir_picker, "pick_directory", _capture)

    res = client.post(f"/api/projects/browse-directory?initial_dir={_HOME}")
    assert res.status_code == 200
    assert res.json() == {"path": _HOME}
    assert seen["initial_dir"] == _HOME
