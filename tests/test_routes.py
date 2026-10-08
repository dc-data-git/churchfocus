"""API route smoke tests (T6.1)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    from app import db
    from app.main import app

    db.init()
    return TestClient(app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_serves_chat(client):
    r = client.get("/v1")
    assert r.status_code == 200
    assert "ChurchFocus" in r.text
    assert "chat" in r.text.lower() or "coming from" in r.text.lower() or "drive" in r.text.lower()


def test_chat_roundtrip(client, monkeypatch):
    from tests.fakes import FakeLLM

    fake = FakeLLM(
        {
            "interviewer": {"say": "Where from?", "options": None, "updates": [], "raised": [], "skip_rest": False},
            "crisis_check": {"escalate": False, "kind": "none", "reason": "ok"},
            "readback": {"text": "Looks good?"},
        }
    )
    monkeypatch.setattr("app.llm.complete_json", fake.complete_json)

    start = client.get("/v1")
    assert start.status_code == 200
    # Pull session from page
    import re

    m = re.search(r'name="session_id"[^>]*value="([^"]+)"', start.text)
    assert m
    sid = m.group(1)
    r = client.post("/v1/api/chat", json={"session_id": sid, "text": "Hesston, KS, 15 miles"})
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == sid
    assert data["say"]


def test_log_endpoint(client):
    r = client.get("/api/log/nosuch")
    assert r.status_code == 200
    assert r.json() == []
