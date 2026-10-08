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


def _page_key(url: str) -> tuple:
    parsed = urlparse(url)
    return (parsed.netloc.lower().removeprefix("www."), parsed.path.rstrip("/") or "/", parsed.query)


def resource_kind(url: str, label: str = "") -> str | None:
    """Large/changing public collections are catalogued, not exhaustively crawled."""
    combined = f"{url} {label}".lower()
    for kind, pattern in (("calendar", r"calendar|events"), ("sermons", r"sermon|podcast|messages|/watch|/media"),
                          ("groups", r"small.groups|group.finder|group.directory"),
                          ("bulletins", r"bulletin|newsletter"), ("registration", r"registration|register")):
        if re.search(pattern, combined):
            return kind
    return None


def site_pages(url: str, max_pages: int = 30, *, cancelled=None, progress=None,
               max_seconds: float = 120, coverage=None, resources=None) -> list[dict]:
    """Bounded recursive same-site factual scan; expose limits rather than claim completeness."""
    import time
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda *args: None)
    coverage = coverage if coverage is not None else {}
    resources = resources if resources is not None else []
    coverage.update(pages_scanned=0, limited=False, failures=[], missing_basics=[])
    if not url:
        return []
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    queue = [(200, url.rstrip("/") + "/", "home", False)]
    seen, final_seen, pages, known_resources = set(), set(), [], set()
    started = time.monotonic()
    base = url
    while queue and len(pages) < max_pages and time.monotonic() - started < max_seconds:
        if cancelled():
            break
        queue.sort(key=lambda item: (-item[0], item[1]))
        _, target, kind, collection = queue.pop(0)
        key = _page_key(target)
        if key in seen:
            continue
        seen.add(key)
        try:
            result = fetch(target, max_chars=60000, max_age_days=7)
        except Blocked:
            coverage["failures"].append({"url": target, "reason": "not permitted"})
            continue
        if result.get("status") != 200 or result.get("error") or not result.get("text", "").strip():
            coverage["failures"].append({"url": target, "reason": result.get("error", "no readable content")})
            continue
        if not _same_site(base, result["url"]):
            coverage["failures"].append({"url": target, "reason": "redirected outside church site"})
            continue
        final_key = _page_key(result["url"])
        if final_key in final_seen:
            continue
        final_seen.add(final_key)
        seen.add(final_key)
        if not pages:
            base = result["url"]
        pages.append({"url": result["url"], "kind": kind, "text": result["text"],
                      "text_truncated": result.get("text_truncated", False),
                      "checked_at": result.get("checked_at"), "from_cache": result.get("from_cache", False)})
        progress(len(pages), max_pages, f"Scanning website — {len(pages)} pages read")
        # Collection landing pages establish availability; individual archive entries stay on demand.
        if collection:
            continue
        for link in result.get("links", []):
            full = _normalize_url(result["url"], link.get("href", ""))
            if not link.get("href") or _BLOCKLIST_HINT.search(full) or not full.startswith(("https://", "http://")):
                continue
            rk = resource_kind(full, link.get("text", ""))
            if rk and full not in known_resources:
                resources.append({"kind": rk, "url": full, "label": link.get("text") or rk})
                known_resources.add(full)
            if rk in {"calendar", "registration"}:
                continue
            if not _same_site(base, full) or _page_key(full) in seen:
                continue
            if re.search(r"\.(?:jpg|png|gif|svg|mp3|mp4|zip|css|js)(?:\?|$)", full, re.I):
                continue
            # Discard tracking/pagination/search query variants and individual dated event entries.
            if urlparse(full).query or re.search(r"/(?:event|events)/.+|/20\d\d/", urlparse(full).path):
                continue
            score, lk = _score_link(full, link.get("text", ""))
            queue.append((score, full, _classify_page(full, lk), bool(rk)))
    coverage["pages_scanned"] = len(pages)
    coverage["limited"] = bool(queue) and not cancelled()
    coverage["limit_reason"] = ("page/time budget reached" if coverage["limited"] else "")
    coverage["truncated_pages"] = [p["url"] for p in pages if p.get("text_truncated")]
    if coverage["truncated_pages"]:
        coverage["limited"] = True
        coverage["limit_reason"] += "; page text budget reached (60,000 characters)"
    # Repeated footer text is removed only from extraction, never from retained source documents.
    lines = {}
    for page in pages:
        for line in set(x.strip() for x in page["text"].splitlines() if x.strip()):
            lines[line] = lines.get(line, 0) + 1
    for page in pages:
        page["scan_text"] = "\n".join(line for line in page["text"].splitlines()
             if not (len(pages) >= 3 and lines.get(line.strip(), 0) >= max(3, len(pages) * .7)
                     and re.search(r"copyright|all rights reserved|privacy policy|powered by", line, re.I)))
    return pages
