"""Overpass fallback when Places is unavailable (ARCHITECTURE §5)."""
from __future__ import annotations

import math
import re

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_client: httpx.Client | None = None


def set_client(client: httpx.Client | None) -> None:
    global _client
    _client = client


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=60.0)
    return _client


def _miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlng = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return r * 2 * math.asin(min(1.0, math.sqrt(a)))


def _coords(el: dict) -> tuple[float, float]:
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    center = el.get("center") or {}
    return float(center.get("lat", 0)), float(center.get("lon", 0))


def search_churches_osm(lat: float, lng: float, radius_m: int) -> list[dict]:
    """Query amenity=place_of_worship + religion=christian; same dict shape as Places."""
    query = f"""
[out:json][timeout:25];
(
  node["amenity"="place_of_worship"]["religion"="christian"](around:{radius_m},{lat},{lng});
  way["amenity"="place_of_worship"]["religion"="christian"](around:{radius_m},{lat},{lng});
);
out center tags;
"""
    resp = _get_client().post(OVERPASS_URL, data={"data": query})
    resp.raise_for_status()
    out: list[dict] = []
    for el in resp.json().get("elements") or []:
        tags = el.get("tags") or {}
        plat, plng = _coords(el)
        name = tags.get("name") or tags.get("operator") or "Unknown Church"
        addr_parts = [tags.get(k, "") for k in ("addr:housenumber", "addr:street", "addr:city", "addr:state")]
        address = " ".join(p for p in addr_parts if p).strip()
        website = tags.get("website") or tags.get("contact:website")
        if website and not website.startswith("http"):
            website = "https://" + website
        out.append({
            "church_id": f"osm:{el.get('id', '')}",
            "name": name,
            "address": address,
            "lat": plat,
            "lng": plng,
            "website": website,
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "types": ["place_of_worship"],
            "business_status": "OPERATIONAL",
            "distance_miles": round(_miles(lat, lng, plat, plng), 2),
            "osm_denomination": tags.get("denomination"),
            "source": "osm",
        })
    return sorted(out, key=lambda c: c["distance_miles"])
