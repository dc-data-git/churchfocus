"""Sermon feeds, transcription, and analysis (INTERFACES §4, BUILD_PLAN T5)."""
from __future__ import annotations

import json
import re
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
import imageio_ffmpeg

from app.config import get_settings
from app.llm import complete_json, load_prompt, transcribe as llm_transcribe
from app.models import Church, Evidence, now
from app.web import Blocked, fetch

AUDIO_MAX_BYTES = 24 * 1024 * 1024
CHUNK_MAX_SECONDS = 20 * 60
DOWNLOAD_MAX_BYTES = 150 * 1024 * 1024   # R7: never pull a 1 GB video into memory
AUDIO_EXTS = (".mp3", ".m4a", ".mp4", ".mpeg", ".mpga", ".wav", ".webm", ".ogg", ".aac")
SPEAKER_KNOWN_MIN = 5                    # women.preach only from >= 5 sermons with an identifiable speaker

_FEED_HINTS: list[tuple[str, re.Pattern[str]]] = [
    ("rss", re.compile(r"(?i)(/feed\b|/rss\b|\.xml\b|type=rss|podcast[-_]?feed)")),
    ("podcast", re.compile(r"(?i)(podcasts\.apple\.com|open\.spotify\.com|podcast)")),
    ("youtube", re.compile(r"(?i)(youtube\.com/(channel/|@|c/)|youtu\.be/)")),
    ("sermonaudio", re.compile(r"(?i)sermonaudio\.com")),
    ("subsplash", re.compile(r"(?i)subsplash\.com")),
    ("page", re.compile(r"(?i)(/sermons\b|/messages\b|/media\b|/watch\b|/livestream\b)")),
]

SERMON_ANALYSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "speaker",
        "speaker_role",
        "speaker_gender",
        "minutes",
        "style",
        "main_texts",
        "scripture_density",
        "audience",
        "politics_mentions",
        "politics_examples",
        "topics",
        "stated_positions",
    ],
    "properties": {
        "speaker": {"type": "string"},
        "speaker_role": {"type": "string"},
        "speaker_gender": {"type": "string", "enum": ["female", "male", "unknown"]},
        "minutes": {"type": "number"},
        "style": {"type": "string"},
        "main_texts": {"type": "array"},
        "scripture_density": {"type": "string"},
        "audience": {"type": "string"},
        "politics_mentions": {"type": "number"},
        "politics_examples": {"type": "array"},
        "topics": {"type": "array"},
        "stated_positions": {"type": "array"},
    },
}


def _ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _audio_cache_dir() -> Path:
    path = get_settings().data_dir / "cache" / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _throughput_log_path() -> Path:
    path = get_settings().data_dir / "logs" / "sermon_throughput.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def log_throughput(
    *,
    church_id: str | None,
    sermon_title: str,
    audio_minutes: float,
    wall_ms: int,
    cost_usd: float = 0.0,
    action: str = "transcribe",
) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "church_id": church_id,
        "action": action,
        "sermon_title": sermon_title,
        "audio_minutes": round(audio_minutes, 2),
        "wall_minutes": round(wall_ms / 60_000, 3),
        "cost_usd": round(cost_usd, 6),
        "throughput": round(audio_minutes / (wall_ms / 60_000), 3) if wall_ms > 0 else 0.0,
    }
    with _throughput_log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _feed_kind(url: str, link_text: str = "") -> str | None:
    hay = f"{url} {link_text}"
    for kind, pattern in _FEED_HINTS:
        if pattern.search(hay):
            return kind
    return None


def _normalize_url(base: str, href: str) -> str:
    return urljoin(base, href)


def find_sermon_feeds(
    church: Church | dict[str, Any],
    *,
    site_pages: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Discover sermon/media feeds from church website links."""
    if isinstance(church, Church):
        website = church.website
        church_id = church.church_id
    else:
        website = church.get("website")
        church_id = church.get("church_id", "")

    feeds: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_feed(url: str, kind: str) -> None:
        if url in seen:
            return
        seen.add(url)
        feeds.append({"url": url, "kind": kind})

    if site_pages is None and website:
        pages: list[dict[str, Any]] = []
        try:
            home = fetch(website)
            pages = [home]
            for link in home.get("links", []):
                kind = _feed_kind(link["href"], link.get("text", ""))
                if kind == "page":
                    page_url = _normalize_url(home["url"], link["href"])
                    try:
                        pages.append(fetch(page_url))
                    except Blocked:
                        continue
        except (Blocked, httpx.HTTPError):
            pages = []
    else:
        pages = site_pages or []

    for page in pages:
        base = page.get("url") or website or ""
        for link in page.get("links", []):
            href = _normalize_url(base, link["href"])
            kind = _feed_kind(href, link.get("text", ""))
            if kind:
                add_feed(href, kind)

    if website:
        parsed = urlparse(website)
        root = f"{parsed.scheme}://{parsed.netloc}"
        for suffix in ("/feed", "/feed/", "/rss", "/rss/", "/podcast/feed"):
            add_feed(root + suffix, "rss")

    feeds.sort(key=lambda f: (f["kind"] != "rss", f["url"]))
    return {"feeds": feeds, "church_id": church_id}


def _entry_datetime(entry: Any) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, key, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _entry_speaker(entry: Any) -> str:
    for key in ("itunes_author", "author"):
        val = getattr(entry, key, None) or entry.get(key)
        if val:
            if isinstance(val, dict):
                return str(val.get("name") or val.get("detail") or "")
            return str(val).strip()
    for key in ("dc_creator", "creator"):
        val = entry.get(key)
        if val:
            return str(val).strip()
    return ""


def _entry_media(entry: Any) -> tuple[str | None, str | None, str | None]:
    audio_url = video_url = transcript_url = None
    for link in entry.get("links", []):
        href = link.get("href")
        if not href:
            continue
        rel = (link.get("rel") or link.get("type") or "").lower()
        mime = (link.get("type") or "").lower()
        if "transcript" in rel or "transcript" in mime:
            transcript_url = href
        elif mime.startswith("video/"):
            video_url = href
        elif mime.startswith("audio/") or rel == "enclosure":
            audio_url = href
    if not audio_url:
        for enc in entry.get("enclosures", []):
            href = enc.get("href")
            mime = (enc.get("type") or "").lower()
            if href and mime.startswith("audio/"):
                audio_url = href
                break
    if not transcript_url:
        raw = entry.get("itunes_transcript") or entry.get("transcript_url")
        if isinstance(raw, dict):
            transcript_url = raw.get("url") or raw.get("href")
        elif raw:
            transcript_url = str(raw)
    return audio_url, video_url, transcript_url


def get_sermons(
    feed_url: str,
    limit: int | None = None,
    *,
    feed_text: str | None = None,
) -> dict[str, Any]:
    """Parse a sermon RSS/Atom feed; return items newest first."""
    cap = min(limit or get_settings().deep_max_sermons, get_settings().deep_max_sermons)
    if feed_text is None:
        feed_text = _fetch_feed_text(feed_url)
        if feed_text is None:
            return {"feed_url": feed_url, "items": [], "error": "feed could not be fetched"}
    parsed = feedparser.parse(feed_text)

    items: list[dict[str, Any]] = []
    for entry in parsed.entries:
        audio_url, video_url, transcript_url = _entry_media(entry)
        item: dict[str, Any] = {
            "title": (entry.get("title") or "").strip(),
            "date": _entry_datetime(entry).isoformat(),
            "speaker": _entry_speaker(entry),
            "audio_url": audio_url,
            "video_url": video_url,
            "transcript_url": transcript_url,
            "page_url": entry.get("link") or "",
        }
        items.append(item)

    items.sort(key=lambda x: x["date"], reverse=True)
    return {"feed_url": feed_url, "items": items[:cap]}


def _fetch_feed_text(url: str) -> str | None:
    """R7: fetch feeds ourselves (30 s timeout, blocklist) instead of feedparser.parse(url), which has no timeout."""
    from app.web import _is_blocklisted, _ua

    if _is_blocklisted(url):
        return None
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0, headers={"User-Agent": _ua()}) as c:
            resp = c.get(url)
        if resp.status_code != 200:
            return None
        return resp.text
    except httpx.HTTPError:
        return None


def _audio_suffix(url: str, content_type: str = "") -> str:
    path = urlparse(url).path.lower()
    for ext in AUDIO_EXTS:
        if path.endswith(ext):
            return ext
    ct = content_type.lower()
    if "mp4" in ct or "m4a" in ct or "aac" in ct:
        return ".m4a"
    if "wav" in ct:
        return ".wav"
    if "ogg" in ct:
        return ".ogg"
    return ".mp3"


def _download_audio(url: str, dest: Path, client: httpx.Client | None = None) -> Path:
    """Stream to disk with a size cap. Returns the path actually written (suffix fixed from URL/content-type)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    own = client is None
    c = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0))
    try:
        with c.stream("GET", url) as resp:
            resp.raise_for_status()
            dest = dest.with_suffix(_audio_suffix(url, resp.headers.get("content-type", "")))
            size = 0
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > DOWNLOAD_MAX_BYTES:
                        raise ValueError(f"audio larger than {DOWNLOAD_MAX_BYTES // (1024 * 1024)} MB; skipped")
                    f.write(chunk)
    finally:
        if own:
            c.close()
    return dest


def _audio_duration_seconds(path: Path) -> float:
    ffprobe = _ffmpeg_exe().replace("ffmpeg", "ffprobe")
    if not Path(ffprobe).exists():
        ffprobe = _ffmpeg_exe()
        cmd = [
            _ffmpeg_exe(),
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        for line in (result.stderr or "").splitlines():
            if "Duration:" in line:
                part = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = part.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
        return 0.0
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float((result.stdout or "0").strip())
    except ValueError:
        return 0.0


def prepare_audio_chunks(src: Path, work_dir: Path | None = None) -> list[Path]:
    """Convert large audio to mono 32 kbps and split into <=20-minute chunks."""
    work = work_dir or src.parent / f"{src.stem}_chunks"
    work.mkdir(parents=True, exist_ok=True)
    converted = work / "converted.mp3"
    ffmpeg = _ffmpeg_exe()

    subprocess.run(
        [ffmpeg, "-y", "-i", str(src), "-ac", "1", "-b:a", "32k", str(converted)],
        check=True,
        capture_output=True,
    )

    duration = _audio_duration_seconds(converted)
    needs_split = converted.stat().st_size > AUDIO_MAX_BYTES or duration > CHUNK_MAX_SECONDS
    if not needs_split:
        return [converted]

    pattern = work / "chunk_%03d.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(converted),
            "-f",
            "segment",
            "-segment_time",
            str(CHUNK_MAX_SECONDS),
            "-ac",
            "1",
            "-b:a",
            "32k",
            str(pattern),
        ],
        check=True,
        capture_output=True,
    )
    chunks = sorted(work.glob("chunk_*.mp3"))
    return chunks if chunks else [converted]


def _fetch_transcript(url: str) -> str | None:
    try:
        page = fetch(url, max_chars=100_000)
        text = (page.get("text") or "").strip()
        return text or None
    except Blocked:
        return None


def _transcribe_file(path: Path) -> str:
    """R7: always normalise to mono mp3 in <= 20-minute chunks (transcription models cap per-request duration).
    If ffmpeg can't read the file, fall back to sending it as-is when it is small enough."""
    try:
        chunks = prepare_audio_chunks(path)
    except (subprocess.CalledProcessError, OSError):
        if path.stat().st_size > AUDIO_MAX_BYTES:
            raise
        chunks = [path]
    parts: list[str] = []
    for chunk in chunks:
        parts.append(llm_transcribe(str(chunk)))
    return "\n".join(p.strip() for p in parts if p.strip())


def transcribe_sermon(
    item: dict[str, Any],
    *,
    church_id: str | None = None,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Download and transcribe one sermon; prefer existing transcript/captions."""
    t0 = time.perf_counter()
    title = item.get("title") or "sermon"
    speaker = item.get("speaker") or ""

    if item.get("text"):
        minutes = float(item.get("minutes") or 0)
        wall_ms = int((time.perf_counter() - t0) * 1000)
        log_throughput(
            church_id=church_id,
            sermon_title=title,
            audio_minutes=minutes,
            wall_ms=wall_ms,
            action="transcript_existing",
        )
        return {"text": item["text"], "minutes": minutes, "speaker": speaker, "source": "item"}

    transcript_url = item.get("transcript_url")
    if transcript_url:
        text = _fetch_transcript(transcript_url)
        if text:
            minutes = float(item.get("minutes") or max(len(text.split()) / 150.0, 1.0))
            wall_ms = int((time.perf_counter() - t0) * 1000)
            log_throughput(
                church_id=church_id,
                sermon_title=title,
                audio_minutes=minutes,
                wall_ms=wall_ms,
                action="transcript_url",
            )
            return {"text": text, "minutes": minutes, "speaker": speaker, "source": "transcript_url"}

    audio_url = item.get("audio_url")          # R7: never download video
    if not audio_url:
        return {"text": "", "minutes": 0.0, "speaker": speaker, "source": "missing_audio"}

    safe_name = re.sub(r"[^\w-]+", "_", title)[:80] or "sermon"
    dest = _download_audio(audio_url, _audio_cache_dir() / f"{safe_name}.mp3", client=http_client)

    text = _transcribe_file(dest)
    minutes = _audio_duration_seconds(dest) / 60.0
    if minutes <= 0:
        minutes = max(len(text.split()) / 150.0, 1.0)

    wall_ms = int((time.perf_counter() - t0) * 1000)
    log_throughput(
        church_id=church_id,
        sermon_title=title,
        audio_minutes=minutes,
        wall_ms=wall_ms,
        action="transcribe",
    )
    return {"text": text, "minutes": minutes, "speaker": speaker, "source": "transcribed"}


def transcribe_sermons_parallel(
    items: list[dict[str, Any]],
    *,
    church_id: str | None = None,
    max_workers: int = 3,
    http_client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Transcribe up to max_workers sermons in parallel."""
    cap = min(len(items), get_settings().deep_max_sermons)
    items = items[:cap]
    results: list[dict[str, Any] | None] = [None] * len(items)

    def work(idx: int, item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return idx, transcribe_sermon(item, church_id=church_id, http_client=http_client)

    with ThreadPoolExecutor(max_workers=min(max_workers, 3)) as pool:
        futures = [pool.submit(work, i, item) for i, item in enumerate(items)]
        for fut in as_completed(futures):
            idx, result = fut.result()
            results[idx] = result

    return [r for r in results if r is not None]


def _analyse_one_sermon(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    prompt = load_prompt("sermon_analyse.v2")
    user = {
        "metadata": metadata,
        "transcript": text[:50_000],
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(user)},
    ]
    return complete_json("sermon_analyse", messages, SERMON_ANALYSE_SCHEMA, tier="fast")


def _mode(values: list[str]) -> str:
    if not values:
        return "unknown"
    return Counter(values).most_common(1)[0][0]


def _minutes_to_sermon_length(minutes: float) -> str:
    if minutes < 20:
        return "under_20"
    if minutes <= 35:
        return "20_35"
    return "over_35"


def _politics_frequency(analyses: list[dict[str, Any]]) -> str:
    if not analyses:
        return "rare"
    avg = sum(float(a.get("politics_mentions") or 0) for a in analyses) / len(analyses)
    if avg >= 2:
        return "frequent"
    if avg >= 0.5:
        return "occasional"
    return "rare"


def _women_preach_value(female_count: int, total: int) -> str:
    if female_count <= 0:
        return "never"
    if female_count >= max(1, total // 2):
        return "regularly"
    return "occasionally"


def _allowed(feature_id: str, value: str) -> bool:
    from app.features import all_features

    meta = all_features().get(feature_id) or {}
    return value in (meta.get("values") or [])


def _aggregate_evidence(analyses: list[dict[str, Any]], features: list[str]) -> list[Evidence]:
    """Observed tier-D evidence. Every note has the form "k of n sermons ..." so features.data_points() can
    count observations (settled at n >= 5)."""
    if not analyses:
        return []

    n = len(analyses)
    out: list[Evidence] = []
    checked = now()
    feature_set = set(features)

    def add(feature: str, value: str, note: str) -> None:
        if feature in feature_set and _allowed(feature, value):
            out.append(Evidence(feature=feature, value=value, tier="D", how="observed",
                                source_kind="sermon_transcript", checked_at=checked, note=note))

    # women.preach: count only sermons whose speaker could be identified (R7: unknown never means "male")
    genders = [(a.get("speaker_gender") or "unknown").lower() for a in analyses]
    known = [g for g in genders if g in ("female", "male")]
    female = sum(1 for g in known if g == "female")
    if len(known) >= SPEAKER_KNOWN_MIN:
        add("women.preach", _women_preach_value(female, len(known)),
            f"{female} of {len(known)} sermons with an identifiable speaker were preached by women")

    minutes_list = [float(a.get("minutes") or 0) for a in analyses if a.get("minutes")]
    if minutes_list:
        buckets = [_minutes_to_sermon_length(m) for m in minutes_list]
        top = _mode(buckets)
        median = sorted(minutes_list)[len(minutes_list) // 2]
        add("logistics.sermon_length", top, f"{buckets.count(top)} of {len(buckets)} sermons; median {median:.0f} min")

    for fid, key in (("preaching.style", "style"), ("preaching.audience", "audience"),
                     ("preaching.scripture_density", "scripture_density")):
        vals = [str(a.get(key) or "") for a in analyses if a.get(key)]
        if vals:
            top = _mode(vals)
            add(fid, top, f"{vals.count(top)} of {len(vals)} sermons {top.replace('_', ' ')}")

    mentioned = sum(1 for a in analyses if float(a.get("politics_mentions") or 0) > 0)
    add("preaching.politics_frequency", _politics_frequency(analyses), f"{mentioned} of {n} sermons mentioned politics or current events")

    return out


def analyse_sermons(
    texts: list[dict[str, Any]],
    features: list[str],
    *,
    church_id: str | None = None,
) -> dict[str, Any]:
    """Run sermon_analyse.v1 per sermon and aggregate observed tier-D Evidence."""
    t0 = time.perf_counter()
    cap = min(len(texts), get_settings().deep_max_sermons)
    texts = texts[:cap]

    analyses: list[dict[str, Any]] = []
    for entry in texts:
        text = entry.get("text") or ""
        if not text.strip():
            continue
        metadata = {
            "title": entry.get("title", ""),
            "speaker": entry.get("speaker", ""),
            "minutes": entry.get("minutes"),
            "date": entry.get("date"),
            "url": entry.get("page_url") or entry.get("url") or "",
        }
        analysis = _analyse_one_sermon(text, metadata)
        analysis.setdefault("speaker_gender", entry.get("speaker_gender", "unknown"))
        analyses.append(analysis)

    evidence = _aggregate_evidence(analyses, features)
    wall_ms = int((time.perf_counter() - t0) * 1000)
    audio_minutes = sum(float(a.get("minutes") or 0) for a in analyses)
    log_throughput(
        church_id=church_id,
        sermon_title=f"{len(analyses)} sermons analysed",
        audio_minutes=audio_minutes,
        wall_ms=wall_ms,
        action="analyse",
    )

    return {
        "evidence": evidence,
        "analyses": analyses,
        "sermons_analysed": len(analyses),
    }
