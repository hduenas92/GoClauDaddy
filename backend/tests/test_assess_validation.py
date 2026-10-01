"""Gate 2026-10-01 (xss-check case 4): the Assess dialog put the model's `level` into
innerHTML unescaped. Fixed on both sides: the router only returns a known level and
string fields, and the composer allow-lists + escapes before rendering."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import assess as assess_router

COMPOSER = Path(__file__).resolve().parents[2] / "frontend" / "static" / "js" / "ui" / "composer.js"
XSS = '"><img src=x onerror="window.__xss=1">'


def _fake_cli(monkeypatch, payload):
    class _Proc:
        async def communicate(self):
            return json.dumps({"result": json.dumps(payload)}).encode("utf-8"), b""

        def kill(self):
            pass

    async def _exec(*a, **k):
        return _Proc()

    monkeypatch.setattr(assess_router.asyncio, "create_subprocess_exec", _exec)


def _assess(monkeypatch, payload):
    _fake_cli(monkeypatch, payload)
    return TestClient(app).post("/api/conversations/assess", json={"message": "drop the prod table"}).json()


@pytest.mark.parametrize("payload", [
    {"level": XSS, "summary": "s", "concerns": []},
    {"level": "HIGH", "summary": "s", "concerns": []},
    {"level": None, "summary": "s", "concerns": []},
    ["not", "an", "object"],
])
def test_unknown_level_or_shape_falls_back(monkeypatch, payload):
    assert _assess(monkeypatch, payload) == assess_router._FALLBACK


def test_known_level_is_normalized(monkeypatch):
    out = _assess(monkeypatch, {"level": "high", "summary": 7, "concerns": ["a", 2], "extra": XSS})
    assert out == {"level": "high", "summary": "7", "concerns": ["a", "2"]}


def test_non_list_concerns_become_empty(monkeypatch):
    out = _assess(monkeypatch, {"level": "medium", "summary": "s", "concerns": XSS})
    assert out["concerns"] == []


def test_composer_allow_lists_and_escapes_level():
    s = COMPOSER.read_text(encoding="utf-8")
    assert '["low", "medium", "high"].includes(level)' in s
    assert "${_escHtml(lvl[0].toUpperCase() + lvl.slice(1))} risk" in s
