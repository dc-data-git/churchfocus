"""Tests for denomination.resolve and light_search (no network)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.models import Preference, PreferenceProfile
from app.stage1 import denomination, osm, places
from tests.fakes import FakeLLM, FakeWeb
from tests.test_places import OsmTransport, PlacesTransport

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def wired_search(monkeypatch):
    pages = {
        "page1": json.loads((FIX / "places_search_page1.json").read_text(encoding="utf-8")),
        "page2": json.loads((FIX / "places_search_page2.json").read_text(encoding="utf-8")),
    }
    geocode = json.loads((FIX / "places_geocode.json").read_text(encoding="utf-8"))
    places.set_client(httpx.Client(transport=PlacesTransport(pages, geocode)))
    osm.set_client(httpx.Client(transport=OsmTransport(json.loads((FIX / "osm_overpass.json").read_text()))))
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    yield
    places.set_client(None)
    osm.set_client(None)


def test_resolve_first_mennonite_by_name(kb):
    guess = denomination.resolve({"name": "First Mennonite Church", "address": "Hesston, KS", "types": ["church"]})
    assert guess.denomination_id == "mcusa"
    assert guess.method == "name"
    assert guess.confidence >= 0.6


def test_resolve_non_denom_from_name():
    guess = denomination.resolve({"name": "Grace Non-Denominational Church", "types": ["church"]})
    assert guess.label == "Non-denominational"
    assert guess.confidence >= 0.8
    assert guess.method == "name"


def test_resolve_osm_denomination_tag(kb):
    guess = denomination.resolve({
        "name": "Prairie Community Church",
        "osm_denomination": "Mennonite",
        "types": ["church"],
    })
    assert guess.denomination_id == "mcusa"
    assert guess.confidence == 0.6
    assert guess.evidence and guess.evidence[0].tier == "C"


def test_resolve_locator_hit(monkeypatch, kb):
    fake = FakeLLM()
    fake.responses["web_search"] = [{
        "title": "Hesston Mennonite Fellowship - Hesston",
        "url": "https://mennoniteusa.org/congregations/hesston-mennonite-fellowship",
        "snippet": "Hesston Mennonite Fellowship in Hesston, KS is a member congregation.",
    }]
    monkeypatch.setattr("app.llm.web_search", fake.web_search)
    monkeypatch.setattr(denomination, "LOCATOR_ENABLED", True)   # off by default for the demo (R9)
    guess = denomination.resolve({
        "name": "Hesston Mennonite Fellowship",
        "address": "101 E Smith St, Hesston, KS",
        "types": ["church"],
    })
    assert guess.method == "locator"
    assert guess.confidence >= 0.95
    assert guess.denomination_id == "mcusa"


def test_resolve_website_non_denom(monkeypatch):
    pages = {
        "https://community-nondenominational.example.org": {
            "html": "<html><body><h1>Community Church</h1><p>We are an independent non-denominational church.</p></body></html>"
        }
    }
    FakeWeb(pages).install(monkeypatch)
    fake = FakeLLM(responses={
        "denom_classify": {
            "label": "Non-denominational", "kb_candidates": [], "independent": True,
            "confidence": 0.9,
            "quote": "independent non-denominational church",
        }
    })
    monkeypatch.setattr("app.llm.complete_json", fake.complete_json)
    guess = denomination.resolve({
        "name": "Community Church of Hesston",
        "website": "https://community-nondenominational.example.org",
        "types": ["church"],
    })
    assert guess.label == "Non-denominational"
    assert guess.confidence >= 0.8


def test_locator_disabled_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr("app.llm.web_search", lambda q, max_results=8: calls.append(q) or [])
    denomination.resolve({"name": "Hesston Mennonite Fellowship", "address": "Hesston, KS", "types": ["church"]})
    assert calls == []


def test_website_classify_uses_kb_ids_and_requires_verbatim_quote(monkeypatch):
    pages = {"https://trinity.example.org": {"html": "<html><body><p>Trinity is a congregation of the Evangelical Lutheran Church in America (ELCA), gathered around Word and Sacrament.</p></body></html>"}}
    FakeWeb(pages).install(monkeypatch)
    good = FakeLLM(responses={"denom_classify": {"label": "ELCA", "kb_candidates": ["elca"], "independent": False, "confidence": 0.9,
                                                 "quote": "a congregation of the Evangelical Lutheran Church in America (ELCA)"}})
    monkeypatch.setattr("app.llm.complete_json", good.complete_json)
    g = denomination.resolve({"name": "Trinity Church", "website": "https://trinity.example.org", "types": ["church"]})
    assert g.denomination_id == "elca" and g.confidence >= 0.8 and g.evidence[0].tier == "A"
    bad = FakeLLM(responses={"denom_classify": {"label": "ELCA", "kb_candidates": ["elca"], "independent": False, "confidence": 0.9,
                                                "quote": "Trinity proudly belongs to the ELCA synod of Kansas"}})
    monkeypatch.setattr("app.llm.complete_json", bad.complete_json)
    g2 = denomination._website_classify("Trinity Church", "https://trinity.example.org", denomination.get_kb())
    assert g2.confidence <= 0.6 and not g2.evidence     # invented quote -> not confident, no A-tier evidence


def test_resolve_unknown_low_confidence():
    guess = denomination.resolve({"name": "Random Chapel", "types": ["church"]})
    assert guess.confidence <= 0.4
    assert "Unknown" in guess.label


def test_light_search_sorted_and_resolves(wired_search, kb):
    profile = PreferenceProfile(
        session_id="t1",
        origin={"text": "Hesston, KS"},
        max_miles=20,
        preferences=[Preference(feature="worship.style", want=["traditional_hymns"], weight="important")],
    )
    results = denomination.light_search(profile)
    assert len(results) >= 1
    scores = [mr.score for _, mr in results]
    assert scores == sorted(scores, reverse=True)
    assert any(c.denomination.denomination_id for c, _ in results)


def test_light_search_excludes_dealbreaker_violator(wired_search, monkeypatch, kb):
    real_resolve = denomination.resolve

    def fake_resolve(candidate):
        if candidate["name"] == "Holy Cross Catholic Church":
            from app.models import DenomGuess, Evidence
            return DenomGuess(
                denomination_id="catholic",
                label="Catholic Church",
                confidence=0.9,
                method="name",
                evidence=[
                    Evidence(
                        feature="women.senior_pastor",
                        value="no",
                        tier="A",
                        quote="Only men may be ordained as priests.",
                        url=candidate.get("website") or "",
                        source_kind="website",
                        how="stated",
                    )
                ],
            )
        return real_resolve(candidate)

    monkeypatch.setattr(denomination, "resolve", fake_resolve)
    profile = PreferenceProfile(
        session_id="t2",
        origin={"lat": 38.1383, "lng": -97.4314},
        max_miles=20,
        preferences=[Preference(feature="women.senior_pastor", want=["yes"], weight="dealbreaker")],
    )
    results = denomination.light_search(profile)
    names = [c.name for c, _ in results]
    assert "Holy Cross Catholic Church" not in names
    assert all(not mr.excluded for _, mr in results)
