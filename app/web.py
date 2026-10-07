"""Polite web fetcher — robots, rate limit, cache, blocklist (INTERFACES §4)."""
from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

from app.config import get_settings
from app.db import cache_get, cache_put

# Tests may monkeypatch this (BUILD_PLAN T0.1).
RATE_LIMIT_SEC = 1.0

_BLOCKLIST = re.compile(
    r"(?i)(/prayer|prayer[-_]?request|member[-_]?directory|/directory|/members|"
    r"/login|sign[-_]?in|/give\b|/donate|/offering|child[-_]?check|check[-_]?in|/kidcheck)"
)


class Blocked(Exception):
    """URL is blocklisted or disallowed by robots.txt."""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            return
        text = ""
        self.links.append({"href": href, "text": text})

    def handle_data(self, data: str) -> None:
        if self.links:
            self.links[-1]["text"] = (self.links[-1]["text"] + data).strip()


_robots: dict[str, RobotFileParser] = {}
_last_fetch: dict[str, float] = {}
_client: httpx.Client | None = None


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": get_settings().user_agent},
        )
    return _client


def set_client(client: httpx.Client | None) -> None:
    """Inject httpx client (tests)."""
    global _client
    _client = client


def _is_blocklisted(url: str) -> bool:
    return bool(_BLOCKLIST.search(urlparse(url).path + "?" + urlparse(url).query))


def _robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    domain = parsed.netloc
    if domain not in _robots:
        rp = RobotFileParser()
        robots_url = f"{parsed.scheme}://{domain}/robots.txt"
        try:
            resp = _get_client().get(robots_url)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.allow_all = True
        except httpx.HTTPError:
            rp.allow_all = True
        _robots[domain] = rp
    return _robots[domain].can_fetch(get_settings().user_agent, url)


def _rate_limit(url: str) -> None:
    domain = _domain(url)
    now = time.monotonic()
    last = _last_fetch.get(domain, 0.0)
    wait = RATE_LIMIT_SEC - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_fetch[domain] = time.monotonic()


def _extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        href = urljoin(base_url, link["href"])
        if href not in seen:
            seen.add(href)
            out.append({"href": href, "text": link["text"]})
    return out


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"
)


def scrub_pii(text: str, *, keep_contact: str | None = None) -> str:
    """Drop emails/phones that are not the church's main contact (ARCHITECTURE §10)."""
    keep = (keep_contact or "").strip().lower()

    def _email(m: re.Match[str]) -> str:
        return m.group(0) if keep and m.group(0).lower() == keep else "[email redacted]"

    def _phone(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        keep_digits = re.sub(r"\D", "", keep) if keep else ""
        return m.group(0) if keep_digits and digits.endswith(keep_digits[-10:]) else "[phone redacted]"

    text = _EMAIL_RE.sub(_email, text)
    return _PHONE_RE.sub(_phone, text)


def _extract_text(html: str, url: str, max_chars: int) -> str:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
    if not text:
        text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    return scrub_pii(text)[:max_chars]


def fetch(url: str, max_chars: int = 20000) -> dict:
    """Fetch a page; returns {url, status, text, links, from_cache}. Raises Blocked."""
    if _is_blocklisted(url):
        raise Blocked(f"blocklisted URL: {url}")

    cached = cache_get(url)
    if cached:
        fetched_at, body = cached
        age_days = (time.time() - fetched_at.timestamp()) / 86400
        if age_days <= get_settings().cache_max_age_days:
            payload = json.loads(body)
            payload["from_cache"] = True
            return payload

    if not _robots_allowed(url):
        raise Blocked(f"robots.txt disallows: {url}")

    _rate_limit(url)

    # Read-only: GET only (G4).
    resp = _get_client().get(url)
    html = resp.text
    text = _extract_text(html, url, max_chars)
    links = _extract_links(html, str(resp.url))

    result = {
        "url": str(resp.url),
        "status": resp.status_code,
        "text": text,
        "links": links,
        "from_cache": False,
    }
    cache_put(url, json.dumps(result))
    return result
