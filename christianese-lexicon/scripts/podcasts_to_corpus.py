#!/usr/bin/env python
"""Build a lexicon corpus from Christian podcasts and YouTube channels.

Sources: podcasts/podcasts.yaml (RSS feeds) and podcasts/youtube.yaml (channels).
For each source:
  1. list its recent episodes/videos and pick --episodes-per-tradition spread across that
     tradition's sources, sampled evenly over the past year (--sample spread) so one
     sermon series can't dominate a show;
  2. get text:
       podcast: the feed's published transcript (<podcast:transcript>) if any, else Whisper;
       YouTube: youtube-transcript-api (YouTube's own captions) first, else Whisper on the
                audio (downloaded with yt-dlp);
     at most --max-minutes per item (long YouTube services: a window from the middle,
     where the sermon usually is);
  3. split into ~--chunk-words chunks and write data/raw/podcasts.jsonl.

Seeker vs church: for now both are the same text; a stable --seeker-fraction of chunks is
labeled "seeker" so the seeker stages and the coverage eval have something to run on.

Resumable: every transcript is cached in podcasts/cache/. Items are processed round-robin
across sources, so stopping early still leaves a balanced corpus.

  python scripts/podcasts_to_corpus.py                 # fetch + transcribe + write corpus
  python scripts/podcasts_to_corpus.py --plan          # list what would be processed
  python scripts/podcasts_to_corpus.py --sources youtube
  python scripts/podcasts_to_corpus.py --write-only    # rebuild the jsonl from the cache
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
NS = {
    "podcast": "https://podcastindex.org/namespace/1.0",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
}
# A plain, honest client name. Some podcast hosts (Ancient Faith) refuse browser-like "Mozilla/..." agents.
UA = {"User-Agent": "christianese-lexicon/1.0 (podcast research fetcher)"}
log = logging.getLogger("podcasts")


# ----------------------------------------------------------------- helpers
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def get(url: str, **kw) -> requests.Response:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 60), **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 4 * attempt)
    raise last


def parse_duration(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip()
    try:
        if ":" in s:
            sec = 0.0
            for part in s.split(":"):
                sec = sec * 60 + float(part)
            return sec
        return float(s)
    except ValueError:
        return None


# ----------------------------------------------------------------- feeds
def read_feed(show: dict) -> list[dict]:
    root = ET.fromstring(get(show["rss"]).content)
    eps = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        guid = (it.findtext("guid") or "").strip()
        enc = it.find("enclosure")
        audio = enc.get("url") if enc is not None else None
        length = int(enc.get("length") or 0) if enc is not None and (enc.get("length") or "").isdigit() else 0
        dur = parse_duration(it.findtext("itunes:duration", namespaces=NS))
        ep_type = (it.findtext("itunes:episodeType", namespaces=NS) or "full").strip().lower()
        try:
            date = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except Exception:  # noqa: BLE001
            date = None
        transcripts = [{"url": t.get("url"), "type": (t.get("type") or "").lower()}
                       for t in it.findall("podcast:transcript", NS) if t.get("url")]
        eps.append({"title": title, "guid": guid or audio or title, "audio": audio, "length": length,
                    "duration": dur, "type": ep_type, "date": date, "transcripts": transcripts})
    return eps


def eligible(e: dict, min_minutes: float) -> bool:
    if e.get("type") in ("trailer", "bonus"):
        return False
    if e["duration"] is not None and not (min_minutes * 60 <= e["duration"] <= 4 * 3600):
        return False
    if not e.get("audio") and not e.get("transcripts") and not e.get("yt"):
        return False
    if re.search(r"(\btrailer\b|\bteaser\b|\brebroadcast\b|\bencore\b|\bbest of\b|#shorts|"
                 r"\bprelude\b|\bpostlude\b|\bconcert\b|\brecital\b)", e["title"], re.I):
        return False
    return True


def pick_episodes(eps: list[dict], n: int, min_minutes: float, mode: str = "spread") -> list[dict]:
    """eps are newest first. 'latest' = the n newest; 'spread' = n evenly spaced over the past year."""
    ok = [e for e in eps if eligible(e, min_minutes)]
    if mode == "latest" or len(ok) <= n:
        return ok[:n]
    pool = ok
    dates = [e["date"] for e in ok if e.get("date")]
    if dates:
        import datetime as dt
        newest = dt.date.fromisoformat(max(dates))
        cutoff = (newest - dt.timedelta(days=365)).isoformat()
        year = [e for e in ok if (e.get("date") or "9999") >= cutoff]
        pool = year if len(year) >= n else ok[:max(n, len(year))]
    if n == 1:
        return pool[:1]
    idx = sorted({round(i * (len(pool) - 1) / (n - 1)) for i in range(n)})
    return [pool[i] for i in idx]


# ----------------------------------------------------------------- YouTube
def list_channel(ch: dict, limit: int = 150) -> list[dict]:
    """Recent uploads of a channel tab, newest first (no download)."""
    import yt_dlp
    opts = {"extract_flat": "in_playlist", "playlistend": limit, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(ch["url"], download=False)
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id") or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        out.append({"title": e.get("title") or "", "guid": "yt:" + e["id"], "yt": e["id"], "audio": None,
                    "length": 0, "duration": e.get("duration"), "type": "full", "date": None, "transcripts": []})
    return out


_YT_BLOCKED = False
_TAGS = re.compile(r"\[(?:music|applause|laughter|inaudible|silence|__)\]|>>", re.I)


def yt_window(duration: float | None, skip_s: float, max_s: float) -> float:
    """Start of the window to keep. Long videos are usually full services: take the middle."""
    if not duration:
        return skip_s
    if duration > 2 * max_s + skip_s:
        return max(skip_s, (duration - max_s) / 2)
    return skip_s if duration > max_s + skip_s else 0.0


def fetch_youtube_transcript(ep: dict, skip_s: float, max_s: float) -> str | None:
    """YouTube's captions via youtube-transcript-api (manual preferred, auto-generated otherwise)."""
    global _YT_BLOCKED
    if _YT_BLOCKED:
        return None
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        ft = YouTubeTranscriptApi().fetch(ep["yt"], languages=["en", "en-US", "en-GB", "en-CA"])
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if name in ("RequestBlocked", "IpBlocked", "TooManyRequests"):
            _YT_BLOCKED = True
            log.warning("YouTube is blocking caption requests from this network (%s). Remaining videos go "
                        "straight to Whisper.", name)
        else:
            log.info("no captions (%s): %s", name, ep["title"][:60])
        return None
    finally:
        time.sleep(1.0)  # be polite; YouTube blocks bursts
    snips = ft.snippets
    if not snips:
        return None
    dur = ep["duration"] or (snips[-1].start + snips[-1].duration)
    start = yt_window(dur, skip_s, max_s)
    text = " ".join(sn.text for sn in snips if start <= sn.start < start + max_s)
    text = re.sub(r"\s+", " ", _TAGS.sub(" ", text)).strip()
    return text if len(text.split()) > 200 else None


def download_youtube_audio(ep: dict, dest_dir: Path) -> Path:
    import yt_dlp
    opts = {"format": "bestaudio[ext=m4a]/bestaudio", "quiet": True, "no_warnings": True, "noprogress": True,
            "outtmpl": str(dest_dir / "yt.%(ext)s"), "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info("https://www.youtube.com/watch?v=" + ep["yt"], download=True)
        return Path(y.prepare_filename(info))


# ----------------------------------------------------------------- published transcripts
_TS = re.compile(r"^\s*(\d+\s*)?$|-->|^WEBVTT|^NOTE\b|^STYLE\b|^Kind:|^Language:")


def transcript_to_text(body: str, ttype: str) -> str:
    if "json" in ttype:
        try:
            data = json.loads(body)
            segs = data.get("segments") if isinstance(data, dict) else data
            return " ".join((s.get("body") or s.get("text") or "").strip() for s in segs or [])
        except Exception:  # noqa: BLE001
            return ""
    if "html" in ttype:
        body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        return html.unescape(re.sub(r"\s+", " ", body)).strip()
    # vtt / srt / plain
    lines = []
    for ln in body.splitlines():
        if _TS.search(ln):
            continue
        ln = re.sub(r"<[^>]+>", "", ln).strip()
        if ln and (not lines or ln != lines[-1]):
            lines.append(ln)
    return " ".join(lines)


def fetch_published(ep: dict) -> str | None:
    order = ["text/plain", "application/json", "text/vtt", "application/x-subrip", "application/srt", "text/html"]
    for want in order + [""]:
        for t in ep["transcripts"]:
            if (want and t["type"] != want) or (not want and t["type"] in order):
                continue
            try:
                txt = transcript_to_text(get(t["url"]).text, t["type"] or t["url"].rsplit(".", 1)[-1])
            except Exception as e:  # noqa: BLE001
                log.debug("transcript fetch failed %s: %s", t["url"], e)
                continue
            if len(txt.split()) > 200:
                return txt
    return None


# ----------------------------------------------------------------- whisper
def _windows_cuda_dlls():
    """pip's nvidia-* wheels put cuBLAS/cuDNN DLLs in site-packages/nvidia/*/bin; make them findable."""
    if os.name != "nt":
        return
    import site
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        base = Path(sp) / "nvidia"
        if base.is_dir():
            for b in base.glob("*/bin"):
                os.add_dll_directory(str(b))
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    def __init__(self, model: str, device: str, batch_size: int):
        _windows_cuda_dlls()
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        self.batch_size = batch_size
        self.model = model
        try:
            m = WhisperModel(model, device=device, compute_type="float16" if device != "cpu" else "int8")
            self.device = device
        except Exception as e:  # noqa: BLE001
            if device == "cpu":
                raise
            log.error("Could not start Whisper on the GPU (%s). Falling back to CPU with 'small.en' "
                      "(much slower). See podcasts/README-podcasts.md for the CUDA fix.", e)
            m = WhisperModel("small.en", device="cpu", compute_type="int8")
            self.device, self.model = "cpu", "small.en"
        self.pipe = BatchedInferencePipeline(model=m)
        log.info("whisper ready: model=%s device=%s", self.model, self.device)

    def transcribe(self, path: str, skip_s: float, max_s: float) -> str:
        sr = 16000
        audio = load_audio(path, skip_s, max_s, sr)
        if len(audio) < sr * 30:
            return ""
        segs, _ = self.pipe.transcribe(audio, language="en", batch_size=self.batch_size,
                                       vad_filter=True, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs)


def load_audio(path: str, start_s: float, dur_s: float, sr: int = 16000):
    """Decode [start_s, start_s + dur_s) as 16 kHz mono float32.

    Our own decoder instead of faster_whisper.decode_audio, which passes an option that newer
    PyAV releases (17+) removed. Stops decoding once enough audio is read, and tolerates the
    cut-off end of a partially downloaded file.
    """
    import av
    import numpy as np
    need = int((start_s + dur_s) * sr)
    parts, got = [], 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
    with av.open(path) as container:
        stream = container.streams.audio[0]
        try:
            for frame in container.decode(stream):
                out = resampler.resample(frame)
                for f in (out if isinstance(out, list) else [out]):
                    if f is None:
                        continue
                    a = f.to_ndarray().reshape(-1)
                    parts.append(a)
                    got += len(a)
                if got >= need:
                    break
        except (av.error.FFmpegError, ValueError) as e:  # truncated tail of a partial download
            log.debug("decode stopped early (%s): %s", path, e)
    if not parts:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(parts).astype(np.float32) / 32768.0
    return audio[int(start_s * sr): need]


def download_head(ep: dict, max_s: float, skip_s: float, dest: Path) -> bool:
    """Download only as much of the file as the first skip_s + max_s seconds needs."""
    # MP4/M4A files usually keep their index at the END, so a partial download can't be opened.
    # Fetch those whole (they're audio-only podcasts in practice; skip anything huge, e.g. video).
    full = Path(ep["audio"].split("?")[0]).suffix.lower() in (".mp4", ".m4a", ".m4v", ".mov", ".aac")
    if full and ep["length"] > 300_000_000:
        raise ValueError(f"{ep['length'] / 1e6:.0f} MB MP4 is probably video; skipped (use an audio feed)")
    need = None
    if full:
        need = 300_000_000
    elif ep["length"] and ep["duration"]:
        need = int(ep["length"] * min(1.0, (skip_s + max_s) / ep["duration"]) * 1.15) + 3_000_000  # + ID3/cover art
    cap = need or int((skip_s + max_s) * 320_000 / 8)  # unknown bitrate: assume up to 320 kbps
    got = 0
    with requests.get(ep["audio"], headers=UA, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if got >= cap:
                    break
    return got > 100_000


# ----------------------------------------------------------------- chunking
_LABELS = re.compile(r"\[?\b(?:SPEAKER|Speaker)[ _]?\d+\]?:?|\(?\b\d{1,2}:\d{2}(?::\d{2})?\)?")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])")


def chunk_text(text: str, target: int) -> list[str]:
    text = re.sub(r"\s+", " ", _LABELS.sub(" ", text)).strip()
    sents = _SENT.split(text)
    chunks, cur, n = [], [], 0
    for s in sents:
        w = len(s.split())
        if w > target * 2:  # unpunctuated run (some transcripts): hard-split it
            words = s.split()
            for i in range(0, len(words), target):
                if cur:
                    chunks.append(" ".join(cur)); cur, n = [], 0
                chunks.append(" ".join(words[i:i + target]))
            continue
        cur.append(s); n += w
        if n >= target:
            chunks.append(" ".join(cur)); cur, n = [], 0
    if cur and n >= target // 3:
        chunks.append(" ".join(cur))
    return chunks


# ----------------------------------------------------------------- main
def read_items(src: dict) -> list[dict]:
    return list_channel(src) if src["type"] == "youtube" else read_feed(src)


def plan(sources: list[dict], args) -> list[tuple[dict, list[dict]]]:
    by_label = defaultdict(list)
    for s in sources:
        by_label[label_of(s, args.label)].append(s)
    out = []
    for lab, group in sorted(by_label.items()):
        n = max(args.min_per_show, min(args.max_per_show, math.ceil(args.episodes_per_tradition / len(group))))
        for s in group:
            try:
                eps = pick_episodes(read_items(s), n, args.min_minutes, args.sample)
            except Exception as e:  # noqa: BLE001
                log.warning("listing failed, skipping %s: %s", s["name"], e)
                continue
            out.append((s, eps))
            pub = sum(1 for e in eps if e["transcripts"] or e.get("yt"))
            log.info("%-24s %-7s %-52s %2d items (%d with transcripts/captions to try)",
                     lab, s["type"], s["name"][:52], len(eps), pub)
    return out


def label_of(src: dict, label: str) -> str:
    return src.get(label) or src["tradition"]


def cache_path(cache: Path, src: dict, ep: dict) -> Path:
    return cache / slug(src["name"]) / (sha(ep["guid"])[:16] + ".json")


def interleave(planned):
    """Round-robin: one episode per show per pass, traditions mixed, so early stops stay balanced."""
    queues = [[(s, e) for e in eps] for s, eps in planned]
    while any(queues):
        for q in queues:
            if q:
                yield q.pop(0)


def whisper_test(shows, args):
    """One episode, 2 minutes, no error handling: whatever breaks is shown in full."""
    show = next(s for s in shows if "bible for normal people" in s["name"].lower())
    ep = pick_episodes(read_feed(show), 1, args.min_minutes)[0]
    log.info("test episode: %s / %s", show["name"], ep["title"])
    t = Transcriber(args.whisper_model, args.device, args.batch_size)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / ("audio" + Path(ep["audio"].split("?")[0]).suffix[:5])
        ok = download_head(ep, 120, args.skip_seconds, tmp)
        log.info("downloaded %d bytes (ok=%s) to %s", tmp.stat().st_size, ok, tmp)
        audio = load_audio(str(tmp), 0, 10_000)
        log.info("decoded %.1f s of audio (PyAV %s)", len(audio) / 16000, __import__("av").__version__)
        t0 = time.time()
        text = t.transcribe(str(tmp), args.skip_seconds, 120)
        log.info("transcribed in %.1f s on %s: %d words", time.time() - t0, t.device, len(text.split()))
        print("\n" + text[:600] + "\n")
    print("WHISPER TEST PASSED" if text and t.device != "cpu" else "WHISPER TEST: no text or not on GPU (see above)")
    return 0 if text else 1


def load_sources(args) -> list[dict]:
    out = []
    if "podcasts" in args.sources:
        for s in yaml.safe_load(open(args.shows, encoding="utf-8"))["shows"]:
            if s.get("enabled", True):
                out.append({**s, "type": "podcast"})
    if "youtube" in args.sources and Path(args.youtube).exists():
        for c in yaml.safe_load(open(args.youtube, encoding="utf-8"))["channels"]:
            if c.get("enabled", True):
                out.append({**c, "type": "youtube"})
    return out


def get_text(src, ep, args, state) -> tuple[str | None, str | None]:
    """Returns (text, how). Order: published transcript / YouTube captions, then Whisper."""
    max_s = args.max_minutes * 60
    if ep.get("yt"):
        text = fetch_youtube_transcript(ep, args.skip_seconds, max_s)
        if text:
            return text, "captions"
    elif ep["transcripts"]:
        text = fetch_published(ep)
        if text:
            return " ".join(text.split()[: int(args.max_minutes * 160)]), "published"
    if args.no_whisper or not (ep.get("audio") or ep.get("yt")):
        return None, None
    if state.get("whisper") is None:
        state["whisper"] = Transcriber(args.whisper_model, args.device, args.batch_size)
    with tempfile.TemporaryDirectory() as td:
        if ep.get("yt"):
            path = download_youtube_audio(ep, Path(td))
            start = yt_window(ep["duration"], args.skip_seconds, max_s)
        else:
            path = Path(td) / ("audio" + Path(ep["audio"].split("?")[0]).suffix[:5])
            if not download_head(ep, max_s, args.skip_seconds, path):
                state["outcome"]["download too small"] += 1
                return None, None
            start = args.skip_seconds
        text = state["whisper"].transcribe(str(path), start, max_s)
    if not text:
        state["outcome"]["whisper returned no text"] += 1
        log.warning("no text from whisper: %s / %s", src["name"][:40], ep["title"][:60])
    return text or None, "whisper"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shows", default=str(ROOT / "podcasts" / "podcasts.yaml"))
    ap.add_argument("--youtube", default=str(ROOT / "podcasts" / "youtube.yaml"))
    ap.add_argument("--sources", default="podcasts,youtube", help="podcasts, youtube, or both (default)")
    ap.add_argument("--out", default=str(ROOT / "data" / "raw" / "podcasts.jsonl"))
    ap.add_argument("--cache", default=str(ROOT / "podcasts" / "cache"))
    ap.add_argument("--label", choices=["tradition", "family", "denomination"], default="tradition",
                    help="which yaml field becomes the corpus 'tradition' label (denomination falls back to tradition)")
    ap.add_argument("--episodes-per-tradition", type=int, default=30,
                    help="spread across that tradition's podcasts and channels (default 30)")
    ap.add_argument("--sample", choices=["spread", "latest"], default="spread",
                    help="spread = evenly over the past year (default); latest = most recent only")
    ap.add_argument("--min-per-show", type=int, default=3)
    ap.add_argument("--max-per-show", type=int, default=10)
    ap.add_argument("--min-minutes", type=float, default=8, help="skip shorter items (trailers, clips)")
    ap.add_argument("--max-minutes", type=float, default=15, help="use at most this much of each item")
    ap.add_argument("--skip-seconds", type=float, default=60, help="skip the intro / pre-roll ads")
    ap.add_argument("--chunk-words", type=int, default=250)
    ap.add_argument("--seeker-fraction", type=float, default=0.25)
    ap.add_argument("--max-words-per-tradition", type=int, default=200_000,
                    help="cap so one talkative tradition can't dominate (0 = no cap)")
    ap.add_argument("--whisper-model", default="large-v3-turbo")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--no-whisper", action="store_true", help="published transcripts and captions only")
    ap.add_argument("--plan", action="store_true", help="list what would be processed and exit")
    ap.add_argument("--write-only", action="store_true", help="skip fetching; rebuild the jsonl from the cache")
    ap.add_argument("--whisper-test", action="store_true",
                    help="transcribe 2 minutes of one episode, show any error in full, and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    for h in (logging.StreamHandler(), logging.FileHandler(ROOT / "podcasts" / "podcasts.log", encoding="utf-8")):
        h.setFormatter(fmt); root.addHandler(h)
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log.info("python %s on %s; args: %s", sys.version.split()[0], sys.platform, vars(args))

    sources = load_sources(args)
    cache = Path(args.cache); cache.mkdir(parents=True, exist_ok=True)
    manifest = cache.parent / "selected.json"   # which cached items belong to the current corpus

    if args.whisper_test:
        return whisper_test([s for s in sources if s["type"] == "podcast"], args)

    if not args.write_only:
        planned = plan(sources, args)
        total = sum(len(e) for _, e in planned)
        todo = [(s, e) for s, es in planned for e in es if not cache_path(cache, s, e).exists()]
        log.info("plan: %d sources, %d items (%d already cached, %d to fetch)",
                 len(planned), total, total - len(todo), len(todo))
        if args.plan:
            return
        json.dump(sorted(str(cache_path(cache, s, e).relative_to(cache)) for s, es in planned for e in es),
                  open(manifest, "w"), indent=0)
        state = {"whisper": None, "outcome": defaultdict(int)}
        done = fails_in_a_row = 0
        t0 = time.time()
        for src, ep in interleave(planned):
            done += 1
            fp = cache_path(cache, src, ep)
            if fp.exists():
                continue
            fp.parent.mkdir(parents=True, exist_ok=True)
            try:
                text, how = get_text(src, ep, args, state)
            except Exception:  # noqa: BLE001
                text, how = None, None
                state["outcome"]["error"] += 1
                log.exception("failed: %s / %s", src["name"][:40], ep["title"][:60])
            if not text:
                fails_in_a_row += 1
                if fails_in_a_row >= 8:
                    log.error("8 items in a row produced no text. Stopping so the lexicon is not built on a "
                              "broken corpus. The errors are above and in podcasts/podcasts.log.")
                    return 2
                continue
            fails_in_a_row = 0
            state["outcome"][how] += 1
            json.dump({"show": src["name"], "source_type": src["type"], "tradition": src["tradition"],
                       "denomination": src.get("denomination"), "family": src["family"], "kind": src["kind"],
                       "title": ep["title"], "date": ep["date"], "how": how, "text": text},
                      open(fp, "w", encoding="utf-8"))
            log.info("[%d/%d] %-9s %-40s %-50s %5d words  (%.0f min elapsed)", done, total, how,
                     src["name"][:40], ep["title"][:50], len(text.split()), (time.time() - t0) / 60)
        log.info("fetch finished: %s", dict(state["outcome"]))

    # ---- write the corpus: the items selected by the last plan (all cached ones if no plan yet)
    files = sorted(cache.glob("*/*.json"))
    if manifest.exists():
        keep = set(json.load(open(manifest)))
        files = [p for p in files if str(p.relative_to(cache)) in keep]
    eps = [json.load(open(p, encoding="utf-8")) for p in files]
    by_name = {s["name"]: s for s in sources}
    by_lab = defaultdict(list)
    for e in eps:
        if e["show"] in by_name:  # sources you disabled since are left out
            by_lab[label_of(by_name[e["show"]], args.label)].append(e)
    rows, report = [], {}
    for lab, group in sorted(by_lab.items()):
        per_src = defaultdict(list)   # round-robin by source so the word cap trims evenly
        for e in group:
            for c in chunk_text(e["text"], args.chunk_words):
                per_src[e["show"]].append((e, c))
        words, n0 = 0, len(rows)
        queues = list(per_src.values())
        while any(queues):
            for q in queues:
                if not q:
                    continue
                if args.max_words_per_tradition and words >= args.max_words_per_tradition:
                    q.clear(); continue
                e, c = q.pop(0)
                h = sha(c)
                side = "seeker" if int(h[:8], 16) / 0xFFFFFFFF < args.seeker_fraction else "church"
                stype = e.get("source_type", "podcast")
                rows.append({"id": h[:16], "text": c, "side": side, "tradition": lab,
                             "source": f"{stype}:{e['kind']}:{slug(e['show'])}", "date": e["date"]})
                words += len(c.split())
        report[lab] = {"items": len(group), "sources": len(per_src), "words": words, "chunks": len(rows) - n0,
                       "youtube_items": sum(1 for e in group if e.get("source_type") == "youtube")}
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(rows)} chunks to {out}")
    print(f"{'label':28s} {'srcs':>5s} {'items':>5s} {'(yt)':>5s} {'chunks':>7s} {'words':>8s}")
    for t, r in report.items():
        flag = "   <- under 20 chunks: stage 04/07 will skip it" if r["chunks"] < 20 else ""
        print(f"{t:28s} {r['sources']:5d} {r['items']:5d} {r['youtube_items']:5d} {r['chunks']:7d} {r['words']:8d}{flag}")
    json.dump(report, open(cache.parent / "corpus_report.json", "w"), indent=2)


if __name__ == "__main__":
    sys.exit(main())
