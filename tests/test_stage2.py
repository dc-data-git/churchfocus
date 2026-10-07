"""Stage 2 — site_pages + medium_search (BUILD_PLAN T4)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models import Church, DenomGuess, Evidence, Preference, PreferenceProfile
from app.stage2.summary import medium_search, quote_verbatim
from app.stage2.website import site_pages
from app.web import Blocked, fetch
from tests.fakes import FakeLLM, FakeWeb

FIX = Path(__file__).parent / "fixtures"


def _load_html(site: str, name: str) -> str:
    return (FIX / site / name).read_text(encoding="utf-8")


def _grace_pages() -> dict[str, dict]:
    base = "https://grace-baptist.fixture"
    return {
        f"{base}/": {"html": _load_html("site_grace_baptist", "home.html")},
        f"{base}/beliefs": {"html": _load_html("site_grace_baptist", "beliefs.html")},
        f"{base}/staff": {"html": _load_html("site_grace_baptist", "staff.html")},
        f"{base}/sermons": {"html": _load_html("site_grace_baptist", "sermons.html")},
    }


def _hope_pages() -> dict[str, dict]:
    base = "https://hope-community.fixture"
    return {
        f"{base}/": {"html": _load_html("site_hope_community", "home.html")},
        f"{base}/about": {"html": _load_html("site_hope_community", "home.html")},
        f"{base}/what-we-believe": {"html": _load_html("site_hope_community", "beliefs.html")},
        f"{base}/leadership": {"html": _load_html("site_hope_community", "leadership.html")},
    }


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db import init

    init()
    yield tmp_path
    get_settings.cache_clear()


MARRIAGE_QUOTE = (
    "We believe marriage is a covenant between one man and one woman, "
    "ordained by God for the union of husband and wife."
)
HOPE_MARRIAGE_QUOTE = "We affirm that marriage is between one man and one woman as taught in Scripture."


def _page_extract_grace() -> dict:
    return {
        "features": [
            {
                "feature": "lgbtq.marriage",
                "status": "stated",
                "value": "traditional",
                "quote": MARRIAGE_QUOTE,
                "url": "https://grace-baptist.fixture/beliefs",
            },
            {
                "feature": "lgbtq.inclusion",
                "status": "stated",
                "value": "full",
                "quote": "We welcome LGBTQ members into full leadership.",
                "url": "https://grace-baptist.fixture/beliefs",
            },
            {
                "feature": "theology.baptism",
                "status": "stated",
                "value": "believers_immersion",
                "quote": "We practice believers baptism by immersion for those who profess faith in Jesus Christ.",
                "url": "https://grace-baptist.fixture/beliefs",
            },
        ]
    }


def _page_extract_hope() -> dict:
    return {
        "features": [
            {
                "feature": "lgbtq.marriage",
                "status": "stated",
                "value": "traditional",
                "quote": HOPE_MARRIAGE_QUOTE,
                "url": "https://hope-community.fixture/what-we-believe",
            },
            {
                "feature": "theology.baptism",
                "status": "stated",
                "value": "infant_and_adult",
                "quote": "Baptism is available for both infants and adults who wish to follow Christ.",
                "url": "https://hope-community.fixture/what-we-believe",
            },
        ]
    }


def _church(church_id: str, name: str, website: str) -> Church:
    return Church(
        church_id=church_id,
        name=name,
        address="123 Main St",
        website=website,
        denomination=DenomGuess(label="Baptist", confidence=0.7, method="name"),
    )


def _profile() -> PreferenceProfile:
    return PreferenceProfile(
        session_id="t-stage2",
        origin={"text": "Hesston, KS"},
        preferences=[
            Preference(feature="lgbtq.marriage", want=["traditional"], weight="important"),
            Preference(feature="lgbtq.inclusion", want=["full"], weight="important"),
            Preference(feature="theology.baptism", want=["believers_immersion"], weight="nice_to_have"),
        ],
    )


class TestSitePages:
    def test_grace_finds_beliefs_and_staff(self, data_dir, monkeypatch):
        FakeWeb(pages=_grace_pages(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        pages = site_pages("https://grace-baptist.fixture/")
        kinds = {p["kind"] for p in pages}
        urls = {p["url"] for p in pages}
        assert "beliefs" in kinds
        assert "staff" in kinds
        assert any("belief" in u for u in urls)
        assert any("staff" in u for u in urls)
        assert len(pages) <= 8

    def test_hope_finds_beliefs_and_staff(self, data_dir, monkeypatch):
        FakeWeb(pages=_hope_pages(), robots={"hope-community.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        pages = site_pages("https://hope-community.fixture/")
        kinds = {p["kind"] for p in pages}
        assert "beliefs" in kinds
        assert "staff" in kinds

    def test_never_fetches_blocklisted_pages(self, data_dir, monkeypatch):
        fetched: list[str] = []
        pages = _grace_pages()
        pages["https://grace-baptist.fixture/prayer"] = {"html": "<html><body>secret prayers</body></html>"}
        pages["https://grace-baptist.fixture/give"] = {"html": "<html><body>give form</body></html>"}

        FakeWeb(pages=pages, robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)

        import httpx
        from app import web

        orig_transport = web._get_client()._transport

        class LoggingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                fetched.append(str(request.url))
                return orig_transport.handle_request(request)

        web.set_client(httpx.Client(transport=LoggingTransport(), follow_redirects=True))
        site_pages("https://grace-baptist.fixture/")
        fetched_paths = [u for u in fetched if "grace-baptist" in u and not u.endswith("robots.txt")]
        assert not any("/prayer" in u for u in fetched_paths)
        assert not any("/give" in u for u in fetched_paths)
        with pytest.raises(Blocked):
            fetch("https://grace-baptist.fixture/prayer")


class TestQuoteVerbatim:
    def test_verbatim_match(self):
        text = "We believe marriage is a covenant between one man and one woman, ordained by God."
        assert quote_verbatim(MARRIAGE_QUOTE, text)

    def test_non_verbatim_rejected(self):
        text = "We believe marriage is a covenant between one man and one woman, ordained by God."
        fake = "Marriage is always between two people who love each other regardless of gender."
        assert not quote_verbatim(fake, text)


class TestMediumSearch:
    def test_marriage_rule_and_verbatim(self, data_dir, monkeypatch):
        FakeWeb(pages=_grace_pages(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        fake_llm = FakeLLM({"page_extract": _page_extract_grace()})
        monkeypatch.setattr("app.stage2.summary.complete_json", fake_llm.complete_json)

        church = _church("grace-1", "Grace Baptist", "https://grace-baptist.fixture/")
        card = medium_search(church, _profile())

        features = {e.feature: e for e in card["evidence"]}
        assert "lgbtq.marriage" in features
        assert features["lgbtq.marriage"].value == "traditional"
        assert features["lgbtq.marriage"].tier == "A"
        assert "lgbtq.inclusion" not in features
        assert "lgbtq.inclusion" in card["open"]
        fetched = site_pages(church.website)
        for ev in card["evidence"]:
            assert any(quote_verbatim(ev.quote, p["text"]) for p in fetched)

        assert card["deep_dive_candidate"] in ("strong", "possible", "weak")
        assert card["reason"]

    def test_drops_non_verbatim_llm_output(self, data_dir, monkeypatch):
        FakeWeb(pages=_grace_pages(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        bad = {
            "features": [
                {
                    "feature": "lgbtq.marriage",
                    "status": "stated",
                    "value": "traditional",
                    "quote": "We fully affirm same-sex marriage in our church.",
                    "url": "https://grace-baptist.fixture/beliefs",
                }
            ]
        }
        monkeypatch.setattr("app.stage2.summary.complete_json", FakeLLM({"page_extract": bad}).complete_json)
        card = medium_search(_church("grace-2", "Grace Baptist", "https://grace-baptist.fixture/"), _profile())
        assert not any(e.feature == "lgbtq.marriage" for e in card["evidence"])

    def test_hope_community_site(self, data_dir, monkeypatch):
        FakeWeb(pages=_hope_pages(), robots={"hope-community.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        monkeypatch.setattr("app.stage2.summary.complete_json", FakeLLM({"page_extract": _page_extract_hope()}).complete_json)
        card = medium_search(_church("hope-1", "Hope Community", "https://hope-community.fixture/"), _profile())
        marriage = next((e for e in card["evidence"] if e.feature == "lgbtq.marriage"), None)
        assert marriage is not None
        assert marriage.value == "traditional"
        assert "lgbtq.inclusion" not in {e.feature for e in card["evidence"]}

    def test_strong_candidate_with_sermons_and_open_important(self, data_dir, monkeypatch):
        FakeWeb(pages=_grace_pages(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        monkeypatch.setattr("app.stage2.summary.complete_json", FakeLLM({"page_extract": {"features": []}}).complete_json)
        profile = PreferenceProfile(
            session_id="t",
            preferences=[Preference(feature="worship.style", want=["contemporary"], weight="important")],
        )
        card = medium_search(_church("grace-3", "Grace Baptist", "https://grace-baptist.fixture/"), profile)
        assert card["deep_dive_candidate"] == "strong"
        assert "worship.style" in card["open"]

    def test_weak_without_beliefs_or_sermons(self, data_dir, monkeypatch):
        base = "https://minimal.fixture"
        FakeWeb(
            pages={f"{base}/": {"html": "<html><body><p>Hello</p><a href='/contact'>Contact</a></body></html>"}},
            robots={"minimal.fixture": "User-agent: *\nAllow: /\n"},
        ).install(monkeypatch)
        monkeypatch.setattr("app.stage2.summary.complete_json", FakeLLM({"page_extract": {"features": []}}).complete_json)
        card = medium_search(_church("min-1", "Minimal Church", f"{base}/"), _profile())
        assert card["deep_dive_candidate"] == "weak"


class TestCardsTemplate:
    def test_cards_template_renders(self):
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader(str(Path(__file__).parent.parent / "app" / "templates")))
        tpl = env.get_template("cards.html")
        ev = Evidence(
            feature="lgbtq.marriage",
            value="traditional",
            tier="A",
            quote=MARRIAGE_QUOTE,
            url="https://grace-baptist.fixture/beliefs",
            source_kind="statement_of_faith",
            how="stated",
            checked_at=datetime.now(timezone.utc),
        )
        from app.match import score

        church = _church("grace-1", "Grace Baptist", "https://grace-baptist.fixture/")
        church = church.model_copy(update={"evidence": [ev]})
        profile = _profile()
        html = tpl.render(
            cards=[
                {
                    "church": church,
                    "match": score(church, profile),
                    "settled": ["lgbtq.marriage"],
                    "open": ["lgbtq.inclusion"],
                    "evidence": [ev],
                    "deep_dive_candidate": "strong",
                    "reason": "Sermon page found; important features open",
                }
            ]
        )
        assert "Settled" in html
        assert "Open" in html
        assert "tier A" in html or "tier-A" in html
        assert "Strong" in html
        assert "lgbtq.marriage" in html
