"""Google Places Text Search — only place that calls Google (INTERFACES §4, ARCHITECTURE §5)."""
from __future__ import annotations

import math
import re
import time
from typing import Any

import httpx

from app.config import get_settings

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.websiteUri,places.nationalPhoneNumber,places.regularOpeningHours,"
    "places.businessStatus,places.types,places.googleMapsUri,nextPageToken"
)

_client: httpx.Client | None = None


def set_client(client: httpx.Client | None) -> None:
    global _client
    _client = client


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=30.0)
    return _client


def _miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlng = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return r * 2 * math.asin(min(1.0, math.sqrt(a)))


def _parse_place(place: dict, origin_lat: float, origin_lng: float) -> dict:
    loc = place.get("location") or {}
    display = place.get("displayName") or {}
    return {
        "church_id": place.get("id", ""),
        "name": display.get("text", ""),
        "address": place.get("formattedAddress", ""),
        "lat": loc.get("latitude", 0.0),
        "lng": loc.get("longitude", 0.0),
        "website": place.get("websiteUri"),
        "phone": place.get("nationalPhoneNumber"),
        "types": place.get("types") or [],
        "business_status": place.get("businessStatus", ""),
        "distance_miles": round(_miles(origin_lat, origin_lng, loc.get("latitude", 0.0), loc.get("longitude", 0.0)), 2),
        "source": "places",
    }


def _search_page(body: dict) -> dict:
    settings = get_settings()
    resp = _get_client().post(
        PLACES_URL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        },
    )
    resp.raise_for_status()
    return resp.json()


def search_churches(
    lat: float,
    lng: float,
    radius_m: int,
    query: str = "church",
    max_results: int = 60,
) -> list[dict]:
    """Places API (New) Text Search with locationBias circle, paging ≤ max_results."""
    radius_m = min(radius_m, 50000)
    seen: dict[str, dict] = {}
    page_token: str | None = None

    while len(seen) < max_results:
        body: dict[str, Any] = {
            "textQuery": query,
            "includedType": "church",
            "strictTypeFiltering": True,
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}},
            "pageSize": min(20, max_results - len(seen)),
        }
        if page_token:
            body["pageToken"] = page_token

        data = _search_page(body)
        for place in data.get("places") or []:
            if str(place.get("businessStatus", "")).startswith("CLOSED"):   # missing status = keep
                continue
            parsed = _parse_place(place, lat, lng)
            if parsed["church_id"]:
                seen[parsed["church_id"]] = parsed
            if len(seen) >= max_results:
                break

        page_token = data.get("nextPageToken")
        if not page_token or len(seen) >= max_results:
            break
        time.sleep(1.5)   # page tokens can take a moment to become valid

    return sorted(seen.values(), key=lambda c: c["distance_miles"])


_MILES_RE = re.compile(r"[,;]?\s*(within\s+)?\d+(\.\d+)?\s*(mi|mile|miles)\b.*$", re.I)


class GeocodeError(ValueError):
    """The origin text could not be turned into a location. Message is safe to show the user."""


def clean_origin(text: str) -> str:
    """'Hesston, KS, 15 miles' -> 'Hesston, KS'."""
    return _MILES_RE.sub("", text or "").strip(" ,;")


def geocode(text: str) -> tuple[float, float]:
    """Geocode via Places Text Search; first result with a location.
    (No businessStatus filter: Google only sends that field for businesses, never for a city or address.)"""
    q = clean_origin(text)
    if not q:
        raise GeocodeError("Please tell me a town, ZIP code or address to search from.")
    settings = get_settings()
    resp = _get_client().post(
        PLACES_URL,
        json={"textQuery": q, "pageSize": 5},
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": "places.location,places.formattedAddress",
        },
    )
    resp.raise_for_status()
    for place in resp.json().get("places") or []:
        loc = place.get("location") or {}
        if "latitude" in loc and "longitude" in loc:
            return loc["latitude"], loc["longitude"]
    raise GeocodeError(f"I couldn't find \"{q}\" on the map. Try a town and state or a ZIP code.")
