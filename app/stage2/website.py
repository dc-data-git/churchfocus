"""Fetch and pick pages from a church site (INTERFACES §4, ARCHITECTURE §7)."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from app.web import Blocked, fetch

_KIND_PATTERNS: list[tuple[str, str, int]] = [
    ("beliefs", r"belief|what-we-believe|faith|doctrine|statement", 100),
    ("staff", r"staff|leadership|team|pastors|elders|our-team", 95),
    ("about", r"\babout\b|who-we-are|our-story|welcome", 90),
    ("sermons", r"sermon|watch|media|messages|podcast|livestream", 85),
    ("ministries", r"ministr", 80),
    ("events", r"event|calendar", 75),
    ("other", r"contact|connect|visit", 40),
]

_BLOCKLIST_HINT = re.compile(
    r"(?i)(/prayer|prayer[-_]?request|member[-_]?directory|/directory|/members|"
    r"/login|sign[-_]?in|/give\b|/donate|/offering|child[-_]?check|check[-_]?in|/kidcheck)"
)


def _same_site(base: str, href: str) -> bool:
    b, h = urlparse(base), urlparse(href)
    if h.scheme not in ("http", "https", ""):
        return False
    if not h.netloc:
        return True
    return h.netloc.lower().removeprefix("www.") == b.netloc.lower().removeprefix("www.")


def _normalize_url(base: str, href: str) -> str:
    return urljoin(base, href).split("#")[0].rstrip("/") or urljoin(base, href)


def _score_link(href: str, text: str) -> tuple[int, str]:
    combined = f"{href} {text}".lower()
    best_score, best_kind = 0, "other"
    for kind, pattern, weight in _KIND_PATTERNS:
        if re.search(pattern, combined):
            if weight > best_score:
                best_score, best_kind = weight, kind
    return best_score, best_kind


def _classify_page(url: str, link_kind: str) -> str:
    path = urlparse(url).path.lower()
    for kind, pattern, _ in _KIND_PATTERNS:
        if kind == "other":
            continue
        if re.search(pattern, path):
            return kind
    return link_kind if link_kind != "other" else "other"


def site_pages(url: str, max_pages: int = 8) -> list[dict]:
    """Return up to max_pages [{url, kind, text}] from the church site."""
    if not url:
        return []

    home_url = url.rstrip("/") + ("" if url.endswith("/") else "")
    if not home_url.startswith("http"):
        home_url = "https://" + home_url.lstrip("/")

    try:
        home = fetch(home_url if home_url.endswith("/") else home_url + "/")
    except Blocked:
        return []

    base = home["url"]
    pages: list[dict] = [{"url": base, "kind": "home", "text": home["text"]}]
    seen = {base.rstrip("/"), base.rstrip("/") + "/"}

    candidates: list[tuple[int, str, str]] = []
    for link in home.get("links", []):
        href = link.get("href", "")
        if not href or _BLOCKLIST_HINT.search(href):
            continue
        full = _normalize_url(base, href)
        key = full.rstrip("/")
        if key in seen or not _same_site(base, full):
            continue
        score, kind = _score_link(href, link.get("text", ""))
        if score > 0:
            candidates.append((score, full, kind))
            seen.add(key)

    candidates.sort(key=lambda x: (-x[0], x[1]))
    for _, page_url, link_kind in candidates[: max(0, max_pages - 1)]:
        if _BLOCKLIST_HINT.search(urlparse(page_url).path):
            continue
        try:
            result = fetch(page_url)
        except Blocked:
            continue
        if result.get("status", 0) != 200 or not (result.get("text") or "").strip():
            continue
        kind = _classify_page(result["url"], link_kind)
        pages.append({"url": result["url"], "kind": kind, "text": result["text"]})
    return pages
