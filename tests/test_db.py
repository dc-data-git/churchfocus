import json
from datetime import datetime, timezone

import pytest

from app.db import (
    add_evidence,
    cache_get,
    cache_put,
    create_job,
    get_evidence,
    get_job,
    get_profile,
    init,
    save_profile,
    update_job,
    upsert_church,
)
from app.log import read_session, write_step
from app.models import Evidence, Preference, PreferenceProfile, StepLog


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    init()
    yield tmp_path
    get_settings.cache_clear()


def test_profile_round_trip(data_dir):
    p = PreferenceProfile(
        session_id="s1",
        origin={"text": "Hesston, KS"},
        preferences=[Preference(feature="logistics.distance", want=["15"], weight="important")],
    )
    save_profile(p)
    got = get_profile("s1")
    assert got is not None
    assert got.session_id == "s1"
    assert got.preferences[0].feature == "logistics.distance"


def test_church_and_evidence(data_dir):
    upsert_church("place123", "https://example.church")
    ev = Evidence(
        feature="theology.baptism",
        value="infant_and_adult",
        tier="A",
        quote="We baptize infants and adults.",
        url="https://example.church/beliefs",
        source_kind="website",
        how="stated",
        checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    add_evidence("place123", [ev])
    rows = get_evidence("place123")
    assert len(rows) == 1
    assert rows[0].value == "infant_and_adult"


def test_jobs(data_dir):
    create_job("j1", "s1", "place123")
    update_job("j1", status="running", progress=0.5)
    job = get_job("j1")
    assert job["status"] == "running"
    assert job["progress"] == 0.5


def test_cache(data_dir):
    cache_put("https://example.com", '{"html":"<p>hi</p>"}')
    got = cache_get("https://example.com")
    assert got is not None
    fetched_at, body = got
    assert "hi" in body
    assert fetched_at.tzinfo is not None


def test_write_step_appends_jsonl(data_dir):
    step = StepLog(
        session_id="s1",
        church_id="place123",
        stage=2,
        step=1,
        action="fetch_page",
        why="check beliefs page",
        input={"url": "https://example.church/beliefs"},
        result_summary="found baptism statement",
    )
    write_step(step)
    rows = read_session("s1")
    assert len(rows) == 1
    assert rows[0]["action"] == "fetch_page"
    assert "google" not in json.dumps(rows[0]).lower()


def test_churches_table_stores_only_id_and_website(data_dir):
    upsert_church("place456", "https://church.org")
    from app.db import _connect

    with _connect() as conn:
        row = conn.execute("SELECT * FROM churches WHERE church_id = ?", ("place456",)).fetchone()
    keys = set(row.keys())
    assert keys <= {"church_id", "website", "last_checked"}
