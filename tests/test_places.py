"""Tests for Stage 1 Places + OSM (no network)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.stage1 import osm, places

FIX = Path(__file__).parent / "fixtures"


class PlacesTransport(httpx.BaseTransport):
    def __init__(self, pages: dict[str, dict], geocode: dict):
        self.pages = pages
        self.geocode = geocode
        self.requests: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        self.requests.append(body)
        if "includedType" not in body:
            return httpx.Response(200, json=self.geocode, request=request)
        token = body.get("pageToken")
        if token == "page2token":
            return httpx.Response(200, json=self.pages["page2"], request=request)
        return httpx.Response(200, json=self.pages["page1"], request=request)


class OsmTransport(httpx.BaseTransport):
    def __init__(self, payload: dict):
        self.payload = payload

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.payload, request=request)


@pytest.fixture
def places_client(monkeypatch):
    pages = {
        "page1": json.loads((FIX / "places_search_page1.json").read_text(encoding="utf-8")),
        "page2": json.loads((FIX / "places_search_page2.json").read_text(encoding="utf-8")),
    }
    geocode = json.loads((FIX / "places_geocode.json").read_text(encoding="utf-8"))
    transport = PlacesTransport(pages, geocode)
    client = httpx.Client(transport=transport)
    places.set_client(client)
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    yield transport
    places.set_client(None)


@pytest.fixture
def osm_client():
    payload = json.loads((FIX / "osm_overpass.json").read_text(encoding="utf-8"))
    client = httpx.Client(transport=OsmTransport(payload))
    osm.set_client(client)
    yield
    osm.set_client(None)


def test_search_churches_filters_operational_and_pages(places_client):
    lat, lng = 38.1383, -97.4314
    results = places.search_churches(lat, lng, radius_m=8000, max_results=60)
    ids = {r["church_id"] for r in results}
    assert "ChIJ004_closed_chapel" not in ids
    assert len(results) == 6
    assert all(r["business_status"] == "OPERATIONAL" for r in results if "business_status" in r)
    assert results[0]["distance_miles"] <= results[-1]["distance_miles"]
    req = places_client.requests[0]
    assert req["textQuery"] == "church"
    assert req["includedType"] == "church"
    assert req["strictTypeFiltering"] is True
    assert req["locationBias"]["circle"]["radius"] == 8000
    assert req["pageSize"] == 20
    assert any(r.get("pageToken") == "page2token" for r in places_client.requests)


def test_search_churches_dict_shape(places_client):
    r = places.search_churches(38.1383, -97.4314, 5000)[0]
    for key in ("church_id", "name", "address", "lat", "lng", "website", "phone", "types", "distance_miles", "source"):
        assert key in r
    assert r["source"] == "places"


def test_geocode_returns_first_operational(places_client):
    lat, lng = places.geocode("Hesston, KS")
    assert lat == pytest.approx(38.1383)
    assert lng == pytest.approx(-97.4314)


def test_osm_search_same_shape_and_denomination_hint(osm_client):
    results = osm.search_churches_osm(38.1383, -97.4314, 5000)
    assert len(results) == 2
    assert results[0]["church_id"].startswith("osm:")
    assert results[0]["source"] == "osm"
    assert results[0]["osm_denomination"] == "Mennonite"
    for key in ("church_id", "name", "address", "lat", "lng", "distance_miles"):
        assert key in results[0]
