# Podcast + YouTube corpus

`scripts/run_podcasts_overnight.ps1` builds a corpus from 62 Christian podcasts
(`podcasts.yaml`) and 32 YouTube channels (`youtube.yaml`, 13 traditions including
Churches of Christ) and runs the lexicon pipeline on it. Everything runs locally.

YouTube text comes from YouTube's own captions via `youtube-transcript-api` (manual
captions preferred, auto-generated otherwise); when a video has none, the audio is
downloaded with `yt-dlp` and transcribed with Whisper. Long videos (full services) use a
window from the middle, where the sermon usually is.

Since 2026-10-07 items are sampled evenly over each source's past year (`--sample spread`)
rather than only the newest, so one sermon series can't dominate a show.

```powershell
.\scripts\run_podcasts_overnight.ps1 -Plan    # 1 min: checks every feed, lists the episodes
.\scripts\run_podcasts_overnight.ps1          # full run, about 5 h on a 16 GB GPU
```

## What happens

1. **Episodes.** For each tradition, about 24 recent full episodes, spread evenly across
   that tradition's shows (min 3, max 10 per show). Trailers and clips under 8 minutes are skipped.
2. **Text.** If the feed publishes a transcript (`<podcast:transcript>`), that is used. Otherwise
   the first 20 minutes after a 60-second intro skip are transcribed on the GPU with
   faster-whisper `large-v3-turbo`. Only the part of each file that is needed is downloaded. Only
   about 7 of the 64 feeds publish transcripts, so most text comes from Whisper.
3. **Batches.** Episodes are processed round-robin across traditions (one per show per pass), so
   stopping early still leaves a balanced corpus. Whisper uses batched inference (batch 16).
   Each finished episode is cached in `podcasts/cache/`, so a re-run picks up where it stopped.
4. **Corpus.** Transcripts are split into ~250-word chunks at sentence boundaries, capped at
   150k words per tradition, and written to `data/raw/podcasts.jsonl`.
   For now seeker and church text are the same: a stable 25% of chunks are labeled `seeker`
   so the seeker stages and the coverage eval can run. Treat those numbers as rough.
5. **Lexicon.** `python -m lexicon -c config.podcasts.yaml run` (work dir `work_podcasts/`).
   Whisper runs first and the Ollama models after, so they don't compete for VRAM.

## Knobs (`python scripts/podcasts_to_corpus.py --help`)

| flag | default | effect |
|---|---|---|
| `--episodes-per-tradition` | 30 | main size control (podcasts and channels together) |
| `--sources` | podcasts,youtube | `youtube` or `podcasts` alone |
| `--sample` | spread | `latest` = newest items only |
| `--max-minutes` | 15 | text used per item |
| `--label family` or `denomination` | tradition | compare 7 broad families (catholic, orthodox, mainline, evangelical, black_protestant, anabaptist, progressive) instead of 12 traditions |
| `--seeker-fraction` | 0.25 | share of chunks labeled `seeker` |
| `--max-words-per-tradition` | 150000 | balance cap |
| `--no-whisper` | | published transcripts only (fast, but only ~7 shows) |
| `--write-only` | | rebuild the jsonl from the cache (after changing labels or chunking) |

Shows are listed in `podcasts/podcasts.yaml`. Add or remove shows there, or set `enabled: false`.

## If Whisper says it can't use the GPU

The log line `whisper ready: model=large-v3-turbo device=cuda` means the GPU is in use. If it
falls back to `device=cpu`, the run still works but is far slower. Usually the CUDA libraries
are missing: `.\.venv\Scripts\python.exe -m pip install -r podcasts\requirements-podcasts.txt`.
An NVIDIA driver from 2023 or later is needed (CUDA 12).

## Data notes for the build doc

- Sources are public broadcasts by denominations, ministries, churches and public figures, not
  congregants' posts. Transcripts are copyrighted: `podcasts/cache/` is gitignored; only aggregate
  statistics and model-drafted definitions leave the machine.
- Whisper output has spelling errors on rare names and jargon; published transcripts have speaker
  labels stripped.
- Bible-reading shows (Bible in a Year, Bible Recap) include read scripture, which inflates biblical
  vocabulary relative to church self-description.
- Feeds were checked on 2026-10-07; 64 of 65 were live (Allen Temple AME returned 404 and is disabled).
