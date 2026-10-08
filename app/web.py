"""Polite web fetcher — robots, rate limit, cache, blocklist (INTERFACES §4)."""
from __future__ import annotations

import json
import ipaddress
import socket
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
    ua = get_settings().user_agent or "ChurchFocus/0.1"
    return ua if ua.startswith("Mozilla/") else f"{_BROWSER_UA} {ua}"


class Blocked(Exception):
    """URL is blocklisted or disallowed by robots.txt."""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.active = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            return
        text = ""
        self.links.append({"href": href, "text": text})
        self.active = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self.active = False

    def handle_data(self, data: str) -> None:
        if self.active and self.links:
            self.links[-1]["text"] = (self.links[-1]["text"] + data).strip()


_robots: dict[str, RobotFileParser] = {}
_last_fetch: dict[str, float] = {}
_client: httpx.Client | None = None


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _validate_public_url(url: str, *, resolve: bool = False) -> None:
    """Validate every outbound target, including redirects and robots requests."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or parsed.password is not None:
            raise Blocked("Only public HTTP(S) pages without credentials are permitted")
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")) or "%" in host:
            raise Blocked("Local/private hosts are not permitted")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise Blocked("Local/private addresses are not permitted")
        if resolve:
            try:
                addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            except OSError as exc:
                raise Blocked("Public host could not be resolved") from exc
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise Blocked("Host resolves to a local/private address")
    except (ValueError, TypeError) as exc:
        raise Blocked("Invalid public URL") from exc


def _guard_request(request: httpx.Request) -> None:
    _validate_public_url(str(request.url), resolve=True)
    if _is_blocklisted(str(request.url)):
        raise Blocked("Request target is not permitted")


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": _ua(), "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"},
            event_hooks={"request": [_guard_request]},
        )
    return _client


def set_client(client: httpx.Client | None) -> None:
    """Inject httpx client (tests)."""
    global _client
    _client = client
    if client is not None:
        # Injected fixture transports perform no DNS/network; literal/local targets still blocked.
        def guard_fixture(request):
            _validate_public_url(str(request.url))
            if _is_blocklisted(str(request.url)):
                raise Blocked("Request target is not permitted")
        client.event_hooks["request"] = list(client.event_hooks.get("request", [])) + [guard_fixture]


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
    return _robots[domain].can_fetch("ChurchFocus", url)


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


class _VisibleTextParser(HTMLParser):
    """Keep hero panels and public staff text omitted by article extraction."""
    excluded = {"script", "style", "nav", "footer", "head", "template", "aside"}
    def __init__(self):
        super().__init__()
        self.skip = []
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in self.excluded:
            self.skip.append(tag)
        if not self.skip and tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in self.skip:
            i = len(self.skip) - 1 - self.skip[::-1].index(tag)
            del self.skip[i:]
        if not self.skip and tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip() + " ")


def _published_embedded_html(html: str) -> str:
    """Read published Servant Keeper page/menu JSON; never execute scripts."""
    decoder = json.JSONDecoder()
    def read(marker):
        start = html.find(marker)
        if start < 0:
            return {}
        try:
            return decoder.raw_decode(html[start + len(marker):].lstrip())[0]
        except (ValueError, RecursionError):
            return {}
    page = read("window.Page = FW.Models.Page.findOrCreate(")
    parts = []
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("content"), str) and isinstance(value.get("title"), str):
                from html import escape
                title = value["title"]
                parts.append(title if "<" in title else "<h3>" + escape(title) + "</h3>")
            for key, item in value.items():
                if key == "content" and isinstance(item, str):
                    parts.append(item)
                elif key not in {"default", "defaults", "template", "settings"} and isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(page)
    pages = read("FW.store.set('pages', new FW.Models.Pages(")
    lookup = {x.get("uuid"): x.get("slug") for x in pages if isinstance(x, dict)} if isinstance(pages,list) else {}
    menu = read("FW.store.set('menu', FW.Models.Menu.findOrCreate(")
    from html import escape
    def links(items):
        for item in items:
            details = item.get("details") or {}
            href = details.get("href") or ("/" + lookup[details["pageUuid"]] if lookup.get(details.get("pageUuid")) else "")
            if href:
                parts.append('<a href="' + escape(href,quote=True) + '">' + escape(details.get("title") or "") + '</a>')
            links(item.get("children") or [])
    if isinstance(menu,dict):
        links(menu.get("items") or [])
    return "\n".join(parts)


def _extract_text(html: str, url: str, max_chars: int) -> str:
    main = trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
    if not main:
        main = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    parser = _VisibleTextParser()
    parser.feed(html)
    seen = set()
    supplement = []
    normalized_main = re.sub(r"\s+", " ", main.lower())
    for line in "".join(parser.parts).splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        key = line.lower()
        if not line or key in seen or key in normalized_main:
            continue
        seen.add(key)
        if re.search(r"copyright|all rights reserved|privacy policy|powered by|cookie preferences", line, re.I):
            continue
        supplement.append(line)
    # Preserve supplements first if the article itself consumes the text budget.
    text = "\n".join(supplement + [main])
    return scrub_pii(text)[:max_chars]


def fetch(url: str, max_chars: int = 20000, *, max_age_days: float | None = None) -> dict:
    """Fetch a page; returns {url, status, text, links, from_cache}. Raises Blocked."""
    _validate_public_url(url)
    if _is_blocklisted(url):
        raise Blocked(f"blocklisted URL: {url}")

    cached = cache_get(url)
    if cached:
        fetched_at, body = cached
        age_days = (time.time() - fetched_at.timestamp()) / 86400
        if age_days <= (get_settings().cache_max_age_days if max_age_days is None else max_age_days):
            payload = json.loads(body)
            if payload.get("extract_version", 0) >= 3 and (not payload.get("text_truncated", len(payload.get("text", "")) >= payload.get("text_limit", 20000)) or payload.get("text_limit", 20000) >= max_chars):
                payload["from_cache"] = True
                payload.setdefault("checked_at", fetched_at.isoformat())
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
        html += "\n" + _published_embedded_html(html)
        text = _extract_text(html, url, max_chars)
        links = _extract_links(html, final_url)
    else:  # images, audio, etc.
        return {"url": final_url, "status": resp.status_code, "text": "", "links": [], "from_cache": False,
                "error": f"unsupported content-type {ctype}"}

    from datetime import datetime, timezone
    result = {"url": final_url, "status": resp.status_code, "text": text, "links": links, "from_cache": False,
              "checked_at": datetime.now(timezone.utc).isoformat(), "text_truncated": len(text) >= max_chars,
              "text_limit": max_chars, "extract_version": 3}
    if not re.sub(r"jump directly to main content|toggle navigation|\s+", "", text, flags=re.I):
        result["error"] = "Insufficient readable website content"
    if text.strip() and not result.get("error"):  # never cache page shells
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


def youtube_video_id(url: str) -> str | None:
    """Accept only public YouTube video URLs; never arbitrary extraction targets."""
    from urllib.parse import parse_qs
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        value = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com", "youtube-nocookie.com"}:
        value = parse_qs(parsed.query).get("v", [""])[0]
        if not value and parsed.path.startswith(("/embed/", "/live/", "/shorts/")):
            value = parsed.path.split("/")[2]
    else:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_-]{11}", value) else None


def youtube_videos(url: str, limit: int = 25) -> dict:
    """Discover public individual videos/playlist entries and completed livestreams."""
    from yt_dlp import YoutubeDL
    host = (urlparse(url).hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com", "youtube-nocookie.com"}:
        raise Blocked("Not a YouTube source")
    video_id = youtube_video_id(url)
    if video_id:
        return {"items": [{"video_id": video_id, "page_url": "https://www.youtube.com/watch?v=" + video_id, "video_url": "https://www.youtube.com/watch?v=" + video_id, "title": "Service recording", "date": "", "speaker": ""}]}
    cap = min(max(int(limit), 1), 25)
    base = url.rstrip("/")
    path = urlparse(base).path
    targets = [base] if path.endswith(("/videos", "/streams")) or path == "/playlist" else [base + "/streams", base + "/videos"]
    found, errors = {}, []
    class QuietLogger:
        def debug(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
    for target in targets:
        try:
            with YoutubeDL({"quiet": True, "logger": QuietLogger(), "extract_flat": True, "playlistend": cap,
                            "socket_timeout": 20, "retries": 1, "extractor_retries": 1, "ignoreerrors": True}) as ydl:
                result = ydl.extract_info(target, download=False) or {}
            for entry in result.get("entries") or []:
                if not entry or entry.get("live_status") in {"is_live", "is_upcoming"}:
                    continue
                vid = entry.get("id", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                    continue
                watch = "https://www.youtube.com/watch?v=" + vid
                found[vid] = {"video_id": vid, "page_url": watch, "video_url": watch,
                              "title": entry.get("title") or "Service recording", "date": entry.get("upload_date") or "",
                              "speaker": "", "minutes": (entry.get("duration") or 0) / 60}
        except Exception as exc:
            errors.append(type(exc).__name__)
    return {"items": list(found.values())[:cap], **({"error": "YouTube video discovery failed: " + ", ".join(errors)} if not found else {})}


def youtube_captions(url: str) -> dict:
    """Fetch available English public captions with timestamps; no video download."""
    from youtube_transcript_api import YouTubeTranscriptApi
    video_id = youtube_video_id(url)
    if not video_id:
        raise Blocked("Expected a YouTube video URL")
    from requests import Session
    class BoundedSession(Session):
        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", 30)
            return super().request(*args, **kwargs)
    with BoundedSession() as session:
        transcript = YouTubeTranscriptApi(http_client=session).fetch(video_id, languages=["en", "en-US", "en-GB"])
        segments = [{"text": x.text, "start": x.start, "duration": x.duration} for x in transcript]
    from html import unescape
    return {"text": "\n".join(unescape(x["text"]) for x in segments), "segments": segments,
            "minutes": max((x["start"] + x["duration"] for x in segments), default=0) / 60,
            "source": "youtube_captions", "is_generated": transcript.is_generated}
