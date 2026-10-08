import json

import httpx
import pytest

from app.web import Blocked, fetch, set_client


SAMPLE_HTML = """
<html><head><title>About</title></head><body>
<article><h1>About Us</h1><p>We are a welcoming congregation in Hesston.</p></article>
<a href="/beliefs">Beliefs</a>
<a href="https://example.church/contact">Contact</a>
</body></html>
"""


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()
    set_client(None)


@pytest.fixture
def fake_transport(monkeypatch):
    from app import web

    pages = {
        "https://example.church/": {"html": SAMPLE_HTML},
        "https://example.church/beliefs": {"html": "<html><body><p>Baptism for all ages.</p></body></html>"},
    }
    robots = {"example.church": "User-agent: *\nAllow: /\n"}

    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/robots.txt"):
                body = robots.get(request.url.host or "", "User-agent: *\nAllow: /\n")
                return httpx.Response(200, text=body, request=request)
            page = pages.get(url)
            if page is None:
                return httpx.Response(404, text="not found", request=request)
            return httpx.Response(page.get("status", 200), text=page["html"], request=request)

    web.set_client(httpx.Client(transport=Transport(), follow_redirects=True))
    monkeypatch.setattr(web, "RATE_LIMIT_SEC", 0.0)
    monkeypatch.setattr(web, "_robots", {})
    monkeypatch.setattr(web, "_last_fetch", {})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.church/prayer",
        "https://example.church/directory",
        "https://example.church/members",
        "https://example.church/login",
        "https://example.church/give",
        "https://example.church/donate",
        "https://example.church/child-check-in",
    ],
)
def test_blocklisted_urls_raise_blocked(url):
    with pytest.raises(Blocked):
        fetch(url)


def test_fetch_returns_shape(data_dir, fake_transport):
    from app.db import init

    init()
    result = fetch("https://example.church/")
    assert result["url"].startswith("https://example.church")
    assert result["status"] == 200
    assert "welcoming congregation" in result["text"].lower()
    assert isinstance(result["links"], list)
    assert any("beliefs" in link["href"] for link in result["links"])
    assert result["from_cache"] is False


def test_cached_second_fetch(data_dir, fake_transport):
    from app.db import init

    init()
    first = fetch("https://example.church/")
    second = fetch("https://example.church/")
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["text"] == first["text"]


def test_robots_disallowed(data_dir, monkeypatch):
    from app import web

    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/robots.txt"):
                return httpx.Response(200, text="User-agent: *\nDisallow: /private\n", request=request)
            return httpx.Response(200, text=SAMPLE_HTML, request=request)

    web.set_client(httpx.Client(transport=Transport(), follow_redirects=True))
    monkeypatch.setattr(web, "RATE_LIMIT_SEC", 0.0)
    monkeypatch.setattr(web, "_robots", {})
    with pytest.raises(Blocked):
        fetch("https://blocked.example/private/page")


# ---- code review R8 ----
@pytest.mark.parametrize("url", [
    "https://church.org/giving", "https://church.org/online-giving", "https://church.org/give/",
    "https://tithe.ly/give?c=123", "https://mychurch.churchcenter.com/people/forms/1", "https://pushpay.com/g/church",
])
def test_giving_and_people_pages_blocked(url):
    with pytest.raises(Blocked):
        fetch(url)


def test_error_pages_not_cached(data_dir, monkeypatch):
    from tests.fakes import FakeWeb
    FakeWeb({"https://x.org/a": {"status": 403, "html": "<html><body>Access denied by Cloudflare</body></html>"}}).install(monkeypatch)
    r = fetch("https://x.org/a")
    assert r["status"] == 403 and r["text"] == ""
    from app.db import cache_get
    assert cache_get("https://x.org/a") is None


def test_published_embedded_page_recovers_content_and_menu():
    from app.web import _published_embedded_html, _extract_text, _extract_links
    page = {"groups": [{"content": "<p>Sunday worship at 10:30am</p>", "default": "<p>Editor placeholder</p>"}]}
    pages = [{"uuid": "staff", "slug": "our-staff"}]
    menu = {"items": [{"details": {"pageUuid": "staff", "title": "Our Staff"}}]}
    html = "<script>window.Page = FW.Models.Page.findOrCreate(" + json.dumps(page) + ");FW.store.set('pages', new FW.Models.Pages(" + json.dumps(pages) + "));FW.store.set('menu', FW.Models.Menu.findOrCreate(" + json.dumps(menu) + "));</script>"
    recovered = _published_embedded_html(html)
    assert "10:30am" in _extract_text(recovered, "https://example.church", 10000)
    assert "Editor placeholder" not in recovered
    assert {"href": "https://example.church/our-staff", "text": "Our Staff"} in _extract_links(recovered, "https://example.church")

def test_invalid_embedded_json_does_not_execute():
    from app.web import _published_embedded_html
    assert _published_embedded_html("window.Page = FW.Models.Page.findOrCreate(alert('bad'))") == ""
