# ChurchFocus — ARCHITECTURE

Current as of the October 7 repair (tasks X0–X5, C1–C8). Names and signatures are in `docs/INTERFACES.md`; lifecycle rules in `docs/REPAIR_PLAN.md`.

## 1. Stack
Python 3.11+, FastAPI, Jinja2, vanilla JavaScript (no build step), SQLite, httpx, pydantic v2, PyYAML, rapidfuzz, feedparser, trafilatura, pypdf, yt-dlp and youtube-transcript-api (sermon captions), imageio-ffmpeg (bundled ffmpeg), `mcp` for the denomination server. Background work runs in thread pools inside the app, with job state in SQLite. No Docker needed.

## 2. Data flow

```
 browser (app.html + app.js, polls /api/state every 2 s)
    │ POST /api/chat
    ▼
 Stage 0  conversation.turn ──┬─ crisis check (fast, in parallel) ─▶ 988 message
          interview_skill.v8  │
          + memory, lexicon   ├─ memory ops ─▶ memory.append (validated against features.yaml)
                              │                     └─ memory.to_profile ─▶ PreferenceProfile (fixed rules)
                              ├─ location ─▶ places.geocode ─▶ search.request_coverage (background)
                              └─ intent ─▶ qa.answer · KB answer · jobs.select/prepare · jobs.submit(deep)
    ▼
 Stage 1  search.ensure_coverage: Places Text Search circles (OSM fallback) → denomination name/KB match
          → non-Nicene removed → session_churches; publish after each circle
          search.table: match.score vs CURRENT memory → fit label, sort, radius filter, pins first
    │ Learn more → Research selected (1–5) / pin
    ▼
 Stage 2  jobs (5 medium workers) → summary.medium_search: site_pages (≤30 pages, 120 s)
          → medium_extract.v1 per page → verbatim-checked facts, full public staff, evidence, resources, coverage
          → research_sources (saved page text) → factual summary on the church row
    │ chat question                         │ Deep dive (button or chat)
    ▼                                       ▼
 qa.answer: saved sources + sermon text    Stage 3  jobs (2 deep workers) → agent.deep_search (deep_search.v5 + tools)
 → exact-quote check → answer or            → seven coverage areas, sermons, open questions → ChurchReport HTML
   Open question                            → open questions answered from new sources
 everything ──▶ data/logs (calls.jsonl, <session>/<church>.jsonl), data/app.db, data/reports/
```

## 3. Models and routing
`app/llm.py` is the only model client: OpenAI Chat Completions (or Ollama with `LLM_BACKEND=ollama`). Tasks ask for a tier, and `.env` maps the tiers to models (fast = gpt-5.6-luna, strong = gpt-5.6-terra in our setup).

| Task (`llm` task name) | Tier | Notes |
|---|---|---|
| `interview_turn` | strong | JSON: reply, memory_ops, location, intent, church refs, options |
| `crisis_check` | fast | runs in parallel with the turn; keyword hard-triggers first |
| `denom_answer` | fast | denomination summaries and comparisons from KB facts only |
| `memory_edit` | fast | About-you edits → memory ops |
| `denom_classify` | fast | website denomination check for displayed rows; verbatim quote required |
| `medium_extract` | fast | per page: facts, staff, features (any stage ≤ 3 feature, not just preferences) |
| `church_qa` | fast | answer from saved sources; quotes re-checked |
| `deep_search` | strong, tool calling | `reasoning_effort` from `TOOLS_REASONING_EFFORT` (must be `none` for terra on Chat Completions) |
| `sermon_analyse` | fast | per sermon, aggregated by code |
| `report` | strong | narrative only from recorded evidence |

JSON calls use `response_format=json_object` with a schema note (the word "JSON" must appear), schema validation and one repair retry. Tool calls retry on 429/5xx. Every call is logged with tokens and cost to `data/logs/calls.jsonl`. `llm.web_search` uses the OpenAI web search tool and parses url_citation annotations. Transcription uses `OPENAI_TRANSCRIBE_MODEL`.

## 4. Stage 0 — conversation (`app/stage0/conversation.py`)
- One strong-model call per turn, using `interview_skill.v8` plus context: ABOUT YOU (current view and history with reasons), research state, starting place, lexicon hints for words in the message, up to 150 ranked churches with ids, the 120 largest Nicene denominations, and the feature catalog.
- The model proposes **memory ops**; the server decides. `_apply_ops` normalizes known model mistakes before `memory.append` validates keys and values against `features.yaml`. The fixes are: `add`→`assert`; strength capped at 0.7 unless the words are mandatory ("must", "only", "never"…); broad families to `identity.tradition` and Catholic avoidance to `identity.branch`; hymns to `worship.music_sources`; "biblical authority" doesn't mean inerrancy; head-pastor negation; "definitely Protestant" stored as a branch filter. Dropped ops are logged with reasons.
- Intents the server carries out (the model never claims an action happened):
  - `church_question` → `qa.answer` (up to 3 churches).
  - `denomination_question` → KB summary or comparison.
  - `know_more` → `jobs.select` / `jobs.prepare`.
  - `find_these_out` → `jobs.submit(..., "deep")`, only for a church name that resolves uniquely, and only acknowledged after the job exists.
  - `new_search` → offer "Start a new chat" or "Change this search here" before resetting anything.
  - A pasted URL → `search.add_url` and a pin.
- Location: geocode the text. The same town (within 0.5 mi) only changes the limit. A new place resets the session's candidates and jobs (new generation) and starts discovery. Discovery itself never posts chat messages.
- Crisis: `crisis_check.v2` in parallel, plus hard keyword triggers (self-harm or danger only). On escalation the 988 message replaces the reply and no memory is written.
- The greeting is posted once per session. A per-session lock serializes turns.

## 5. Memory (`app/memory.py`)
- An append-only log of `MemoryOp`s per session (SQLite `memory_log`). Nothing is overwritten: `revise` and `retract` are new lines that point at what they change.
- Want and avoid views of a feature are independent. `assert` adds values, while `revise` and `retract` replace or withdraw them.
- `to_profile` turns the log into a `PreferenceProfile` with fixed rules. Weight comes from strength: ≥ 0.8 dealbreaker, ≥ 0.5 important, > 0.15 nice to have. Confidence < 0.5 drops one step, and inferred sensitive guesses are capped at nice to have.
- `plain_summary` feeds the About-you tab. `context` gives the model the current view plus historical corrections and reasons.

## 6. Stage 1 — discovery and the churches table (`app/stage1/search.py`)
- **Places API (New) Text Search** (`places.py`): `textQuery: "church"`, `includedType: church`, `locationBias` circle, paging to 60 results. Cities have no `businessStatus`, so `geocode` doesn't filter on it.
- **Coverage:** a radius up to 12 mi uses one circle. Larger radii use a centre circle plus a ring of six (radius/2 each), up to 50 mi. Each completed circle publishes immediately. Coverage is recorded per origin, and a larger radius queries only if it goes beyond what is covered (D40). OSM Overpass is the fallback.
- **Denomination:** for every candidate, a cheap `denomination.resolve(use_website=False)` (name/alias against the KB, OSM tag, non-denominational wording). Website classification runs in the background only for rows actually shown with confidence < 0.8. The locator web-search step is disabled. Non-Nicene groups (LDS, Jehovah's Witnesses, Christian Science, Unitarian, Oneness and similar) are dropped (D29).
- **Generations:** every reset or new place increments the session generation. Discovery and jobs from an older generation stop and cannot publish.
- **Table:** scores every candidate against the current profile, filters by radius (pinned churches always shown, with an honest "outside current preferences" note), sorts by fit then score then distance (or by distance or name) and pages. Each row carries research status and the factual summary when the person has authorized it.

## 7. Matcher (`app/match.py`, D20, D28)
- Weights: dealbreaker 5, important 3, nice to have 1. Best evidence per feature: tier A > B > D > C > denomination prior.
- Strength: A 1.0, B 0.8, C 0.5, D 0.6 (≥ 5 data points) or 0.4. A prior is 0.5 × denomination confidence × (1 − variability); identity priors use the denomination confidence itself.
- Agreement is +1 when a wanted value is present and −1 for a conflict or an avoided value. Unknown is 0, with a small penalty for an unknown dealbreaker.
- Score = 50 + 50 × Σ(w·s·m) / Σw. Fit: known share < 0.4 → "Not enough info yet"; otherwise ≥ 70 strong, ≥ 58 possible, ≥ 45 unlikely, else poor. A known important worship mismatch can't be labeled strong or possible.
- **Exclusion** only at the identity level: an avoided denomination, tradition or branch, or a confirmed mismatch with an explicit dealbreaker affiliation request. Practice and belief preferences only move fit. Avoid-only preferences don't reward unrelated churches.
- `why`: up to two positives, one negative, and "N things to check later", in plain words.

## 8. Research jobs (`app/jobs.py`)
- Pools: 5 medium workers, 2 deep workers. A job row records kind, generation, visible, status, done/total, label, cancel, result_json, report_path, verified_at and announced.
- **Authorization:** no website research starts on its own (U42).
  - **Learn more** (`jobs.prepare`) quietly starts the top 5 as invisible jobs and opens a picker.
  - **Research selected** (`jobs.select`, 1–5 churches) pins them, shows their jobs, and cancels the unselected ones that aren't pinned.
  - **Pin** starts one church. **Unpin** cancels its unfinished work.
- Submitting is deduplicated per church, kind and generation. Cancellation is cooperative: workers check `is_cancelled()` (cancel flag or stale generation) between pages, tools and model calls, and before every write.
- **Reuse:** a medium result younger than 7 days (and at `research_version` 5) is reused. A deep dive always builds a fresh report for this person but reuses saved sources. Pages older than 30 days are re-checked, and copying cached text never moves a source's date forward.
- **Retention:** medium research 90 days, deep research 365 days, purged at startup. Jobs left running by a dead process are marked as errors at startup.
- **Progress:** real page and tool counts, plus elapsed seconds. The UI shows a persistent deep-dive banner above the composer and never invents percentages. Completed medium research shows as a summary on the church row, not as a chat message (U41). A finished deep dive posts one chat message with the report link.

## 9. Stage 2 — website research (`app/stage2/`)
- `website.site_pages` scans the public site recursively, up to 30 pages and 120 seconds by default. It ranks links (beliefs, staff, about, sermons, ministries, events), skips repeated footers, and catalogs calendars, group directories and documents as resources instead of downloading them all. It records which pages were scanned and which failed.
- `web.fetch` extracts hero and body text (trafilatura plus HTML repair). It recovers content that sites publish as embedded page or menu JSON without running JavaScript, and refuses to cache empty shells.
- `summary.medium_search` uses `medium_extract.v1` per page, 4 pages in parallel. It collects facts `{label, value, quote}`, the full public staff list `{name, position, quote}` and feature evidence, all regardless of the person's preferences. Every quote must appear in the page text, and name and position must appear in the staff quote. It keeps the best evidence per feature and applies the marriage rule. Coverage lists missing basics (service times, public staff, active ministries).
- Full page text is saved to `research_sources` (scope `medium`) for later Q&A. Unchanged pages reuse earlier extraction.

## 10. Q&A and Open questions (`app/qa.py`)
`answer` ranks saved sources (medium and deep pages and sermon transcripts) by relevance to the question and may run a bounded, guarded lookup of a saved resource. The fast model answers with sources, and each quote must match the text at the cited URL. An answer without a verified source is not confident: it says what was found and adds the question to Open questions. It never starts a deep dive by itself.

## 11. Stage 3 — deep research (`app/stage3/`)
- The loop is `chat_tools` with `deep_search.v5`, a "current state" message, which replaces older ones, and a budget (`DEEP_MAX_MINUTES` 120, `DEEP_MAX_TOOL_CALLS` 150, `DEEP_MAX_SERMONS` 25). Every tool call carries a `why` and is logged as a `StepLog`.
- **Seven minimum coverage areas** (`tools.COVERAGE_AREAS`): identity and governance, full public staff, services and worship, ministries and community, stated beliefs, sermon teaching, history and public context. The agent records each with `review_coverage` (supported, partial or not found). "Supported" requires fetched source URLs.
- **Nine adaptive tactics:** denomination sources, the church website, church networks, sermon archives, nonprofit filings (never pay or donor data), local news, worship resources, archived websites, and leaders' published teaching. Preferences and the person's questions set priorities but never limit the scope.
- **Tools:**
  - pages and search: `fetch_page`, `read_source` (12k-character chunks, used to read staff lists and transcripts completely), `search_web`, `wayback_snapshots`;
  - denominations: `denomination_lookup` (typical positions, labeled as priors);
  - sermons: `find_sermon_feeds`, `get_sermons` (RSS, YouTube videos, channels, playlists and livestreams), `transcribe_sermons` (captions first, then transcripts, then audio transcription; 3 in parallel), `analyse_sermons` (`sermon_analyse.v3`; the only source of tier D);
  - results: `record_evidence` (tiers A/B/C, quote re-checked at the exact URL), `review_coverage`, `escalate`, `finish`.
- **Sermons:** if a site's sermon links are broken or missing, `deep_search.v5` recovers the public YouTube channel or playlist and verifies it belongs to the same congregation before using its teaching. Transcripts are saved (scope `deep`, kind `sermon`) for future Q&A. Speaker gender is never inferred from names, voices or photos.
- **Report** (`report.py`, `/reports/{job_id}`): at a glance, how it fits, stated vs observed, notes, still unknown, questions to ask on a visit, sources, your questions, and research coverage. A report with gaps is labeled partial. Report HTML is stand-alone and prints to PDF.
- After the report, open questions for that church are answered from the new sources where possible.

## 12. Cache, freshness and Google data
- `cache` holds fetched public pages. Only 2xx text/HTML is cached, and cache entries carry an extractor version so older extractions are invalidated.
- `research_sources` holds saved public text with `checked_at` and a text hash.
- Google Places content stays in session scope (`session_churches`). Only `place_id` and a website the app itself fetched are kept long-term (`churches`).

## 13. Security and guardrails
- **Fetcher (`web.py`):** GET only, public http(s) URLs only (private IPs refused), robots.txt respected, at least 1 s per domain, browser-like user agent with a contact address. A URL blocklist covers prayer, member directories, login, giving, donation and child check-in pages. Text is length-capped, and emails and phones other than the church's main contact are scrubbed.
- Prompts carry the guardrails. Evidence outside `features.yaml` is refused. Sensitive denomination priors are used only when they come from the original workbook or a human accepted them.
- Secrets come only from `.env`, and logs never include keys.

## 14. Deployment
`run.bat` or `run.sh` → `uvicorn app.main:app` on 127.0.0.1:8000, shared with the team through Tailscale Funnel. Runtime data lives in `data/` (gitignored): `app.db`, `cache/`, `logs/`, `reports/`. The MCP server runs separately (`python -m app.denom.mcp_server`, stdio). Before restarting the server, check that no research jobs are running, because jobs that are running are marked as errors at startup.

## 15. Cost model
- **Per search:** 1 or 7 Places queries, and denomination name matching with no model calls.
- **Per researched church:** about one fast call per page read, up to 30.
- **Per deep dive:** up to 150 strong tool turns and up to 25 sermons. Captions are free; transcription costs about $0.0045 per audio minute.
- Measured $/church belongs in `eval/results.md` from `data/logs/calls.jsonl`. With data sharing on, OpenAI gives complimentary daily tokens: 10M/day for the luna/terra group at our tier. Transcription is not covered.
