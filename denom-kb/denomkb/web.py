"""Polite web access: robots.txt, per-domain rate limit, disk cache, HTML/PDF -> text."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("denomkb.web")


class Web:
    def __init__(self, cfg: dict, cache_dir: Path, offline: bool = False):
        self.ua = cfg.get("user_agent", "denom-kb/0.1 (research; church discovery hackathon)")
        self.delay = cfg.get("per_domain_delay_s", 1.5)
        self.timeout = cfg.get("timeout_s", 20)
        self.max_bytes = cfg.get("max_bytes", 5_000_000)
        self.offline = offline
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.s = requests.Session()
        self.s.headers["User-Agent"] = self.ua

    # ------------------------------------------------------------ helpers
    def _cpath(self, url: str) -> Path:
        return self.cache / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code >= 400:
                    rp = None
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def _wait(self, url: str):
        host = urlparse(url).netloc
        dt = time.time() - self._last.get(host, 0)
        if dt < self.delay:
            time.sleep(self.delay - dt)
        self._last[host] = time.time()

    # ------------------------------------------------------------ public
    def get_json(self, url: str, params: dict | None = None) -> dict | None:
        key = url + ("?" + json.dumps(params, sort_keys=True) if params else "")
        cp = self._cpath(key)
        if cp.exists():
            return json.loads(cp.read_text(encoding="utf-8"))["json"]
        if self.offline:
            return None
        data = None
        for attempt in range(5):
            self._wait(url)
            try:
                r = self.s.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = float(r.headers.get("Retry-After", 0) or 0) or 5 * 2 ** attempt
                    log.info("HTTP %s from %s, waiting %.0fs", r.status_code, urlparse(url).netloc, wait)
                    time.sleep(min(wait, 120))
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except (requests.RequestException, ValueError) as e:
                log.warning("json fetch failed %s: %s", url, e)
                time.sleep(3)
        if data is None:
            return None   # not cached, so a later run retries
        cp.write_text(json.dumps({"url": key, "json": data}), encoding="utf-8")
        return data

    def get_page(self, url: str) -> dict | None:
        """Returns {url, final_url, title, text, links} or None. Cached (including failures)."""
        cp = self._cpath(url)
        if cp.exists():
            d = json.loads(cp.read_text(encoding="utf-8"))
            return d.get("page")
        if self.offline:
            return None
        page = None
        try:
            if not self._allowed(url):
                log.info("robots.txt disallows %s", url)
            else:
                self._wait(url)
                r = self.s.get(url, timeout=self.timeout, stream=True)
                content = r.raw.read(self.max_bytes, decode_content=True)
                if r.status_code < 400:
                    ctype = r.headers.get("Content-Type", "").lower()
                    if "pdf" in ctype or url.lower().endswith(".pdf"):
                        page = {"url": url, "final_url": r.url, "title": url.rsplit("/", 1)[-1],
                                "text": pdf_text(content), "links": []}
                    elif "html" in ctype or not ctype:
                        page = html_page(content, r.url)
                        page["url"] = url
        except requests.RequestException as e:
            log.warning("fetch failed %s: %s", url, e)
        cp.write_text(json.dumps({"url": url, "page": page}), encoding="utf-8")
        return page


def html_page(content: bytes, base_url: str) -> dict:
    soup = BeautifulSoup(content, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "form", "iframe"]):
        t.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if href.startswith("http"):
            links.append({"url": href, "text": a.get_text(" ", strip=True)[:120]})
    for t in soup(["nav", "header", "footer", "aside"]):
        t.decompose()
    title = soup.title.get_text(strip=True) if soup.title else base_url
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return {"final_url": base_url, "title": title[:200], "text": text, "links": links}


def pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:60])
    except Exception as e:  # noqa: BLE001
        log.warning("pdf parse failed: %s", e)
        return ""
