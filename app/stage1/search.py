"""v2 search + churches table (REDESIGN §5; D28-D31, D38, D40).

- ensure_coverage(): runs Google Places only when the requested radius goes beyond what this session already fetched
  (D40); covers up to 50 miles with a ring of overlapping queries because one query returns at most 60 places.
- table(): scores every gathered church against the CURRENT memory profile, filters by radius, sorts, pages.
  Denomination: cheap name/KB match for all; a website check only for rows actually shown (in the background).
"""
from __future__ import annotations

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.denom.kb import NON_NICENE_NAME, NON_NICENE_IDS, get_kb
from app.match import score
from app.models import Church, DenomGuess, Evidence
from app.stage1 import denomination, osm, places

log = logging.getLogger("app.search")
MAX_RADIUS_MI = 50.0
MI_PER_M = 1 / 1609.34
_lock = threading.Lock()
from collections import defaultdict
_coverage_locks = defaultdict(threading.RLock)
_mem: dict[str, dict[str, dict]] = {}          # session -> church_id -> {"candidate":..., "denom":...}
_web_checked: set[tuple] = set()
_discovering: set[tuple] = set()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="denom-web")
FIT_ORDER = {"strong": 0, "possible": 1, "unknown": 2, "unlikely": 3, "poor": 4}


def _miles(lat1, lng1, lat2, lng2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return r * 2 * math.asin(min(1.0, math.sqrt(a)))


def _session(sid: str) -> dict[str, dict]:
    with _lock:
        if sid not in _mem:
            _mem[sid] = {r["church_id"]: {"candidate": r["candidate"], "denom": r["denom"]} for r in db.session_churches(sid)}
        return _mem[sid]


def covered_radius(sid: str, lat: float, lng: float) -> float:
    rows = [c for c in db.coverage_get(sid) if _miles(lat, lng, c["lat"], c["lng"]) < 0.5]
    return max((c["radius_mi"] for c in rows), default=0.0)


def reset(sid: str) -> None:
    """New starting place: forget gathered churches for this session (coverage rows stay as history)."""
    from app import jobs
    with jobs._lock:
        jobs.reset_session(sid)
        db.session_church_reset(sid)
        with _lock:
            _mem[sid] = {}
    from app import memory
    memory.bump_table(sid)


def _points(lat: float, lng: float, radius_mi: float) -> list[tuple[float, float, float]]:
    """Query centres and radii (mi). <=12 mi: one circle. Larger: centre + ring of 6 overlapping circles."""
    if radius_mi <= 12:
        return [(lat, lng, radius_mi)]
    sub = radius_mi / 2.0
    ring = radius_mi * 0.55
    pts = [(lat, lng, sub)]
    for k in range(6):
        ang = math.radians(60 * k)
        dlat = (ring * math.cos(ang)) / 69.0
        dlng = (ring * math.sin(ang)) / (69.0 * max(0.2, math.cos(math.radians(lat))))
        pts.append((lat + dlat, lng + dlng, sub))
    return pts


def _fast_denom(c: dict) -> DenomGuess:
    try:
        return denomination.resolve(c, use_website=False)
    except Exception:   # never fail the search over one name
        return DenomGuess(label="Unknown", confidence=0.2, method="unknown")


def _non_nicene(c: dict, d: DenomGuess) -> bool:
    return bool(NON_NICENE_NAME.search(c.get("name") or "")) or (d.denomination_id in NON_NICENE_IDS)


def ensure_coverage(session_id: str, lat: float, lng: float, radius_mi: float) -> int:
    # A table refresh can overlap the first automatic search. Fetch each radius once.
    with _coverage_locks[session_id]:
        return _ensure_coverage(session_id, lat, lng, radius_mi)


def _ensure_coverage(session_id: str, lat: float, lng: float, radius_mi: float) -> int:
    radius_mi = min(max(radius_mi, 1.0), MAX_RADIUS_MI)
    have = covered_radius(session_id, lat, lng)
    if radius_mi <= have + 0.01:
        return 0   # D40: nothing new to fetch
    generation = db.session_state(session_id)["generation"]
    store = _session(session_id)
    from app import jobs, memory
    queries = added = 0
    # Keep the original bounded circle/ring search. Publish each completed circle.
    for plat, plng, prad in _points(lat, lng, radius_mi):
        if db.session_state(session_id)["generation"] != generation:
            return added
        try:
            res = places.search_churches(plat, plng, int(prad / MI_PER_M), max_results=60)
            queries += 1
        except Exception as e:
            log.warning("Places failed (%s); falling back to OpenStreetMap", e)
            try:
                res = osm.search_churches_osm(plat, plng, int(prad / MI_PER_M))
                queries += 1
            except Exception:
                res = []
        with jobs._lock:
            if db.session_state(session_id)["generation"] != generation:
                return added
            batch_added = 0
            for c in res:
                cid = c.get("church_id")
                c["distance_miles"] = round(_miles(lat, lng, c.get("lat") or 0, c.get("lng") or 0), 2)
                if not cid or c["distance_miles"] > radius_mi:
                    continue
                if cid in store:
                    continue
                d = _fast_denom(c)
                if _non_nicene(c, d):
                    continue
                store[cid] = {"candidate": c, "denom": d.model_dump(mode="json")}
                db.session_church_put(session_id, cid, c, store[cid]["denom"])
                batch_added += 1
            added += batch_added
            if batch_added:
                memory.bump_table(session_id)
    if not queries:
        raise RuntimeError("Both map search providers failed")
    with jobs._lock:
        if db.session_state(session_id)["generation"] == generation:
            db.coverage_add(session_id, lat, lng, radius_mi, queries)
            memory.bump_table(session_id)
    return added



def _church(entry: dict) -> Church:
    c, d = entry["candidate"], DenomGuess.model_validate(entry["denom"] or {})
    ev = db.get_evidence(c["church_id"])
    return Church(church_id=c["church_id"], name=c.get("name", ""), address=c.get("address") or "", lat=c.get("lat") or 0.0,
                  lng=c.get("lng") or 0.0, website=c.get("website"), phone=c.get("phone"),
                  distance_miles=c.get("distance_miles") or 0.0, denomination=d, evidence=list(d.evidence) + ev)


def _web_check(session_id: str, church_id: str) -> None:
    generation = db.session_state(session_id)["generation"]
    store = _session(session_id)
    entry = store.get(church_id)
    if not entry:
        return
    try:
        d = denomination.resolve(entry["candidate"], use_website=True)
    except Exception:
        return
    from app import jobs
    with jobs._lock:
        if db.session_state(session_id)["generation"] != generation:
            return
        if _non_nicene(entry["candidate"], d):
            store.pop(church_id, None)
        else:
            entry["denom"] = d.model_dump(mode="json")
            db.session_church_put(session_id, church_id, entry["candidate"], entry["denom"])
        from app import memory
        memory.bump_table(session_id)


def churches(session_id: str) -> list[Church]:
    return [_church(e) for e in list(_session(session_id).values())]


def get_church(session_id: str, church_id: str) -> Church | None:
    e = _session(session_id).get(church_id)
    return _church(e) if e else None


def ranked(session_id: str) -> list[tuple[Church, object]]:
    from app import memory

    kb = get_kb()
    prof = memory.to_profile(session_id)
    out = []
    for ch in churches(session_id):
        mr = score(ch, prof, kb)
        if not mr.excluded or ch.church_id in db.session_state(session_id)["pins"]:
            out.append((ch, mr))
    out.sort(key=lambda x: (FIT_ORDER.get(x[1].fit, 2), -x[1].score, x[0].distance_miles))
    return out


def table(session_id: str, *, radius_mi: float = 15, sort: str = "fit", page: int = 1, size: int = 10) -> dict:
    from app import memory

    loc = memory.location(session_id) or {}
    covered = covered_radius(session_id, loc.get("lat", 0), loc.get("lng", 0)) if loc else 0.0
    if loc and radius_mi > covered + 0.01:
        request_coverage(session_id, loc["lat"], loc["lng"], radius_mi)
    state = db.session_state(session_id)
    pins = set(state["pins"])
    rows = [(ch, mr) for ch, mr in ranked(session_id) if ch.distance_miles <= radius_mi or ch.church_id in pins]
    if sort == "distance":
        rows.sort(key=lambda x: x[0].distance_miles)
    elif sort == "name":
        rows.sort(key=lambda x: x[0].name.lower())
    rows.sort(key=lambda item: item[0].church_id not in pins)  # stable sort keeps selected churches at top.
    total = len(rows)
    pages = max(1, math.ceil(total / size))
    page = min(max(page, 1), pages)
    view = rows[(page - 1) * size: page * size]
    stage2 = _stage2_status(session_id)
    out = []
    for ch, mr in view:
        web_key = (session_id,state["generation"],ch.church_id)
        if web_key not in _web_checked and ch.denomination.confidence < 0.8 and ch.website:
            _web_checked.add(web_key)
            _pool.submit(_web_check, session_id, ch.church_id)
        out.append({"church_id": ch.church_id, "name": ch.name, "address": ch.address, "distance_miles": ch.distance_miles,
                    "denomination": ch.denomination.label, "denom_confidence": ch.denomination.confidence,
                    "fit": mr.fit, "score": mr.score, "why": mr.why[:3], "website": ch.website,
                    "stage2": stage2.get(ch.church_id, {}).get("status", "none"),
                    "progress": stage2.get(ch.church_id, {}).get("pct"),
                    "pinned": ch.church_id in pins, "mismatch": mr.excluded or ch.distance_miles > radius_mi,
                    "affiliation_verified": ch.denomination.confidence >= 0.8 and bool(ch.denomination.denomination_id),
                    "summary": stage2.get(ch.church_id, {}).get("summary")})
    return {"rows": out, "total": total, "page": page, "pages": pages, "radius_mi": radius_mi, "covered_mi": covered,
            "research": db.session_state(session_id), "discovery": db.session_state(session_id)["discovery"]}


def _stage2_status(session_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        for j in db.jobs_for_session(session_id):
            if j.get("kind") != "medium" or not j.get("visible",1) or j.get("generation",0) != db.session_state(session_id)["generation"]:
                continue
            st = j["status"]
            pct = round(100 * (j.get("done") or 0) / j["total"]) if j.get("total") else 0
            import json
            out[j["church_id"]] = {"status": "done" if st == "complete" else st, "pct": pct if j.get("total") else None,
                                    "summary": json.loads(j["result_json"]) if st == "complete" and j.get("result_json") else None}
    except Exception:
        pass
    return out


def top(session_id: str, n: int = 5, radius_mi: float | None = None) -> list[Church]:
    from app import memory

    lim = radius_mi or (memory.to_profile(session_id).max_miles or 15)
    return [ch for ch, mr in ranked(session_id) if ch.distance_miles <= lim and mr.fit in ("strong", "possible", "unknown")][:n]


def request_coverage(sid: str, lat: float, lng: float, radius: float) -> None:
    generation=db.session_state(sid)["generation"]
    key=(sid,generation,lat,lng,radius)
    with _lock:
        if key in _discovering:
            return
        _discovering.add(key)
    db.state_update(sid,discovery={"status":"running","label":"Finding nearby churches"})
    def run():
        try:
            if db.session_state(sid)["generation"]!=generation:
                return
            ensure_coverage(sid,lat,lng,radius)
            if db.session_state(sid)["generation"]==generation:
                db.state_update(sid,discovery={"status":"complete","label":"Nearby church list updated"})
        except Exception:
            log.exception("Church discovery failed")
            if db.session_state(sid)["generation"]==generation:
                db.state_update(sid,discovery={"status":"error","label":"Map search could not finish. Try refreshing the church list."})
        finally:
            with _lock:
                _discovering.discard(key)
            from app import memory
            memory.bump_table(sid)
    _pool.submit(run)


def add_url(sid: str, url: str) -> Church:
    """Add an explicitly supplied public church website, leaving affiliation unverified."""
    import hashlib
    from urllib.parse import urlsplit
    from app import web,memory
    page=web.fetch(url,max_age_days=7)
    if page.get("status")!=200 or not page.get("text", "").strip():
        raise ValueError("No readable public church content at that URL")
    title=(page.get("title") or urlsplit(url).netloc).strip()[:160]
    cid="url-"+hashlib.sha256(page["url"].encode()).hexdigest()[:20]
    loc=memory.location(sid) or {}
    c={"church_id":cid,"name":title,"website":page["url"],"address":"Location not verified", "lat":loc.get("lat",0),
       "lng":loc.get("lng",0),"distance_miles":0}
    d=_fast_denom(c)
    if _non_nicene(c,d):
        raise ValueError("This directory covers historic Trinitarian Christian churches")
    _session(sid)[cid]={"candidate":c,"denom":d.model_dump(mode="json")}
    db.session_church_put(sid,cid,c,d.model_dump(mode="json"))
    db.upsert_church(cid,page["url"])
    memory.bump_table(sid)
    return get_church(sid,cid)
