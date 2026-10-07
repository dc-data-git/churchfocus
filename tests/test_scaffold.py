import json

import pytest
from fastapi.testclient import TestClient

from app.features import all_features, load_features
from app.models import Church, DenomGuess, MatchResult, PreferenceProfile
from app.web import Blocked, fetch


def test_features_yaml_has_71_features():
    feats = all_features()
    assert len(feats) == 71


def test_models_validate():
    p = PreferenceProfile(session_id="t")
    assert p.confirmed is False
    c = Church(
        church_id="x",
        name="Test Church",
        denomination=DenomGuess(label="Unknown", confidence=0.0),
    )
    assert c.stage_done == 1
    m = MatchResult(church_id="x", score=80.0, excluded=False)
    assert m.why == []


def test_denom_fields_in_kb(kb):
    feats = load_features()["features"]
    kb_fields = set()
    for group in kb.data["groups"]:
        kb_fields.update(group.get("fields", {}))
    for fid, meta in feats.items():
        field_id = meta.get("denom_field")
        if not field_id:
            continue
        if field_id in kb_fields:
            val = next(g["fields"][field_id]["value"] for g in kb.data["groups"] if field_id in g.get("fields", {}))
            assert val is None or isinstance(val, str)


@pytest.mark.parametrize(
    "url",
    [
        "https://church.org/prayer-requests",
        "https://church.org/member-directory",
        "https://church.org/login",
        "https://church.org/give",
    ],
)
def test_blocklisted_urls_refused(url):
    with pytest.raises(Blocked):
        fetch(url)


def test_healthz_and_index():
    from app.main import app

    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Church Search" in resp.text
