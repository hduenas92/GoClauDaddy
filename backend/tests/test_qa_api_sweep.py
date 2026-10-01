"""QA §1 functional sweep for GoClaudaddy's REST API.

Route inventory was read directly from backend/app/routers/*.py and
backend/app/main.py, not from the docs.  The sweep never calls the real claude
CLI, the network, or 127.0.0.1:8765:

* /api/conversations/assess subprocess creation is replaced with a fake.
* The native directory picker is replaced with a harmless stub.
* TestClient drives the ASGI app in-process.
* DB and attachment storage are throwaway per test (temp_db + tmp_path).

The "request during an active stream" dimension is skipped in a dedicated
skipped test because it needs a running CLI turn and is covered by the WS
suites.  The infinite SSE endpoint GET /api/server/logs is also not consumed
(no bounded body exists to assert against safely with TestClient).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_MODEL
from app.db.connection import get_connection
from app.main import app
from app.routers import assess as assess_router
from app.services import attachments_service as attsvc
from app.services import conversations_service as convsvc
from app.services import dir_picker

client = TestClient(app, raise_server_exceptions=False)

PROFILE = str(Path.home())
ROOT_WORKDIR = str(Path.home().anchor)  # C:\ on Windows; never under PROFILE
SQL = "'; DROP TABLE conversations;--"
ABSURD = "x" * 100_000
FAKE_UUID = "00000000-0000-0000-0000-000000000000"
MALFORMED_UUID = "not-a-uuid"

Response = Any


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Throwaway attachment dir, fake CLI, fake directory picker per test."""
    att_dir = tmp_path / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(attsvc, "ATTACHMENTS_DIR", att_dir)
    monkeypatch.setattr(convsvc, "ATTACHMENTS_DIR", att_dir)

    monkeypatch.setattr(dir_picker, "pick_directory", lambda initial_dir=None: ROOT_WORKDIR)

    class _FakeProc:
        async def communicate(self):
            payload = {"level": "low", "summary": "fake ok", "concerns": []}
            return json.dumps({"result": json.dumps(payload)}).encode("utf-8"), b""

        def kill(self):  # pragma: no cover - only on unexpected failure
            pass

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(assess_router.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)


@dataclass
class _Ctx:
    client: TestClient
    tmp_path: Path

    def create_conversation(self, **body) -> str:
        res = self.client.post("/api/conversations", json=body)
        assert res.status_code == 200, f"setup create_conversation failed: {res.status_code} {res.text[:200]}"
        return res.json()["id"]

    def create_project(self, name: str = "Project", working_dir: str | None = None,
                       system_prompt: str | None = None) -> str:
        body = {
            "name": name,
            "working_dir": ROOT_WORKDIR if working_dir is None else working_dir,
            "system_prompt": system_prompt,
        }
        res = self.client.post("/api/projects", json=body)
        assert res.status_code == 200, f"setup create_project failed: {res.status_code} {res.text[:200]}"
        return res.json()["id"]

    def create_template(self, **overrides) -> str:
        body = {"title": "Template", "description": None, "body": "Body", "category": "cat"}
        body.update(overrides)
        res = self.client.post("/api/flow-templates", json=body)
        assert res.status_code == 200, f"setup create_template failed: {res.status_code} {res.text[:200]}"
        return res.json()["id"]

    def upload(self, conversation_id: str, *, filename: str = "qa.txt", content: bytes = b"qa",
               content_type: str = "text/plain"):
        return self.client.post(
            "/api/attachments",
            params={"conversation_id": conversation_id},
            files={"file": (filename, content, content_type)},
        )

    def add_message(self, conversation_id: str, content: str = "needle"):
        return convsvc.add_message(conversation_id, "user", content)


@dataclass(frozen=True)
class _Case:
    id: str
    kind: str
    run: Callable[[_Ctx], Response]
    check: Callable[[_Ctx, list[Response]], None] | None = None
    xfail_reason: str | None = None


def _p(route: str, dim: str, run: Callable[[_Ctx], Response], *, kind: str = "valid",
       check: Callable[[_Ctx, list[Response]], None] | None = None,
       xfail_reason: str | None = None) -> Any:
    cid = f"{route}-{dim}"
    marks = (pytest.mark.xfail(strict=True, reason=xfail_reason),) if xfail_reason else ()
    return pytest.param(_Case(cid, kind, run, check, xfail_reason), id=cid, marks=marks)


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _body_has_profile_path(resp: Response) -> bool:
    # The temp dir counts too: test data lives there, and only under the REAL profile is it also under
    # PROFILE, so checking PROFILE alone passed in a fake-HOME clone and failed on the dev machine (2026-10-01).
    roots = {PROFILE, tempfile.gettempdir()}
    variants = {v for r in roots for v in (r, r.replace("\\", "/"))}
    try:
        raw = resp.content.decode("utf-8", errors="replace")
    except Exception:
        raw = resp.text
    if any(json.dumps(r)[1:-1] in raw for r in roots):  # JSON-escaped C:\\Users\\...
        return True
    try:
        data = resp.json()
    except Exception:
        data = None
    for value in _walk_strings(data):
        if any(variant in value for variant in variants):
            return True
    return any(variant in raw for variant in variants)


def _assert_common(resp: Response) -> None:
    assert resp.status_code < 500, f"5xx: {resp.status_code} {resp.text[:300]}"
    text = resp.text
    assert "Traceback (most recent call last)" not in text, "traceback leaked in body"
    assert "sqlite3" not in text.lower(), "sqlite3 text leaked in body"
    assert not _body_has_profile_path(resp), "user-profile file path leaked in body"


def _assert_conversations_table() -> None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()
    assert row is not None, "conversations table is missing after SQL-metachar case"


def _check_conversation_stored(field: str, expected: Any):
    def check(ctx: _Ctx, responses: list[Response]) -> None:
        res = responses[0]
        assert 200 <= res.status_code < 300, f"expected 2xx, got {res.status_code}"
        conv_id = res.json()["id"]
        got = ctx.client.get(f"/api/conversations/{conv_id}").json()["conversation"][field]
        assert got == expected, f"{field!r} did not round-trip: {got!r} != {expected!r}"
    return check


def _check_project_stored(field: str, expected: Any):
    def check(ctx: _Ctx, responses: list[Response]) -> None:
        res = responses[0]
        assert 200 <= res.status_code < 300, f"expected 2xx, got {res.status_code}"
        project_id = res.json()["id"]
        rows = ctx.client.get("/api/projects").json()
        got = next(row[field] for row in rows if row["id"] == project_id)
        assert got == expected, f"{field!r} did not round-trip: {got!r} != {expected!r}"
    return check


def _check_template_stored(field: str, expected: Any):
    def check(ctx: _Ctx, responses: list[Response]) -> None:
        res = responses[0]
        assert 200 <= res.status_code < 300, f"expected 2xx, got {res.status_code}"
        template_id = res.json()["id"]
        rows = ctx.client.get("/api/flow-templates").json()
        got = next(row[field] for row in rows if row["id"] == template_id)
        assert got == expected, f"{field!r} did not round-trip: {got!r} != {expected!r}"
    return check


def _check_attachment_content(expected: bytes):
    def check(ctx: _Ctx, responses: list[Response]) -> None:
        res = responses[0]
        assert 200 <= res.status_code < 300, f"expected 2xx, got {res.status_code}"
        attachment_id = res.json()["id"]
        download = ctx.client.get(f"/api/attachments/{attachment_id}/download")
        assert download.status_code == 200
        assert download.content == expected
    return check


def _check_distinct_ids(ctx: _Ctx, responses: list[Response]) -> None:
    ids = [r.json()["id"] for r in responses]
    assert len(ids) == 2
    assert ids[0] and ids[1] and ids[0] != ids[1], "duplicate POSTs did not create distinct resources"


def _check_conversation_defaults(ctx: _Ctx, responses: list[Response]) -> None:
    body = responses[0].json()
    assert body["name"], "empty optional name should become a non-empty default"
    assert body["model"] == DEFAULT_MODEL, "empty optional model should become the default model"


def _search_with_message(ctx: _Ctx, **params) -> Response:
    conv_id = ctx.create_conversation()
    ctx.add_message(conv_id, "needle in a haystack")
    return ctx.client.get("/api/search", params=params)


def _run_download_valid(ctx: _Ctx) -> Response:
    conv_id = ctx.create_conversation()
    upload = ctx.upload(conv_id, content=b"qa")
    assert upload.status_code == 200
    return ctx.client.get(f"/api/attachments/{upload.json()['id']}/download")


def _run_delete_attachment_valid(ctx: _Ctx) -> Response:
    conv_id = ctx.create_conversation()
    upload = ctx.upload(conv_id, content=b"qa")
    assert upload.status_code == 200
    return ctx.client.delete(f"/api/attachments/{upload.json()['id']}")


def _run_auto_title_duplicate(ctx: _Ctx) -> list[Response]:
    conv_id = ctx.create_conversation()
    return [ctx.client.post(f"/api/conversations/{conv_id}/auto-title") for _ in range(2)]


def _run_attachment_duplicate(ctx: _Ctx) -> list[Response]:
    conv_id = ctx.create_conversation()
    return [ctx.upload(conv_id, content=b"qa"), ctx.upload(conv_id, content=b"qa")]


def _run_browse_duplicate(ctx: _Ctx) -> list[Response]:
    return [ctx.client.post("/api/projects/browse-directory") for _ in range(2)]


_CASES: list[Any] = [
    # ------------------------------------------------------------------ main.py
    _p("GET /api/health", "valid", lambda ctx: ctx.client.get("/api/health")),
    _p("GET /", "valid", lambda ctx: ctx.client.get("/")),

    # ------------------------------------------------------- /api/config
    _p("GET /api/config", "valid", lambda ctx: ctx.client.get("/api/config")),

    # ------------------------------------------------------- /api/server
    _p("GET /api/server/info", "valid", lambda ctx: ctx.client.get("/api/server/info")),
    _p("GET /api/server/stats", "valid", lambda ctx: ctx.client.get("/api/server/stats")),

    # ------------------------------------------------------- /api/conversations
    _p("GET /api/conversations", "valid", lambda ctx: ctx.client.get("/api/conversations")),
    _p("GET /api/conversations", "sql_filter", lambda ctx: ctx.client.get("/api/conversations", params={"project_id": SQL}), kind="sql"),
    _p("POST /api/conversations", "valid_empty", lambda ctx: ctx.client.post("/api/conversations", json={})),
    _p("POST /api/conversations", "valid_null_optionals", lambda ctx: ctx.client.post("/api/conversations", json={"name": None, "project_id": None, "model": None})),
    _p("POST /api/conversations", "empty_optionals", lambda ctx: ctx.client.post("/api/conversations", json={"name": "", "model": ""}), check=_check_conversation_defaults),
    _p("POST /api/conversations", "wrong_type_name", lambda ctx: ctx.client.post("/api/conversations", json={"name": 123}), kind="invalid"),
    _p("POST /api/conversations", "wrong_type_model", lambda ctx: ctx.client.post("/api/conversations", json={"model": 123}), kind="invalid"),
    _p("POST /api/conversations", "unknown_model", lambda ctx: ctx.client.post("/api/conversations", json={"model": "bogus"}), kind="invalid",
       xfail_reason="BUG: POST /api/conversations accepts an unknown model and stores it"),
    _p("POST /api/conversations", "nonexistent_project_id", lambda ctx: ctx.client.post("/api/conversations", json={"project_id": FAKE_UUID}), kind="not_found",
       xfail_reason="BUG: nonexistent project_id returns 500 (FOREIGN KEY) instead of 404"),
    _p("POST /api/conversations", "malformed_project_id", lambda ctx: ctx.client.post("/api/conversations", json={"project_id": MALFORMED_UUID}), kind="not_found",
       xfail_reason="BUG: malformed project_id returns 500 (FOREIGN KEY) instead of 404"),
    _p("POST /api/conversations", "empty_project_id", lambda ctx: ctx.client.post("/api/conversations", json={"project_id": ""}), kind="not_found",
       xfail_reason="BUG: empty project_id returns 500 (FOREIGN KEY) instead of 404"),
    _p("POST /api/conversations", "sql_name", lambda ctx: ctx.client.post("/api/conversations", json={"name": SQL}), kind="sql",
       check=_check_conversation_stored("name", SQL)),
    _p("POST /api/conversations", "sql_project_id", lambda ctx: ctx.client.post("/api/conversations", json={"project_id": SQL}), kind="sql",
       xfail_reason="BUG: SQL-metachar project_id returns 500 instead of 4xx/404"),
    _p("POST /api/conversations", "absurd_name", lambda ctx: ctx.client.post("/api/conversations", json={"name": ABSURD}), kind="absurd",
       check=_check_conversation_stored("name", ABSURD)),
    _p("POST /api/conversations", "duplicate", lambda ctx: [
        ctx.client.post("/api/conversations", json={"name": "dup"}),
        ctx.client.post("/api/conversations", json={"name": "dup"}),
    ], kind="duplicate", check=_check_distinct_ids),

    _p("GET /api/conversations/{id}", "valid", lambda ctx: ctx.client.get(f"/api/conversations/{ctx.create_conversation()}")),
    _p("GET /api/conversations/{id}", "nonexistent_id", lambda ctx: ctx.client.get(f"/api/conversations/{FAKE_UUID}"), kind="not_found"),
    _p("GET /api/conversations/{id}", "malformed_uuid", lambda ctx: ctx.client.get(f"/api/conversations/{MALFORMED_UUID}"), kind="not_found"),
    _p("GET /api/conversations/{id}", "sql_path", lambda ctx: ctx.client.get(f"/api/conversations/{SQL}"), kind="sql"),

    _p("PATCH /api/conversations/{id}/rename", "valid", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/rename", json={"name": "Renamed"})),
    _p("PATCH /api/conversations/{id}/rename", "missing_required", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/rename", json={}), kind="invalid"),
    _p("PATCH /api/conversations/{id}/rename", "null_name", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/rename", json={"name": None}), kind="invalid"),
    _p("PATCH /api/conversations/{id}/rename", "empty_name", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/rename", json={"name": ""}), kind="invalid",
       xfail_reason="BUG: rename accepts empty name and stores it"),
    _p("PATCH /api/conversations/{id}/rename", "wrong_type_name", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/rename", json={"name": 123}), kind="invalid"),
    _p("PATCH /api/conversations/{id}/rename", "absurd_name", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/rename", json={"name": ABSURD}), kind="absurd",
       check=_check_conversation_stored("name", ABSURD)),
    _p("PATCH /api/conversations/{id}/rename", "nonexistent_id", lambda ctx: ctx.client.patch(f"/api/conversations/{FAKE_UUID}/rename", json={"name": "x"}), kind="not_found"),
    _p("PATCH /api/conversations/{id}/rename", "malformed_uuid", lambda ctx: ctx.client.patch(f"/api/conversations/{MALFORMED_UUID}/rename", json={"name": "x"}), kind="not_found"),
    _p("PATCH /api/conversations/{id}/rename", "sql_name", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/rename", json={"name": SQL}), kind="sql",
       check=_check_conversation_stored("name", SQL)),

    _p("PATCH /api/conversations/{id}/settings", "valid", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={"system_prompt": "Be concise."})),
    _p("PATCH /api/conversations/{id}/settings", "empty_noop", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={})),
    _p("PATCH /api/conversations/{id}/settings", "null_optionals", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={"model": None, "permission_mode": None, "system_prompt": None, "thinking_budget": None, "max_tokens": None})),
    _p("PATCH /api/conversations/{id}/settings", "empty_system_prompt", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={"system_prompt": ""})),
    _p("PATCH /api/conversations/{id}/settings", "wrong_type_thinking_budget", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={"thinking_budget": "abc"}), kind="invalid"),
    _p("PATCH /api/conversations/{id}/settings", "invalid_model", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={"model": "bogus"}), kind="invalid"),
    _p("PATCH /api/conversations/{id}/settings", "absurd_system_prompt", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={"system_prompt": ABSURD}), kind="absurd",
       check=_check_conversation_stored("system_prompt", ABSURD)),
    _p("PATCH /api/conversations/{id}/settings", "nonexistent_id", lambda ctx: ctx.client.patch(f"/api/conversations/{FAKE_UUID}/settings", json={}), kind="not_found"),
    _p("PATCH /api/conversations/{id}/settings", "malformed_uuid", lambda ctx: ctx.client.patch(f"/api/conversations/{MALFORMED_UUID}/settings", json={}), kind="not_found"),
    _p("PATCH /api/conversations/{id}/settings", "sql_system_prompt", lambda ctx: ctx.client.patch(f"/api/conversations/{ctx.create_conversation()}/settings", json={"system_prompt": SQL}), kind="sql",
       check=_check_conversation_stored("system_prompt", SQL)),

    _p("POST /api/conversations/{id}/auto-title", "valid", lambda ctx: ctx.client.post(f"/api/conversations/{ctx.create_conversation()}/auto-title")),
    _p("POST /api/conversations/{id}/auto-title", "duplicate", _run_auto_title_duplicate, kind="duplicate"),
    _p("POST /api/conversations/{id}/auto-title", "nonexistent_id", lambda ctx: ctx.client.post(f"/api/conversations/{FAKE_UUID}/auto-title"), kind="not_found"),
    _p("POST /api/conversations/{id}/auto-title", "malformed_uuid", lambda ctx: ctx.client.post(f"/api/conversations/{MALFORMED_UUID}/auto-title"), kind="not_found"),
    _p("POST /api/conversations/{id}/auto-title", "sql_path", lambda ctx: ctx.client.post(f"/api/conversations/{SQL}/auto-title"), kind="sql"),

    _p("GET /api/conversations/{id}/export", "valid", lambda ctx: ctx.client.get(f"/api/conversations/{ctx.create_conversation()}/export")),
    _p("GET /api/conversations/{id}/export", "nonexistent_id", lambda ctx: ctx.client.get(f"/api/conversations/{FAKE_UUID}/export"), kind="not_found"),
    _p("GET /api/conversations/{id}/export", "malformed_uuid", lambda ctx: ctx.client.get(f"/api/conversations/{MALFORMED_UUID}/export"), kind="not_found"),
    _p("GET /api/conversations/{id}/export", "sql_path", lambda ctx: ctx.client.get(f"/api/conversations/{SQL}/export"), kind="sql"),

    _p("DELETE /api/conversations/{id}", "valid", lambda ctx: ctx.client.delete(f"/api/conversations/{ctx.create_conversation()}")),
    _p("DELETE /api/conversations/{id}", "nonexistent_id", lambda ctx: ctx.client.delete(f"/api/conversations/{FAKE_UUID}"), kind="not_found"),
    _p("DELETE /api/conversations/{id}", "malformed_uuid", lambda ctx: ctx.client.delete(f"/api/conversations/{MALFORMED_UUID}"), kind="not_found"),
    _p("DELETE /api/conversations/{id}", "sql_path", lambda ctx: ctx.client.delete(f"/api/conversations/{SQL}"), kind="sql"),

    _p("GET /api/conversations/{id}/stats", "valid", lambda ctx: ctx.client.get(f"/api/conversations/{ctx.create_conversation()}/stats")),
    _p("GET /api/conversations/{id}/stats", "nonexistent_id", lambda ctx: ctx.client.get(f"/api/conversations/{FAKE_UUID}/stats"), kind="not_found"),
    _p("GET /api/conversations/{id}/stats", "malformed_uuid", lambda ctx: ctx.client.get(f"/api/conversations/{MALFORMED_UUID}/stats"), kind="not_found"),
    _p("GET /api/conversations/{id}/stats", "sql_path", lambda ctx: ctx.client.get(f"/api/conversations/{SQL}/stats"), kind="sql"),

    # ------------------------------------------------------- assess
    _p("POST /api/conversations/assess", "valid", lambda ctx: ctx.client.post("/api/conversations/assess", json={"message": "hello"})),
    _p("POST /api/conversations/assess", "missing_required", lambda ctx: ctx.client.post("/api/conversations/assess", json={}), kind="invalid"),
    _p("POST /api/conversations/assess", "null_message", lambda ctx: ctx.client.post("/api/conversations/assess", json={"message": None}), kind="invalid"),
    _p("POST /api/conversations/assess", "empty_message", lambda ctx: ctx.client.post("/api/conversations/assess", json={"message": ""})),
    _p("POST /api/conversations/assess", "null_conversation_id", lambda ctx: ctx.client.post("/api/conversations/assess", json={"message": "hello", "conversation_id": None})),
    _p("POST /api/conversations/assess", "wrong_type_message", lambda ctx: ctx.client.post("/api/conversations/assess", json={"message": 123}), kind="invalid"),
    _p("POST /api/conversations/assess", "absurd_message", lambda ctx: ctx.client.post("/api/conversations/assess", json={"message": ABSURD}), kind="absurd"),
    _p("POST /api/conversations/assess", "sql_message", lambda ctx: ctx.client.post("/api/conversations/assess", json={"message": SQL}), kind="sql"),
    _p("POST /api/conversations/assess", "duplicate", lambda ctx: [
        ctx.client.post("/api/conversations/assess", json={"message": "hello"}),
        ctx.client.post("/api/conversations/assess", json={"message": "hello"}),
    ], kind="duplicate"),

    # ------------------------------------------------------- search
    _p("GET /api/search", "valid", lambda ctx: _search_with_message(ctx, q="needle")),
    _p("GET /api/search", "missing_required_q", lambda ctx: ctx.client.get("/api/search"), kind="invalid"),
    _p("GET /api/search", "empty_q", lambda ctx: ctx.client.get("/api/search", params={"q": ""}), kind="invalid"),
    _p("GET /api/search", "wrong_type_limit", lambda ctx: _search_with_message(ctx, q="needle", limit="abc"), kind="invalid"),
    _p("GET /api/search", "sql_q", lambda ctx: ctx.client.get("/api/search", params={"q": SQL}), kind="sql"),

    # ------------------------------------------------------- attachments
    _p("POST /api/attachments", "valid", lambda ctx: ctx.upload(ctx.create_conversation()), kind="valid",
       check=_check_attachment_content(b"qa"), xfail_reason="BUG: attachment upload returns the absolute stored_path (a local file path) to the client"),
    _p("POST /api/attachments", "missing_file", lambda ctx: ctx.client.post("/api/attachments", params={"conversation_id": ctx.create_conversation()}), kind="invalid"),
    _p("POST /api/attachments", "missing_conversation_id", lambda ctx: ctx.client.post("/api/attachments", files={"file": ("qa.txt", b"qa", "text/plain")}), kind="invalid"),
    _p("POST /api/attachments", "empty_conversation_id", lambda ctx: ctx.client.post("/api/attachments", params={"conversation_id": ""}, files={"file": ("qa.txt", b"qa", "text/plain")}), kind="not_found"),
    _p("POST /api/attachments", "nonexistent_conversation_id", lambda ctx: ctx.client.post("/api/attachments", params={"conversation_id": FAKE_UUID}, files={"file": ("qa.txt", b"qa", "text/plain")}), kind="not_found"),
    _p("POST /api/attachments", "malformed_uuid", lambda ctx: ctx.client.post("/api/attachments", params={"conversation_id": MALFORMED_UUID}, files={"file": ("qa.txt", b"qa", "text/plain")}), kind="not_found"),
    _p("POST /api/attachments", "sql_conversation_id", lambda ctx: ctx.client.post("/api/attachments", params={"conversation_id": SQL}, files={"file": ("qa.txt", b"qa", "text/plain")}), kind="sql"),
    _p("POST /api/attachments", "absurd_file", lambda ctx: ctx.upload(ctx.create_conversation(), content=ABSURD.encode()), kind="absurd",
       check=_check_attachment_content(ABSURD.encode()), xfail_reason="BUG: attachment upload returns the absolute stored_path (a local file path) to the client"),
    _p("POST /api/attachments", "duplicate", _run_attachment_duplicate, kind="duplicate",
       check=_check_distinct_ids, xfail_reason="BUG: attachment upload returns the absolute stored_path (a local file path) to the client"),

    _p("GET /api/attachments/{id}/download", "valid", _run_download_valid),
    _p("GET /api/attachments/{id}/download", "nonexistent_id", lambda ctx: ctx.client.get(f"/api/attachments/{FAKE_UUID}/download"), kind="not_found"),
    _p("GET /api/attachments/{id}/download", "malformed_uuid", lambda ctx: ctx.client.get(f"/api/attachments/{MALFORMED_UUID}/download"), kind="not_found"),
    _p("GET /api/attachments/{id}/download", "sql_path", lambda ctx: ctx.client.get(f"/api/attachments/{SQL}/download"), kind="sql"),

    _p("DELETE /api/attachments/{id}", "valid", _run_delete_attachment_valid),
    _p("DELETE /api/attachments/{id}", "nonexistent_id", lambda ctx: ctx.client.delete(f"/api/attachments/{FAKE_UUID}"), kind="not_found"),
    _p("DELETE /api/attachments/{id}", "malformed_uuid", lambda ctx: ctx.client.delete(f"/api/attachments/{MALFORMED_UUID}"), kind="not_found"),
    _p("DELETE /api/attachments/{id}", "sql_path", lambda ctx: ctx.client.delete(f"/api/attachments/{SQL}"), kind="sql"),

    # ------------------------------------------------------- flow templates
    _p("GET /api/flow-templates", "valid", lambda ctx: ctx.client.get("/api/flow-templates")),
    _p("POST /api/flow-templates", "valid", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": "T", "description": None, "body": "B", "category": "C"})),
    _p("POST /api/flow-templates", "missing_required", lambda ctx: ctx.client.post("/api/flow-templates", json={}), kind="invalid"),
    _p("POST /api/flow-templates", "null_title", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": None, "description": None, "body": "B", "category": "C"}), kind="invalid"),
    _p("POST /api/flow-templates", "empty_title", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": "", "description": None, "body": "B", "category": "C"}), kind="invalid",
       xfail_reason="BUG: create flow template accepts empty title and stores it"),
    _p("POST /api/flow-templates", "empty_body", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": "T", "description": None, "body": "", "category": "C"}), kind="invalid",
       xfail_reason="BUG: create flow template accepts empty body and stores it"),
    _p("POST /api/flow-templates", "empty_category", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": "T", "description": None, "body": "B", "category": ""}), kind="invalid",
       xfail_reason="BUG: create flow template accepts empty category and stores it"),
    _p("POST /api/flow-templates", "wrong_type_title", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": 123, "description": None, "body": "B", "category": "C"}), kind="invalid"),
    _p("POST /api/flow-templates", "absurd_body", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": "T", "description": None, "body": ABSURD, "category": "C"}), kind="absurd",
       check=_check_template_stored("body", ABSURD)),
    _p("POST /api/flow-templates", "sql_title", lambda ctx: ctx.client.post("/api/flow-templates", json={"title": SQL, "description": None, "body": "B", "category": "C"}), kind="sql",
       check=_check_template_stored("title", SQL)),
    _p("POST /api/flow-templates", "duplicate", lambda ctx: [
        ctx.client.post("/api/flow-templates", json={"title": "T", "description": None, "body": "B", "category": "C"}),
        ctx.client.post("/api/flow-templates", json={"title": "T", "description": None, "body": "B", "category": "C"}),
    ], kind="duplicate", check=_check_distinct_ids),

    _p("PUT /api/flow-templates/{id}", "valid", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={"title": "Updated", "description": "d", "body": "B2", "category": "C2"})),
    _p("PUT /api/flow-templates/{id}", "missing_required", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={}), kind="invalid"),
    _p("PUT /api/flow-templates/{id}", "null_title", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={"title": None, "description": None, "body": "B", "category": "C"}), kind="invalid"),
    _p("PUT /api/flow-templates/{id}", "empty_title", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={"title": "", "description": None, "body": "B", "category": "C"}), kind="invalid",
       xfail_reason="BUG: update flow template accepts empty title and stores it"),
    _p("PUT /api/flow-templates/{id}", "empty_body", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={"title": "T", "description": None, "body": "", "category": "C"}), kind="invalid",
       xfail_reason="BUG: update flow template accepts empty body and stores it"),
    _p("PUT /api/flow-templates/{id}", "empty_category", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={"title": "T", "description": None, "body": "B", "category": ""}), kind="invalid",
       xfail_reason="BUG: update flow template accepts empty category and stores it"),
    _p("PUT /api/flow-templates/{id}", "wrong_type_body", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={"title": "T", "description": None, "body": 123, "category": "C"}), kind="invalid"),
    _p("PUT /api/flow-templates/{id}", "absurd_body", lambda ctx: ctx.client.put(f"/api/flow-templates/{ctx.create_template()}", json={"title": "T", "description": None, "body": ABSURD, "category": "C"}), kind="absurd",
       check=_check_template_stored("body", ABSURD)),
    _p("PUT /api/flow-templates/{id}", "nonexistent_id", lambda ctx: ctx.client.put(f"/api/flow-templates/{FAKE_UUID}", json={"title": "T", "description": None, "body": "B", "category": "C"}), kind="not_found"),
    _p("PUT /api/flow-templates/{id}", "malformed_uuid", lambda ctx: ctx.client.put(f"/api/flow-templates/{MALFORMED_UUID}", json={"title": "T", "description": None, "body": "B", "category": "C"}), kind="not_found"),
    _p("PUT /api/flow-templates/{id}", "sql_path", lambda ctx: ctx.client.put(f"/api/flow-templates/{SQL}", json={"title": "T", "description": None, "body": "B", "category": "C"}), kind="sql"),

    _p("DELETE /api/flow-templates/{id}", "valid", lambda ctx: ctx.client.delete(f"/api/flow-templates/{ctx.create_template()}")),
    _p("DELETE /api/flow-templates/{id}", "nonexistent_id", lambda ctx: ctx.client.delete(f"/api/flow-templates/{FAKE_UUID}"), kind="not_found"),
    _p("DELETE /api/flow-templates/{id}", "malformed_uuid", lambda ctx: ctx.client.delete(f"/api/flow-templates/{MALFORMED_UUID}"), kind="not_found"),
    _p("DELETE /api/flow-templates/{id}", "sql_path", lambda ctx: ctx.client.delete(f"/api/flow-templates/{SQL}"), kind="sql"),

    # ------------------------------------------------------- projects
    _p("GET /api/projects", "valid", lambda ctx: ctx.client.get("/api/projects")),
    _p("POST /api/projects", "valid", lambda ctx: ctx.client.post("/api/projects", json={"name": "P", "working_dir": ROOT_WORKDIR})),
    _p("POST /api/projects", "missing_required_name", lambda ctx: ctx.client.post("/api/projects", json={"working_dir": ROOT_WORKDIR}), kind="invalid"),
    _p("POST /api/projects", "null_name", lambda ctx: ctx.client.post("/api/projects", json={"name": None, "working_dir": ROOT_WORKDIR}), kind="invalid"),
    _p("POST /api/projects", "empty_name", lambda ctx: ctx.client.post("/api/projects", json={"name": "", "working_dir": ROOT_WORKDIR}), kind="invalid",
       xfail_reason="BUG: create project accepts empty name and stores it"),
    _p("POST /api/projects", "wrong_type_name", lambda ctx: ctx.client.post("/api/projects", json={"name": 123, "working_dir": ROOT_WORKDIR}), kind="invalid"),
    _p("POST /api/projects", "wrong_type_working_dir", lambda ctx: ctx.client.post("/api/projects", json={"name": "P", "working_dir": 123}), kind="invalid"),
    _p("POST /api/projects", "nonexistent_working_dir", lambda ctx: ctx.client.post("/api/projects", json={"name": "P", "working_dir": "/no/such/dir"}), kind="invalid"),
    _p("POST /api/projects", "absurd_name", lambda ctx: ctx.client.post("/api/projects", json={"name": ABSURD, "working_dir": ROOT_WORKDIR}), kind="absurd",
       check=_check_project_stored("name", ABSURD)),
    _p("POST /api/projects", "sql_name", lambda ctx: ctx.client.post("/api/projects", json={"name": SQL, "working_dir": ROOT_WORKDIR}), kind="sql",
       check=_check_project_stored("name", SQL)),
    _p("POST /api/projects", "duplicate", lambda ctx: [
        ctx.client.post("/api/projects", json={"name": "P", "working_dir": ROOT_WORKDIR}),
        ctx.client.post("/api/projects", json={"name": "P", "working_dir": ROOT_WORKDIR}),
    ], kind="duplicate", check=_check_distinct_ids),

    _p("PATCH /api/projects/{id}", "valid", lambda ctx: ctx.client.patch(f"/api/projects/{ctx.create_project()}", json={"name": "Renamed"})),
    _p("PATCH /api/projects/{id}", "empty_noop", lambda ctx: ctx.client.patch(f"/api/projects/{ctx.create_project()}", json={})),
    _p("PATCH /api/projects/{id}", "null_name", lambda ctx: ctx.client.patch(f"/api/projects/{ctx.create_project()}", json={"name": None})),
    _p("PATCH /api/projects/{id}", "empty_name", lambda ctx: ctx.client.patch(f"/api/projects/{ctx.create_project()}", json={"name": ""}), kind="invalid",
       xfail_reason="BUG: update project accepts empty name and stores it"),
    _p("PATCH /api/projects/{id}", "wrong_type_name", lambda ctx: ctx.client.patch(f"/api/projects/{ctx.create_project()}", json={"name": 123}), kind="invalid"),
    _p("PATCH /api/projects/{id}", "absurd_name", lambda ctx: ctx.client.patch(f"/api/projects/{ctx.create_project()}", json={"name": ABSURD}), kind="absurd",
       check=_check_project_stored("name", ABSURD)),
    _p("PATCH /api/projects/{id}", "nonexistent_id", lambda ctx: ctx.client.patch(f"/api/projects/{FAKE_UUID}", json={"name": "x"}), kind="not_found"),
    _p("PATCH /api/projects/{id}", "malformed_uuid", lambda ctx: ctx.client.patch(f"/api/projects/{MALFORMED_UUID}", json={"name": "x"}), kind="not_found"),
    _p("PATCH /api/projects/{id}", "sql_name", lambda ctx: ctx.client.patch(f"/api/projects/{ctx.create_project()}", json={"name": SQL}), kind="sql",
       check=_check_project_stored("name", SQL)),

    _p("DELETE /api/projects/{id}", "valid", lambda ctx: ctx.client.delete(f"/api/projects/{ctx.create_project()}")),
    _p("DELETE /api/projects/{id}", "nonexistent_id", lambda ctx: ctx.client.delete(f"/api/projects/{FAKE_UUID}"), kind="not_found"),
    _p("DELETE /api/projects/{id}", "malformed_uuid", lambda ctx: ctx.client.delete(f"/api/projects/{MALFORMED_UUID}"), kind="not_found"),
    _p("DELETE /api/projects/{id}", "sql_path", lambda ctx: ctx.client.delete(f"/api/projects/{SQL}"), kind="sql"),

    _p("POST /api/projects/browse-directory", "valid", lambda ctx: ctx.client.post("/api/projects/browse-directory")),
    _p("POST /api/projects/browse-directory", "empty_initial_dir", lambda ctx: ctx.client.post("/api/projects/browse-directory", params={"initial_dir": ""})),
    _p("POST /api/projects/browse-directory", "sql_initial_dir", lambda ctx: ctx.client.post("/api/projects/browse-directory", params={"initial_dir": SQL}), kind="sql"),
    _p("POST /api/projects/browse-directory", "duplicate", _run_browse_duplicate, kind="duplicate"),
]


def test_request_during_active_stream_is_not_swept():
    pytest.skip("request during an active stream needs the CLI and is covered by the WebSocket suites")


def _assert_status_shape(case: _Case, responses: list[Response]) -> None:
    statuses = [r.status_code for r in responses]
    if case.kind == "valid":
        assert all(200 <= status < 300 for status in statuses), f"expected 2xx, got {statuses}"
    elif case.kind == "invalid":
        assert all(400 <= status < 500 for status in statuses), f"expected 4xx, got {statuses}"
    elif case.kind == "not_found":
        assert all(status == 404 for status in statuses), f"expected 404, got {statuses}"
    elif case.kind == "duplicate":
        assert all(200 <= status < 300 for status in statuses), f"expected 2xx for duplicate POSTs, got {statuses}"
    else:  # absurd / sql may be accepted or 4xx-rejected, but never 5xx
        assert all(status < 500 for status in statuses), f"5xx seen: {statuses}"


@pytest.mark.parametrize("case", _CASES)
def test_rest_route_dimension(case: _Case, temp_db, tmp_path: Path):
    ctx = _Ctx(client, tmp_path)
    result = case.run(ctx)
    responses = result if isinstance(result, list) else [result]

    if case.kind == "sql":
        _assert_conversations_table()
    _assert_status_shape(case, responses)
    if case.check is not None:
        case.check(ctx, responses)
    for resp in responses:
        _assert_common(resp)







@pytest.mark.skip(reason='GET /api/server/logs is an unbounded SSE stream; TestClient cannot safely consume a bounded body')
def test_rest_route_server_logs_is_not_consumed():
    pass
