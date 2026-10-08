import pytest
from app import web
from app.stage3 import sermons

@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=PvwE3JSyZto", "https://youtu.be/PvwE3JSyZto", "https://www.youtube.com/embed/PvwE3JSyZto", "https://www.youtube.com/live/PvwE3JSyZto"])
def test_valid_video_urls(url):
    assert web.youtube_video_id(url) == "PvwE3JSyZto"

def test_lookalike_host_rejected():
    assert web.youtube_video_id("https://youtube.com.evil.test/watch?v=PvwE3JSyZto") is None
    with pytest.raises(web.Blocked):
        web.youtube_videos("https://youtube.com.evil.test/watch?v=PvwE3JSyZto")

def test_video_url_routes_without_rss(monkeypatch):
    monkeypatch.setattr(web, "youtube_videos", lambda u, n: {"items": [{"video_id": "PvwE3JSyZto"}]})
    monkeypatch.setattr(sermons, "_fetch_feed_text", lambda u: pytest.fail("YouTube cannot be parsed as RSS"))
    assert sermons.get_sermons("https://www.youtube.com/@church", 3)["items"][0]["video_id"] == "PvwE3JSyZto"

def test_captions_preferred_and_download_avoided(monkeypatch):
    monkeypatch.setattr(web, "youtube_captions", lambda u: {"text": "Teaching from Scripture", "minutes": 40, "source": "youtube_captions"})
    monkeypatch.setattr(sermons, "log_throughput", lambda **kw: None)
    monkeypatch.setattr(sermons, "_download_audio", lambda *a, **kw: pytest.fail("No download needed with captions"))
    result = sermons.transcribe_sermon({"video_url": "https://youtu.be/PvwE3JSyZto"})
    assert result["text"] == "Teaching from Scripture" and result["source"] == "youtube_captions"

def test_caption_failure_reported(monkeypatch):
    def fail(url): raise RuntimeError("captions unavailable")
    monkeypatch.setattr(web, "youtube_captions", fail)
    result = sermons.transcribe_sermon({"video_url": "https://youtu.be/PvwE3JSyZto"})
    assert result["source"] == "youtube_captions_failed" and result["error"] == "RuntimeError"
