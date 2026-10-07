# Church Search — BUILD_PLAN (Cursor handoff)

Clock: start ~12:45 pm CT. **Preliminary submission 10:00 pm CT** (code + 250-word description). Finalist submission 9:00 am MT Thu.
Each person drives their own Cursor session on **their own files only** (avoids merge conflicts). Every Cursor prompt starts with:

> Read AGENTS.md, docs/CONVENTIONS.md and docs/INTERFACES.md. Do task <ID> from docs/BUILD_PLAN.md. Touch only the files listed. Finish with the listed tests passing, or reply "blocked: <reason>".

## Critical path
```
T0 scaffold (Daniel, by 1:30) ─┬─ T1 Places+denomination (Daniel) ─┐
                               ├─ T2 DenomKB+matcher+MCP ✅ done    ├─ T6 light search wired + UI (Daniel)  ── M0 4:30
                               ├─ T3 Stage 0 interviewer (Katie)   ┘
                               ├─ T4 Stage 2 website+summary (Carter) ─┐
                               └─ T5 sermons pipeline (Wade)           ├─ T7 deep-search agent + report (Daniel+Carter) ── M1 8:00
                                                                       ┘
T8 eval + submission (all, 8:00–9:45) ── M2 10:00 pm CT
```
*T2 can be built by Claude in the planning chat immediately (no external dependencies) — Daniel's call.

Until T0 lands (~1:30), T3 starts immediately against the fakes described in T0, and others work in their own files against INTERFACES.md with fakes, and do their human tasks (H1–H4).

## Before Cursor (Daniel, ~15 min, in this order)
1. Push everything: `git add -A ; git commit -m "docs + T2" ; git push`.
2. In the repo folder: `python -m venv .venv` → `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` → `.\.venv\Scripts\python.exe -m pytest -q` must print `23 passed`.
3. `copy .env.example .env`, paste the Places and OpenAI keys, then list the model ids your key can use:
   `.\.venv\Scripts\python.exe -c "from openai import OpenAI; import os; from dotenv import load_dotenv; load_dotenv(); print(sorted(m.id for m in OpenAI().models.list()))"`
   Set `OPENAI_MODEL_FAST` (a small/mini chat model), `OPENAI_MODEL_STRONG` (the flagship chat model), `OPENAI_TRANSCRIBE_MODEL` (a model with "transcribe" or "whisper" in its id).
4. Each teammate: `git pull`, create their own venv the same way, open the repo **root folder** in Cursor (so it sees `AGENTS.md` and `.cursor/rules/`), and start with the prompt below for their task.
5. All four: accept the hackathon rules on the hackathon site.

## Human tasks (no Cursor)
- **H1 Katie (1 h, before 3:00):** review `contracts/features.yaml` questions (women ladder, LGBTQ wording, baptism, gifts). Then review sensitive-field rows for the top 30 denominations in `denom-kb/work/out/proposals.csv` (`review_decision` = accept/reject) and run `python -m denomkb apply-review work/out/proposals.csv` in `denom-kb`.
- **H2 Daniel:** `.env` with keys on the demo machine; Legion: after verify finishes, `python -m denomkb run --redo-empty` (consider `chat_model: qwen2.5:14b`), then commit `work/out`.
- **H3 Wade:** build the eval set (EVAL_PLAN §2): 30 churches with known denominations from 3 different regions incl. Hesston/Wichita; 5 with sermon podcasts.
- **H4 Carter:** start `docs/AGENT_BUILD_DOC.md` (skeleton provided) and the 90-second script; all four members **accept the hackathon rules**.

## Tasks

### T0 — Scaffold + harness (Daniel, 45 min) — blocks everything
Already delivered with T2 (keep, don't regenerate): `requirements.txt`, `app/__init__.py`, `app/config.py`, `app/models.py`, `app/features.py` (incl. `settled_open`), `tests/conftest.py`, `tests/fixtures/kb_small.json`.
Files: `app/db.py`, `app/llm.py`, `app/log.py`, `app/web.py`, `app/main.py` (only `/healthz` + `/`), `app/templates/base.html`, `tests/fakes.py`, `tests/test_scaffold.py`, `run.bat`, `run.sh`.
- `tests/fakes.py`: `FakeLLM` (canned JSON by task name), `FakeWeb` (fixture pages by URL). For the KB use the real `DenomKB(tests/fixtures/kb_small.json)` (see `tests/conftest.py` fixture `kb`). `db.py`, `log.py` fully implemented here.
- `llm.py`: OpenAI + Ollama backends, `complete_json` with schema + one repair retry, `chat_tools`, `transcribe`, `web_search`, call log with tokens/cost. A `FakeLLM` in `tests/fakes.py` returns canned JSON by task name.
- `web.py`: robots.txt, per-domain delay, SQLite page cache, URL blocklist (ARCHITECTURE §10), returns text via trafilatura.
- AC / tests: `pytest -q` green; `test_scaffold.py` checks features.yaml loads with 71 features, every `denom_field` resolves in the KB JSON if present, models validate, blocklisted URLs are refused, `uvicorn app.main:app` serves `/healthz`.

### T1 — Places + denomination resolution (Daniel, 1.5 h)
Files: `app/stage1/places.py`, `app/stage1/osm.py`, `app/stage1/denomination.py`, `tests/test_places.py`, `tests/test_denomination.py`, `tests/fixtures/places_*.json`.
- Exactly the request in ARCHITECTURE §5 (locationBias circle, field mask, paging ≤ 60, `geocode`). Record one real response as a fixture, then test offline.
- `resolve()` steps 1–5 with confidences and `method`; locator step uses `contracts/source_registry.md` domains via `llm.web_search`. Uses `get_kb()` (tests: `FakeDenomKB`).
- `light_search(profile)`: geocode origin → Places (OSM fallback on error) → filter → `resolve` top 20 in parallel → `match.score` → sort; returns list of (Church, MatchResult).
- AC: `light_search` with fakes returns sorted results and excludes a dealbreaker-violating church; fixture of 20 places → ≥ 1 churches resolved by name; "First Mennonite Church" → Mennonite family with method=name; an unaffiliated site saying "non-denominational" → label Non-denominational ≥ 0.8; no Google fields besides place_id written to db.

### T2 — DenomKB + matcher + MCP — ✅ DONE (Claude, 2026-10-07 1:30 pm; 23 tests)
Files: `app/denom/kb.py`, `app/denom/mcp_server.py`, `app/match.py`, `tests/test_kb.py`, `tests/test_match.py`, `tests/fixtures/kb_small.json` (if T0 hasn't made it: copy 5 real groups out of denominations_kb.json).
- `DenomKB` reads `denominations_kb.json` (shape: `groups[].fields[field_id] = {value, status, evidence[]}`); `find` uses rapidfuzz over `census_name`, `name`, and the split values of `fields['identity.aliases'].value` and `fields['identity.abbreviations'].value` (see INTERFACES KB shape); `prior` maps feature → `denom_field` and maps free-text values to allowed values with a small rule table + fast model fallback (cached); priors for `sensitive` features per the rule in INTERFACES `DenomKB.prior`.
- `match.score` exactly per ARCHITECTURE §6.
- AC: `find("SBC")` → Southern Baptist Convention top-1; `find("ELCA")` → Evangelical Lutheran Church in America; a dealbreaker violated by a tier-A evidence → `excluded`; violated only by a prior → not excluded, score lowered; `why` has ≤ 4 lines naming tiers; MCP server lists 5 tools.

### T3 — Stage 0 interviewer (Katie, 2 h)
Files: `app/stage0/interviewer.py`, `app/templates/chat.html`, `tests/test_interviewer.py`.
- State machine per ARCHITECTURE §4 using prompts `interviewer.v1`, `readback.v1`, `crisis_check.v1`. Persist profile in `sessions`.
- AC (FakeLLM): core questions asked in order; "skip the rest" jumps to read-back; ladder answer rung (f) elders + "both" sets women.deacon/teach_mixed_adults/pastor_other/elder want=[yes], women.preach want=[regularly, occasionally], women.senior_pastor want=[no]; rung (b) + "maximum" sets roles above deacon want=[no] and deacon dont_care; "prefer not to say" on the marriage question → lgbtq.marriage dont_care and the inclusion question is still asked once; in for_whom=other mode no question asks about the person's orientation; crisis text → escalation returned and no further questions.

### T4 — Stage 2 website + summary (Carter, 2 h)
Files: `app/stage2/website.py`, `app/stage2/summary.py`, `app/templates/cards.html`, `tests/test_stage2.py`, `tests/fixtures/site_*`.
- `site_pages` link-scoring per ARCHITECTURE §7; `medium_search` with `page_extract.v1`; verbatim quote check (rapidfuzz ≥ 90) — drop non-verbatim; evidence tier A.
- AC: on 2 saved church sites (fixtures), finds beliefs + staff pages; every stored quote is verbatim; marriage-definition sentence yields lgbtq.marriage=traditional and lgbtq.inclusion stays open; card has `deep_dive_candidate`.

### T5 — Sermons pipeline (Wade, 2.5 h)
Files: `app/stage3/sermons.py`, `tests/test_sermons.py`.
- `find_sermon_feeds` (links on site: rss/podcast/apple/spotify/youtube/sermonaudio/subsplash; `/feed`), `get_sermons` (feedparser), `transcribe_sermon` (download audio to `data/cache/audio`; > 24 MB → ffmpeg mono 32 kbps, ≤ 20-min chunks; `llm.transcribe`; prefer existing transcript/captions), `analyse_sermons` (`sermon_analyse.v1` per sermon → aggregate observed Evidence: women.preach share, sermon_length median, preaching.style, preaching.audience, politics frequency, scripture density).
- Respect `DEEP_MAX_SERMONS` (25, D22); transcribe up to 3 sermons in parallel. Log throughput: audio minutes, wall minutes, cost per sermon.
- AC: feed fixture → items newest first; aggregate of 6 fake analyses with 2 female speakers → women.preach=occasionally (tier D, note "2 of 6 sermons"); throughput row written.

### T6 — Light search wired + UI (Daniel, 1 h) → **M0 4:30 pm**
Files: `app/main.py` routes `/api/chat`, `/api/profile/confirm`, `/api/search`, `/api/medium` (calls T4's `medium_search`, or a stub until T4 lands); `app/templates/results.html`.
- AC: in the browser, a full conversation → read-back → ranked list with denomination, confidence, distance, why lines, Google attribution. Select 1–5 → `/api/medium` stub shows "coming".

### T7 — Deep-search agent + report (Daniel + Carter, 3 h) → **M1 8:00 pm**
Files: `app/stage3/agent.py`, `app/stage3/tools.py`, `app/stage3/report.py`, `app/templates/report.html`, `tests/test_agent.py`; Daniel adds routes `/api/deep`, `/api/jobs/{id}`, `/reports/{id}`, `/api/log/{session}` in `main.py`.
- Loop + budgets + settled/open recompute + StepLog + escalations per ARCHITECTURE §8; tools wrap T1/T2/T4/T5 + Wayback CDX + news search.
- AC (FakeLLM scripted tool calls): stops when all important features settled; budget stop raises `budget_exhausted` escalation when a dealbreaker is open; a non-verbatim quote is rejected, logged as `correction`, and the agent's retry succeeds; every step has `why` in the log; report renders stated-vs-observed rows and questions for a visit; refuses features not in features.yaml.

### T8 — Eval + submission (all, 8:00–9:45 pm)
- Wade: run eval script (EVAL_PLAN) → `eval/results.md`.
- Carter: Agent Build Doc filled from logs + PROMPT_HISTORY; 250-word description (`docs/SUBMISSION.md`).
- Daniel: README quick start; tag `v0.1-prelim`; submit. Start one deep dive before the presentation; record backup video.
- Katie: role-play 3 personas through the live app (EVAL_PLAN §4) and log issues.

## Cut order (if behind at 6:00 pm, cut from the top; never cut guardrails, logging, or the report)
1. OSM fallback (Places only). 2. Wayback in deep search. 3. Locator cross-search (name + website only). 4. Background jobs (run deep search inline for one church, pre-started). 5. Transcription (use existing transcripts/captions only). 6. MCP server (in-process DenomKB only; MCP overnight).

## Overnight / Thursday (M3)
Hosted deploy; MCP over HTTP (open endpoint bonus); publish eval set; cost table; prompt v2s from failures; video.
