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

# Guardrail G1: never open prayer, member-directory, login, giving or child check-in pages (R8 widened).
_BLOCKLIST = re.compile(
    r"(?i)(/prayer|prayer[-_]?request|member[-_]?directory|/directory|/members|"
    r"/login|sign[-_]?in|/give\b|/give/|/giving|online[-_]?giving|/generosity|/tithe|/donate|/offering|"
    r"child[-_]?check|check[-_]?in|/kidcheck)"
)
# Whole hosts that are only giving / people-directory services.
_BLOCKED_HOSTS = re.compile(r"(?i)(^|\.)(tithe\.ly|pushpay\.com|givelify\.com|onrealm\.org)$")
_BLOCKED_HOST_PATHS = re.compile(r"(?i)churchcenter\.com/(people|giving|check-ins)")
# A plain library UA gets 403 from many church hosts; identify honestly but browser-compatibly.
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def _ua() -> str:
    ua = get_settings().user_agent or "ChurchSearch/0.1"
    return ua if ua.startswith("Mozilla/") else f"{_BROWSER_UA} {ua}"


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
            headers={"User-Agent": _ua(), "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"},
        )
    return _client


def set_client(client: httpx.Client | None) -> None:
    """Inject httpx client (tests)."""
    global _client
    _client = client


def _is_blocklisted(url: str) -> bool:
    u = urlparse(url)
    host = u.netloc.lower().split(":")[0]
    return bool(_BLOCKLIST.search(u.path + "?" + u.query) or _BLOCKED_HOSTS.search(host)
                or _BLOCKED_HOST_PATHS.search(host + u.path))


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
    return _robots[domain].can_fetch("ChurchSearch", url)


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

    # Read-only: GET only (G4). Network errors become an empty result, never an exception (callers stay simple).
    try:
        resp = _get_client().get(url)
    except httpx.HTTPError as e:
        return {"url": url, "status": 0, "text": "", "links": [], "from_cache": False, "error": f"{type(e).__name__}: {e}"}

    ctype = resp.headers.get("content-type", "").lower()
    final_url = str(resp.url)
    if _is_blocklisted(final_url):          # a redirect can land on a giving/login page
        raise Blocked(f"blocklisted URL after redirect: {final_url}")
    if not (200 <= resp.status_code < 300):
        # R8: never cache or extract error pages (403 challenges, 404s) — they are not the church's words.
        return {"url": final_url, "status": resp.status_code, "text": "", "links": [], "from_cache": False,
                "error": f"HTTP {resp.status_code}"}
    if "pdf" in ctype or final_url.lower().endswith(".pdf"):
        text, links = _pdf_text(resp.content, max_chars), []
    elif "html" in ctype or "xml" in ctype or not ctype or ctype.startswith("text/"):
        html = resp.text
        text = _extract_text(html, url, max_chars)
        links = _extract_links(html, final_url)
    else:  # images, audio, etc.
        return {"url": final_url, "status": resp.status_code, "text": "", "links": [], "from_cache": False,
                "error": f"unsupported content-type {ctype}"}

    result = {"url": final_url, "status": resp.status_code, "text": text, "links": links, "from_cache": False}
    if text.strip():                        # only cache real content
        cache_put(url, json.dumps(result))
    return result


def _pdf_text(data: bytes, max_chars: int) -> str:
    """Statements of faith are often PDFs."""
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:20])
        return scrub_pii(text)[:max_chars]
    except Exception:
        return ""
