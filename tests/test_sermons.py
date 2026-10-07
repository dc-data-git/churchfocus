"""Tests for app/stage3/sermons.py (BUILD_PLAN T5). No network."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.models import Church, DenomGuess
from app.stage3 import sermons
from tests.fakes import FakeLLM, FakeWeb

FIX = Path(__file__).parent / "fixtures"
FEED_XML = (FIX / "sermon_feed.xml").read_text(encoding="utf-8")
SITE_HTML = (FIX / "sermon_site.html").read_text(encoding="utf-8")


@pytest.fixture
def tmp_data(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _church(**kw) -> Church:
    defaults = dict(
        church_id="fixture-church",
        name="Fixture Church",
        website="https://fixture.church/",
        denomination=DenomGuess(label="Unknown", confidence=0.0),
    )
    defaults.update(kw)
    return Church(**defaults)


def test_find_sermon_feeds_from_site_pages():
    pages = [
        {
            "url": "https://fixture.church/sermons",
            "links": [
                {"href": "https://fixture.church/feed", "text": "Podcast RSS Feed"},
                {"href": "https://podcasts.apple.com/us/podcast/fixture-church/id123", "text": "Apple Podcasts"},
                {"href": "https://www.youtube.com/@FixtureChurch", "text": "YouTube Channel"},
                {"href": "https://www.sermonaudio.com/church/fixture", "text": "SermonAudio"},
                {"href": "https://subsplash.com/fixture/live", "text": "Subsplash Live"},
                {"href": "https://fixture.church/about", "text": "About"},
            ],
        }
    ]
    out = sermons.find_sermon_feeds(_church(), site_pages=pages)
    kinds = {f["kind"] for f in out["feeds"]}
    urls = {f["url"] for f in out["feeds"]}
    assert "rss" in kinds
    assert "podcast" in kinds
    assert "youtube" in kinds
    assert "sermonaudio" in kinds
    assert "subsplash" in kinds
    assert "https://fixture.church/feed" in urls
    assert out["feeds"][0]["kind"] == "rss"


def test_find_sermon_feeds_adds_root_feed_urls():
    out = sermons.find_sermon_feeds(_church(), site_pages=[])
    urls = [f["url"] for f in out["feeds"]]
    assert "https://fixture.church/feed" in urls


def test_get_sermons_newest_first_with_metadata():
    out = sermons.get_sermons("https://fixture.church/feed", limit=10, feed_text=FEED_XML)
    items = out["items"]
    assert len(items) == 3
    dates = [item["date"] for item in items]
    assert dates == sorted(dates, reverse=True)
    assert items[0]["title"] == "Hope in Hard Times"
    assert items[0]["speaker"] == "Pastor John Smith"
    assert items[0]["audio_url"] == "https://fixture.church/audio/sermon-2024-09-22.mp3"
    assert items[1]["speaker"] == "Rev. Jane Doe"
    assert items[2]["transcript_url"] == "https://fixture.church/transcripts/sermon-2024-09-08.txt"


def test_get_sermons_respects_deep_max_sermons(tmp_data, monkeypatch):
    monkeypatch.setenv("DEEP_MAX_SERMONS", "2")
    get_settings.cache_clear()
    out = sermons.get_sermons("https://fixture.church/feed", feed_text=FEED_XML)
    assert len(out["items"]) == 2


def test_transcribe_sermon_prefers_existing_text(tmp_data):
    item = {"title": "Cached", "text": "Full transcript already here.", "minutes": 28, "speaker": "Pastor"}
    out = sermons.transcribe_sermon(item, church_id="fixture-church")
    assert out["text"] == "Full transcript already here."
    assert out["source"] == "item"
    assert out["minutes"] == 28

    log_path = tmp_data / "logs" / "sermon_throughput.jsonl"
    assert log_path.is_file()
    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["action"] == "transcript_existing"
    assert row["audio_minutes"] == 28


def test_transcribe_sermon_prefers_transcript_url(tmp_data, monkeypatch):
    from app.db import init

    init()
    FakeWeb(
        pages={
            "https://fixture.church/transcripts/sermon.txt": {
                "html": "<html><body><p>Transcript from the church website.</p></body></html>"
            }
        },
        robots={"fixture.church": "User-agent: *\nAllow: /\n"},
    ).install(monkeypatch)

    item = {
        "title": "With transcript",
        "transcript_url": "https://fixture.church/transcripts/sermon.txt",
        "speaker": "Guest",
    }
    out = sermons.transcribe_sermon(item, church_id="fixture-church")
    assert "transcript from the church" in out["text"].lower()
    assert out["source"] == "transcript_url"


def test_transcribe_sermon_downloads_and_transcribes(tmp_data, monkeypatch):
    audio_bytes = b"fake-audio-content-for-test"

    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=audio_bytes, request=request)

    client = httpx.Client(transport=Transport())
    calls: list[str] = []

    def fake_transcribe(path: str) -> str:
        calls.append(path)
        return "spoken sermon words"

    monkeypatch.setattr("app.stage3.sermons.llm_transcribe", fake_transcribe)

    item = {
        "title": "Download me",
        "audio_url": "https://fixture.church/audio/test.mp3",
        "speaker": "Pastor",
    }
    out = sermons.transcribe_sermon(item, church_id="c1", http_client=client)
    assert out["text"] == "spoken sermon words"
    assert out["source"] == "transcribed"
    assert len(calls) == 1


def test_prepare_audio_chunks_splits_large_file(tmp_data):
    ffmpeg = sermons._ffmpeg_exe()
    src = tmp_data / "large_input.mp3"
    # ~30 minutes at 128 kbps mono -> >24 MB
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "1800",
            "-acodec",
            "libmp3lame",
            "-b:a",
            "128k",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    assert src.stat().st_size > sermons.AUDIO_MAX_BYTES

    chunks = sermons.prepare_audio_chunks(src)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.stat().st_size <= sermons.AUDIO_MAX_BYTES + 512_000
        duration = sermons._audio_duration_seconds(chunk)
        assert duration <= sermons.CHUNK_MAX_SECONDS + 5


def _six_analyses() -> list[dict]:
    base = {
        "speaker_role": "lead_pastor",
        "minutes": 30,
        "style": "expository",
        "main_texts": ["Romans 8"],
        "scripture_density": "high",
        "audience": "believers_teaching",
        "politics_mentions": 0,
        "politics_examples": [],
        "topics": ["faith"],
        "stated_positions": [],
    }
    speakers = [
        ("Pastor Tom", "male"),
        ("Pastor Tom", "male"),
        ("Rev. Sarah", "female"),
        ("Pastor Tom", "male"),
        ("Rev. Anna", "female"),
        ("Pastor Tom", "male"),
    ]
    return [
        {**base, "speaker": name, "speaker_gender": gender}
        for name, gender in speakers
    ]


def test_analyse_sermons_aggregates_women_preach(tmp_data, monkeypatch):
    fake_analyses = _six_analyses()
    idx = {"n": 0}

    def fake_complete_json(task, messages, schema, tier="fast", **kw):
        out = fake_analyses[idx["n"]]
        idx["n"] += 1
        return out

    monkeypatch.setattr("app.stage3.sermons.complete_json", fake_complete_json)

    texts = [{"title": f"S{i}", "text": f"sermon body {i}", "minutes": 30} for i in range(6)]
    features = [
        "women.preach",
        "logistics.sermon_length",
        "preaching.style",
        "preaching.audience",
        "preaching.scripture_density",
        "preaching.politics_frequency",
    ]
    out = sermons.analyse_sermons(texts, features, church_id="fixture-church")
    assert out["sermons_analysed"] == 6

    by_feature = {e.feature: e for e in out["evidence"]}
    wp = by_feature["women.preach"]
    assert wp.value == "occasionally"
    assert wp.tier == "D"
    assert wp.how == "observed"
    assert wp.note.startswith("2 of 6 sermons")

    assert by_feature["logistics.sermon_length"].value == "20_35"
    assert by_feature["preaching.style"].value == "expository"
    assert by_feature["preaching.politics_frequency"].value == "rare"

    log_path = tmp_data / "logs" / "sermon_throughput.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    analyse_rows = [r for r in rows if r["action"] == "analyse"]
    assert len(analyse_rows) == 1
    assert analyse_rows[0]["audio_minutes"] == 180.0


def test_analyse_sermons_with_fake_llm(tmp_data, monkeypatch):
    responses = {
        "sermon_analyse": {
            "speaker": "Jane",
            "speaker_role": "other_staff",
            "speaker_gender": "female",
            "minutes": 25,
            "style": "topical",
            "main_texts": ["John 3:16"],
            "scripture_density": "medium",
            "audience": "mixed",
            "politics_mentions": 1,
            "politics_examples": ["vote your conscience"],
            "topics": ["grace"],
            "stated_positions": [],
        }
    }
    fake = FakeLLM(responses)
    monkeypatch.setattr("app.stage3.sermons.complete_json", fake.complete_json)

    out = sermons.analyse_sermons(
        [{"title": "One", "text": "sermon transcript", "minutes": 25}],
        ["women.preach", "preaching.politics_frequency"],
        church_id="c1",
    )
    assert len(fake.calls) == 1
    assert fake.calls[0]["task"] == "sermon_analyse"
    feats = [e.feature for e in out["evidence"]]
    assert "women.preach" not in feats          # 1 identifiable speaker < 5: no claim (R7)
    assert "preaching.politics_frequency" in feats


def test_unknown_speakers_never_mean_women_never_preach(tmp_data, monkeypatch):
    unknown = {"speaker": "", "speaker_role": "unknown", "speaker_gender": "unknown", "minutes": 30, "style": "topical",
               "main_texts": [], "scripture_density": "medium", "audience": "mixed", "politics_mentions": 0,
               "politics_examples": [], "topics": [], "stated_positions": []}
    monkeypatch.setattr("app.stage3.sermons.complete_json", lambda *a, **k: dict(unknown))
    out = sermons.analyse_sermons([{"title": f"S{i}", "text": "x", "minutes": 30} for i in range(8)],
                                  ["women.preach", "preaching.style"], church_id="c1")
    feats = {e.feature: e for e in out["evidence"]}
    assert "women.preach" not in feats
    from app.features import data_points
    assert data_points(feats["preaching.style"].note) == 8    # notes are countable -> can settle


def test_audio_suffix_from_url():
    assert sermons._audio_suffix("https://x.org/a/sermon.m4a?x=1") == ".m4a"
    assert sermons._audio_suffix("https://x.org/a/play", "audio/mpeg") == ".mp3"


def test_transcribe_sermons_parallel_respects_cap(tmp_data, monkeypatch):
    monkeypatch.setenv("DEEP_MAX_SERMONS", "2")
    get_settings.cache_clear()

    def fake_transcribe(item, **kw):
        return {"text": item["title"], "minutes": 1.0, "speaker": "", "source": "item"}

    monkeypatch.setattr("app.stage3.sermons.transcribe_sermon", fake_transcribe)
    items = [{"title": f"S{i}", "text": f"t{i}"} for i in range(5)]
    out = sermons.transcribe_sermons_parallel(items, church_id="c1")
    assert len(out) == 2
