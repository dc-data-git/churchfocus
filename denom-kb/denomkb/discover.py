"""Find sources for a group: Wikipedia article + Wikidata facts + official website + existing
source register URLs; then crawl the official site for belief/history/governance pages.

Wikidata facts are deterministic, cited fills (no model involved):
  P856 official website -> identity.official_website
  P571 inception        -> history.founding_date (year)
  P112 founded by       -> history.founders
  P740 location of formation -> history.founding_location
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from .web import Web

log = logging.getLogger("denomkb.discover")
WP_API = "https://en.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"

CRAWL_HINTS = ["belief", "believe", "faith", "doctrine", "statement", "confession", "creed", "about", "who-we-are",
               "who we are", "history", "heritage", "govern", "polity", "constitution", "bylaws", "book of discipline",
               "worship", "sacrament", "baptism", "communion", "ordination", "ordain", "position", "resolution",
               "social", "teaching", "articles", "what we", "identity", "theology", "values", "mission", "ministries"]
SKIP_LINK = re.compile(r"(login|signin|donate|give|cart|shop|store|calendar|events?/|news/\d|podcast|careers|jobs|"
                       r"privacy|terms|cookie|facebook|twitter|instagram|youtube|linkedin|mailto:|tel:)", re.I)

NAME_NOISE = re.compile(r",?\s*(inc\.?|international|in america|undifferentiated|estimate|"
                        r"groups?|congregations?|temples?|centers?|independent)$", re.I)


def clean_name(name: str) -> str:
    prev = None
    while prev != name:
        prev, name = name, NAME_NOISE.sub("", name).strip(" ,")
    return name


def _norm(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower())) - {"the", "of", "in", "and", "church", "churches", "inc", "usa"}


def wikipedia(web: Web, name: str) -> dict | None:
    """Best Wikipedia article for a group name. Returns {title, url, text, pageid} or None."""
    q = clean_name(name)
    data = web.get_json(WP_API, {"action": "query", "list": "search", "srsearch": q, "srlimit": 5,
                                 "format": "json", "formatversion": 2})
    if not data:
        return None
    hits = data.get("query", {}).get("search", [])
    want = _norm(q)
    best, best_score = None, 0.0
    for i, h in enumerate(hits):
        got = _norm(h["title"])
        overlap = len(want & got) / max(len(want | got), 1)
        score = overlap - 0.05 * i
        if score > best_score:
            best, best_score = h, score
    if not best or best_score < 0.25:
        return None
    page = web.get_json(WP_API, {"action": "query", "prop": "extracts|pageprops", "explaintext": 1,
                                 "pageids": best["pageid"], "format": "json", "formatversion": 2})
    if not page:
        return None
    p = page["query"]["pages"][0]
    return {"title": p["title"], "pageid": p["pageid"], "match_score": round(best_score, 3),
            "url": "https://en.wikipedia.org/wiki/" + p["title"].replace(" ", "_"),
            "text": p.get("extract", ""), "qid": p.get("pageprops", {}).get("wikibase_item")}


def _label(web: Web, qids: list[str]) -> list[str]:
    if not qids:
        return []
    d = web.get_json(WD_API, {"action": "wbgetentities", "ids": "|".join(qids[:20]), "props": "labels",
                              "languages": "en", "format": "json"})
    if not d:
        return []
    return [e["labels"]["en"]["value"] for e in d.get("entities", {}).values() if "en" in e.get("labels", {})]


def wikidata(web: Web, qid: str | None) -> dict:
    if not qid:
        return {}
    d = web.get_json(WD_API, {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"})
    if not d or qid not in d.get("entities", {}):
        return {}
    claims = d["entities"][qid].get("claims", {})

    def vals(p):
        out = []
        for c in claims.get(p, []):
            dv = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if dv is not None:
                out.append(dv)
        return out
    facts = {"qid": qid, "url": f"https://www.wikidata.org/wiki/{qid}"}
    w = [v for v in vals("P856") if isinstance(v, str)]
    if w:
        facts["official_website"] = w[0]
    inc = [v["time"] for v in vals("P571") if isinstance(v, dict) and "time" in v]
    if inc:
        m = re.match(r"[+-](\d{4})", inc[0])
        if m:
            facts["founding_year"] = m.group(1)
    founders = _label(web, [v["id"] for v in vals("P112") if isinstance(v, dict) and "id" in v])
    if founders:
        facts["founders"] = founders
    loc = _label(web, [v["id"] for v in vals("P740") if isinstance(v, dict) and "id" in v])
    if loc:
        facts["founding_location"] = loc
    return facts


def crawl_site(web: Web, start: str, max_pages: int, max_depth: int = 2) -> list[dict]:
    """Same-domain crawl that follows only links that look like belief/history/governance pages."""
    host = urlparse(start).netloc.replace("www.", "")
    seen, pages = set(), []
    frontier = [(start, 0, 100)]
    while frontier and len(pages) < max_pages:
        frontier.sort(key=lambda x: -x[2])
        url, depth, _ = frontier.pop(0)
        if url in seen:
            continue
        seen.add(url)
        page = web.get_page(url)
        if not page or len(page.get("text", "")) < 200:
            continue
        pages.append(page)
        if depth >= max_depth:
            continue
        for ln in page.get("links", []):
            u = ln["url"].rstrip(").,;'\"")
            if urlparse(u).netloc.replace("www.", "") != host or u in seen or SKIP_LINK.search(u):
                continue
            hay = (u + " " + ln["text"]).lower()
            score = sum(2 if h in ln["text"].lower() else 1 for h in CRAWL_HINTS if h in hay)
            if score:
                frontier.append((u, depth + 1, score))
    return pages


def wikipedia_by_title(web: Web, title: str) -> dict | None:
    page = web.get_json(WP_API, {"action": "query", "prop": "extracts|pageprops", "explaintext": 1, "redirects": 1,
                                 "titles": title, "format": "json", "formatversion": 2})
    if not page:
        return None
    p = page["query"]["pages"][0]
    if p.get("missing"):
        return None
    return {"title": p["title"], "pageid": p["pageid"], "match_score": 1.0,
            "url": "https://en.wikipedia.org/wiki/" + p["title"].replace(" ", "_"),
            "text": p.get("extract", ""), "qid": p.get("pageprops", {}).get("wikibase_item")}
