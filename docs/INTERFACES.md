# ChurchFocus — INTERFACES (name registry; anti-drift contract)

Every module, function, schema, route, table and env var used across tasks is named here. If you need a new name, add it here first, in the same change.
Containment rules are normative: **nothing outside the named module does that job.**

This file is consolidated to the current code (October 7, after repair tasks X0–X5 and C1–C8). The order in which contracts were added is in §10.

## 1. Repository layout

```
church-discorvery-hackathon/
  AGENTS.md  README.md  LICENSE (MIT)  .env.example  requirements.txt  pytest.ini  run.bat  run.sh
  tasks.json  scripts/tasks.py         # build board; tasks.py is the only writer of tasks.json
  .cursor/rules/church-search.mdc      # always-on rules for Cursor
  contracts/features.yaml              # shared vocabulary (features.v3, 71 features) — read via app.features only
  contracts/source_registry.md         # denomination locator domains and source notes
  denom-kb/                            # sub-project; produces denom-kb/work/out/denominations_kb.json
  christianese-lexicon/                # sub-project; produces work_podcasts/out/lexicon.json (read by app/lexicon.py)
  eval/                                # eval sets (*.jsonl), runner (python -m eval), results.md, denomkb_audit.md
  docs/
  app/
    main.py                  # FastAPI routes only
    config.py                # Settings — only place that reads os.environ
    features.py              # loads contracts/features.yaml; settled/open rule
    models.py                # pydantic schemas (§3)
    db.py                    # SQLite — only place that runs SQL
    llm.py                   # model client, routing, call log — only place that calls a model
    log.py                   # session/step log writer
    web.py                   # guarded public fetcher + YouTube helpers — only place that does HTTP to non-API sites
    chat.py                  # conversation message store
    memory.py                # append-only preference memory → PreferenceProfile
    lexicon.py               # christianese hints for the interview
    jobs.py                  # research job lifecycle (selection, pins, cancel, reuse, retention)
    qa.py                    # source-checked church Q&A + Open questions
    match.py                 # deterministic matcher — only place that scores
    evidence_rules.py        # allowed values, free-form features, marriage rule (Stage 2 + 3)
    stage0/conversation.py   # v2 chat turn (default UI)
    stage0/interviewer.py    # v1 scripted interviewer (/v1); also holds CRISIS_* constants used by v2
    stage1/places.py         # Google Places — only place that calls Google
    stage1/osm.py            # Overpass fallback
    stage1/denomination.py   # denomination resolution; v1 light_search
    stage1/search.py         # v2 discovery coverage + churches table
    stage2/website.py        # bounded recursive site scan
    stage2/summary.py        # medium_search (factual summary + evidence)
    stage3/agent.py          # deep-research loop
    stage3/tools.py          # tool functions exposed to the agent
    stage3/sermons.py        # feeds, YouTube, transcription, sermon analysis
    stage3/report.py         # Church Report JSON/HTML
    denom/kb.py  denom/mcp_server.py  denom/curated.yaml
    prompts/<name>.vN.md     # versioned prompts; loaded by llm.load_prompt
    templates/app.html (v2) · report.html · base.html, chat.html, results.html, cards.html (v1)
    static/app.js · app.css · focus-scene.svg
  tests/                     # offline suite; fakes.py, fixtures/
  data/                      # runtime (gitignored): app.db, cache/, logs/, reports/
```

## 2. Environment variables (`app/config.py` → `Settings`)

| Var | Default | Use |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | — | Places API (New): discovery + geocode |
| `OPENAI_API_KEY` | — | models, transcription, web search tool |
| `OPENAI_MODEL_FAST` | — | fast tier (we use `gpt-5.6-luna`) |
| `OPENAI_MODEL_STRONG` | — | strong tier (we use `gpt-5.6-terra`) |
| `OPENAI_TRANSCRIBE_MODEL` | — | sermon audio (we use `gpt-transcribe`) |
| `TOOLS_REASONING_EFFORT` | `none` | `reasoning_effort` on `chat_tools`; empty = not sent. Must be `none` for terra on Chat Completions |
| `LLM_BACKEND` | `openai` | `openai` \| `ollama` |
| `OLLAMA_URL` | `http://localhost:11434` | local models |
| `OLLAMA_MODEL_FAST` / `OLLAMA_MODEL_STRONG` | `qwen2.5:7b` / `qwen2.5:14b` | local routing |
| `DENOM_KB_PATH` | `denom-kb/work/out/denominations_kb.json` | denomination KB |
| `FEATURES_PATH` | `contracts/features.yaml` | vocabulary |
| `DATA_DIR` | `data` | db, cache, logs, reports (tests point it at a temp dir) |
| `DEEP_MAX_MINUTES` | `120` | deep-dive time budget |
| `DEEP_MAX_TOOL_CALLS` | `150` | deep-dive tool-call budget |
| `DEEP_MAX_SERMONS` | `25` | sermons per church |
| `CACHE_MAX_AGE_DAYS` | `30` | legacy page-cache window (v1); v2 freshness rules are in `jobs.py` (§4) |
| `USER_AGENT` | `ChurchFocus/0.1 (+contact email)` | fetcher identity |

Secrets live only in `.env` (gitignored). Never log keys.

## 3. Schemas (`app/models.py`, pydantic v2)

```python
Weight = Literal["dealbreaker", "important", "nice_to_have", "dont_care"]
Tier   = Literal["A", "B", "C", "D", "prior"]     # A church's own words · B independent source · C weak signal · D observed (sermons) · prior = typical for denomination
Fit    = Literal["strong", "possible", "unknown", "unlikely", "poor"]

class Preference:        feature; want: list[str] = []; avoid: list[str] = []; weight: Weight = "important"; said = ""; strength = 0.5; conf = 1.0
class PreferenceProfile: session_id; for_whom: "self"|"other" = "self"; origin: dict = {}; max_miles = 15; preferences: list[Preference] = []; likely_denominations = []; confirmed = False
class Evidence:          feature; value; tier: Tier; quote = ""; url = ""; source_kind; how: "stated"|"observed"|"inferred"|"prior"; checked_at; note = ""
class DenomGuess:        denomination_id: str|None; label = "Unknown"; confidence = 0.0; method: name|locator|network|website|inferred|unknown; evidence = []
class Church:            church_id (place_id, "osm:<id>" or "url-<hash>"); name; address; lat; lng; website; phone; distance_miles; denomination: DenomGuess; evidence = []; stage_done = 1
class MatchResult:       church_id; score 0..100; excluded; fit: Fit = "unknown"; known_share; matched; unmatched; unknown; why: list[str] (<= 4)
class StepLog:           ts; session_id; church_id; stage; step; action; why; input; result_summary; features_moved; tokens; cost_usd; ms
class Escalation:        reason: pastoral_or_crisis|dealbreaker_conflict|low_denom_confidence|budget_exhausted; message; questions_to_ask
class ChurchReport:      church; profile_session; match; settled; open; stated_vs_observed; sermons_analysed; escalations; questions_for_visit; generated_at; log_path

class MemoryOp:          # one line of the memory log (D27)
    t: int = 0                                          # turn
    op: Literal["assert", "revise", "confirm", "retract"] = "assert"
    key: str                                            # feature id, or "location" | "for_whom" | "denomination"
    val: str | list[str] | dict = ""                    # location: {"text","lat","lng","limit_miles"}
    stance: Literal["want", "avoid", "neutral"] = "want"
    strength: float = 0.5;  conf: float = 0.5
    src: Literal["stated", "inferred", "confirmed", "user_edit", "lexicon"] = "inferred"
    ev: str = "";  why: str = "";  supersedes: int | None = None
```

## 4. Function signatures

```python
# app/config.py
def get_settings() -> Settings                      # cached; .env via python-dotenv

# app/llm.py   (task names below are the keys FakeLLM uses in tests)
def load_prompt(name: str) -> str                   # "deep_search.v5" -> app/prompts/deep_search.v5.md
def complete_json(task, messages, schema, tier="fast", **kw) -> dict   # json_object + schema note, validate, 1 repair retry
def chat_tools(task, messages, tools, tier="strong", tool_choice=None) -> dict   # retries 429/5xx; reasoning_effort from settings
def transcribe(audio_path: str) -> str
def web_search(query: str, max_results: int = 8) -> list[dict]   # [{title, url, snippet}] from url_citation annotations
# tasks: interview_turn, crisis_check, denom_answer, memory_edit, denom_classify, medium_extract, church_qa,
#        deep_search, sermon_analyse, report, prior_map (+ v1: interviewer, readback, page_extract)
# every call appends {ts, task, model, ms, prompt_tokens, completion_tokens, cost_usd, ok} to data/logs/calls.jsonl

# app/web.py
class Blocked(Exception)
def fetch(url, max_chars=20000, *, max_age_days=None) -> dict   # {url, status, text, links:[{href,text}], from_cache, checked_at, text_truncated}; raises Blocked
    # GET only; public http(s) only (private/local hosts refused, also on redirects); robots.txt; >= 1 s/domain; URL blocklist;
    # hero+body extraction incl. published embedded page/menu JSON (no JS execution); only 2xx text/html with real text cached
def scrub_pii(text, *, keep_contact=None) -> str
def youtube_video_id(url) -> str | None
def youtube_videos(url, limit=25) -> dict           # public uploads/livestreams for a video, channel or playlist URL
def youtube_captions(url) -> dict                   # {text, segments, minutes, source, is_generated} or explicit error

# app/db.py   (DATA_DIR/app.db; init() creates tables and adds job columns)
def init() -> None
def save_profile(p) / get_profile(sid)                                   # v1
def upsert_church(church_id, website) / church_last_checked(church_id)
def add_evidence(church_id, ev: list[Evidence]) / get_evidence(church_id) -> list[Evidence]
def create_job(job_id, session_id, church_id) / update_job(job_id, **fields) / get_job(job_id) -> dict
    # update_job fields: status, progress, finished_at, report_path, kind, cancel, done, total, label, result_json,
    #                    generation, visible, announced, verified_at; never overwrites a cancelled terminal state
def jobs_for_session(sid) -> list[dict];  latest_job(church_id, kind, status="complete") -> dict | None
def jobs_purge(kind, cutoff_iso) -> int;  jobs_orphan_cleanup() -> int     # startup
def cache_get(key) -> (datetime, str) | None;  cache_put(key, body)
def memory_append(sid, op_jsons);  memory_rows(sid) -> list[str]
def message_add(sid, role, text, meta=None) -> int  # sequence n allocated in an immediate transaction
def messages_since(sid, n=0) -> list[dict]          # {n, role, text, meta, ts}
def coverage_add(sid, lat, lng, radius_mi, queries);  coverage_get(sid) -> list[dict]
def session_church_put(sid, church_id, candidate, denom=None);  session_churches(sid);  session_church_reset(sid)
def question_add(sid, church_id, text) -> int (deduplicated);  question_update(qid, **{text,status,answer});  questions_list(sid, church_id=None)
def session_state(sid) -> dict   # {generation, pins, selection_mode, selected, active_church, restart_pending, pending_location, discovery{status,label}, ...}
def state_update(sid, **fields) -> dict;  state_bump(sid, key) -> int;  state_reset(sid) -> dict   # reset: generation+1, clears pins/selection/discovery
def research_put(church_id, url, text, *, kind="website", title="", speaker="", published_at="", scope="medium", checked_at=None)
def research_sources(church_id, scope=None) -> list[dict]   # {url,text,kind,title,speaker,published_at,scope,checked_at,text_hash}; within retention
def research_purge() -> int                                  # medium > 90 d, deep > 365 d
def delete_test_sessions(session_ids) -> dict                # maintenance: removes test chats and unshared research

# app/log.py
def write_step(step: StepLog) -> None    # DATA_DIR/logs/<session>/<church or 'session'>.jsonl
def read_session(session_id) -> list[dict]

# app/features.py
def load_features() / all_features() -> dict;  feature(fid) -> dict;  stage0_questions(level) -> list[dict]
def data_points(note) -> int;  is_settled(evidence, fid) -> bool
def settled_open(evidence, feature_ids) -> (settled, open)   # the ONLY settled/open implementation

# app/evidence_rules.py
def allowed_values(fid) -> set[str];  is_free_form(fid) -> bool;  value_ok(fid, value) -> bool   # placeholders never accepted as values
def marriage_rule_violation(fid, value, quote) -> str | None

# app/chat.py
def post(sid, text, meta=None, role="bot") -> int   # meta: {options, kind, church_id, report_url}
def since(sid, n=0) -> list[dict]

# app/memory.py
def append(sid, ops: list[MemoryOp]) -> list[str]    # validates key/value vs features.yaml; returns dropped reasons; bumps versions
def log(sid) -> list[MemoryOp];  current(sid) -> dict[str, MemoryOp];  next_turn(sid) -> int
def location(sid) -> dict | None
def to_profile(sid) -> PreferenceProfile            # fixed rules (ARCHITECTURE §5); want/avoid independent; assert adds, revise replaces
def plain_summary(sid) -> list[dict]                 # About-you rows {key, text, src, src_text, conf, ev}
def context(sid) -> dict                             # current view + history with reasons, for the model
def user_edit(sid, key, text) -> list[str]           # delegates to conversation.apply_user_edit
def version(sid) / table_version(sid) / bump_table(sid);  dump(sid) -> str (JSONL)

# app/lexicon.py
def entries() -> list[dict];  hints(text, limit=8) -> list[dict]   # {term, aliases, gloss, features, source}

# app/stage0/conversation.py
def turn(session_id, text: str | None) -> dict       # {"messages": [...new]}; None = greeting (once)
def know_more(session_id, church_ids, post=True) -> dict   # = jobs.select
def restart(session_id, mode: "new"|"continue") -> dict    # {session_id, research}
def apply_user_edit(session_id, key, text) -> list[str]    # "" retracts; location re-geocodes
GREETING; TURN_SCHEMA  # turn output: {reply, memory_ops[], location{text,limit_miles}|null, intent, church_ref, church_refs[], denomination (str|list|null), options[]|null}
# intents: chat · search_now · church_question · denomination_question · know_more · find_these_out · new_search

# app/stage1/places.py
def search_churches(lat, lng, radius_m, query="church", max_results=60) -> list[dict]   # radius capped at 50 km
def geocode(text) -> (lat, lng);  clean_origin(text) -> str;  class GeocodeError(ValueError)
# app/stage1/osm.py
def search_churches_osm(lat, lng, radius_m) -> list[dict]
# app/stage1/denomination.py
def resolve(candidate: dict, *, use_website=True) -> DenomGuess   # name/alias → OSM tag → (locator, disabled) → website classify
def score_to_confidence(score) -> float;  light_search(profile) -> list[(Church, MatchResult)]   # v1
LOCATOR_ENABLED = False
# app/stage1/search.py
def ensure_coverage(sid, lat, lng, radius_mi) -> int          # 1 circle (<=12 mi) or centre + ring of 6; publishes per circle; generation-fenced
def request_coverage(sid, lat, lng, radius) -> None           # background ensure_coverage + session_state.discovery status
def reset(sid) -> None                                        # jobs.reset_session + session_church_reset
def covered_radius(sid, lat, lng) -> float
def churches(sid) -> list[Church];  get_church(sid, church_id) -> Church | None
def ranked(sid) -> list[(Church, MatchResult)]                # excluded dropped; fit, score, distance order
def table(sid, *, radius_mi=15, sort="fit"|"distance"|"name", page=1, size=10) -> dict
    # {rows:[{church_id,name,address,distance_miles,denomination,denom_confidence,fit,score,why[<=3],website,
    #         stage2,progress|null,pinned,mismatch,affiliation_verified,summary|null}], total, page, pages, radius_mi, covered_mi, research, discovery}
def top(sid, n=5, radius_mi=None) -> list[Church]
def add_url(sid, url) -> Church                               # supplied public site; affiliation unverified

# app/match.py
def score(church, profile, kb=None) -> MatchResult
def score_evidence(church_id, profile, best, denom_conf=0.0, variability=..., denom_label=..., distance_miles=None) -> MatchResult
def agreement(e, want, fid, avoid=None) -> int;  fit_label(score, known_share) -> Fit;  strength(e, denom_conf, var) -> float
WEIGHT = {dealbreaker: 5, important: 3, nice_to_have: 1, dont_care: 0}

# app/jobs.py
def submit(sid, church_id, kind: "medium"|"deep", *, questions=(), visible=True) -> str   # dedup per church/kind/generation; medium reuse < 7 d
def prepare(sid) -> dict                     # Learn more: top 5 started invisibly, selection_mode on → {jobs, research}
def select(sid, ids: list[str]) -> dict      # 1–5 (ValueError otherwise); pins, shows selected, cancels unpinned unselected
def pin(sid, church_id, pinned: bool) -> dict  # pin starts medium; unpin cancels unfinished medium
def cancel(job_id) -> None;  is_cancelled(job_id) -> bool   # cancel flag or stale generation
def reset_session(sid) -> None               # state_reset + cancel running jobs
def status(sid) -> list[dict]                # visible current-generation jobs: {job_id, church_id, name, kind, status, done, total, label, pct|null, elapsed_seconds, report_url}
def purge_expired() -> int                   # startup retention
REUSE_DAYS = {"medium": 7, "deep": 30};  KEEP_DAYS = {"medium": 90, "deep": 365}

# app/qa.py
def answer(sid, church_id, question, *, record=True) -> dict   # {answer, sources:[{url, quote}], confident}; never creates a deep job
def open_questions(sid, church_id=None) -> list[dict]          # {id, church_id, church_name, text, status: open|answered|dropped, answer}
def add_question(sid, church_id, text) -> int;  update_question(qid, *, text=None, status=None)

# app/stage2/website.py
def site_pages(url, max_pages=30, *, cancelled=None, progress=None, max_seconds=120, coverage=None, resources=None) -> list[dict]
    # [{url, kind, text, checked_at, from_cache}]; kind in home/about/beliefs/staff/ministries/events/sermons/other
def resource_kind(url, label="") -> str | None
# app/stage2/summary.py
def medium_search(church, profile, *, progress=None, cancelled=None, max_pages=30) -> dict
    # {church, match, settled, open, evidence, pages[{url,kind,checked_at}], facts[{label,value,quote,url,kind,tier,checked_at}],
    #  staff[{name,position,quote,url,tier,checked_at}], resources[{kind,url,label}],
    #  coverage{pages_scanned, limited, failures, missing_basics, extractor_version}, deep_dive_candidate, reason}  |  {church, cancelled: True}
def quote_verbatim(quote, page_text, threshold=90) -> bool

# app/stage3/agent.py
def deep_search(church, profile, job_id) -> ChurchReport   # never leaves a job running; partial report on error/budget/cancel
def run_deep_search_job(church, profile, job_id, *, background=False)
# app/stage3/tools.py — each tool takes ctx (hidden from the model) and returns a JSON-serialisable dict; all logged
fetch_page(ctx, url) · read_source(ctx, url, query="", offset=0) · search_web(ctx, query) · wayback_snapshots(ctx, url, years)
denomination_lookup(ctx, name_or_id) · denomination_locator_search(ctx, church_name, city)   # dispatch only; not offered to the model
find_sermon_feeds(ctx) · get_sermons(ctx, feed_url, limit=25) · transcribe_sermons(ctx, sermon_ids) · transcribe_sermon(ctx, sermon_id)
analyse_sermons(ctx, sermon_ids=None, features=None) · record_evidence(ctx, evidence) · review_coverage(ctx, area, status, summary, urls=None)
escalate(ctx, reason, message, questions) · finish(ctx, summary)
COVERAGE_AREAS = {identity_governance, public_staff, services_worship, ministries_community, stated_beliefs, sermon_teaching, history_public_context}
# app/stage3/sermons.py
find_sermon_feeds(church, *, site_pages=None) -> {feeds:[{url, kind}]}
get_sermons(feed_url, limit=None, *, feed_text=None) -> dict          # RSS/Atom and YouTube URLs; newest first; capped by DEEP_MAX_SERMONS
transcribe_sermon(item, *, church_id=None, http_client=None) -> dict   # captions/transcript first, else audio (ffmpeg chunks > 24 MB)
transcribe_sermons_parallel(items, *, church_id=None, max_workers=3, http_client=None) -> list[dict]
analyse_sermons(texts, features, *, church_id=None, cancelled=None) -> dict   # sermon_analyse.v3 per sermon → tier-D aggregates
prepare_audio_chunks(src, work_dir=None) -> list[Path];  log_throughput(...)
# app/stage3/report.py
build_report(church, profile, ctx) -> (ChurchReport, narrative);  render_html(report, *, narrative=None) -> str
save_report(report, job_id, *, narrative=None) -> Path;  build_stated_vs_observed(evidence);  build_questions_for_visit(...);  safe_url(url)

# app/denom/kb.py
def get_kb() -> DenomKB
class DenomKB:  find(text, k=5) · is_christian(id) · get(id) · prior(id, feature) · variability(id, feature) · compare(a, b, features) · match_profile(profile, k=8)
NON_NICENE_IDS;  NON_NICENE_NAME   # D29: removed everywhere; is_christian() is False for them
```

### KB JSON shape (`denominations_kb.json`, schema denom-kb.v1)
`{"groups": [{"id", "census_name", "name", "census_2020": {...}, "processed": bool, "fields": {"<layer.field>": {"value": str|null, "status": str, "evidence": [{"action","quote","url","source_type","confidence","model","checked_at","human_decision"?}]}}}]}`. Aliases and abbreviations: `fields["identity.aliases"].value` / `fields["identity.abbreviations"].value` (split on `[;,]`). Sensitive priors only when the value is an original workbook value or its latest evidence has `human_decision == "accept"`. Tests use `tests/fixtures/kb_small.json` (5 real groups).

## 5. HTTP routes (`app/main.py`)

v2 (default UI, `/`):

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/` | | `app.html` |
| POST | `/api/chat` | `{session_id?, text}` (`text: null` = greeting) | `{session_id, messages}` |
| GET | `/api/state/{sid}` | `?since=n` | `{messages, jobs, table_version, memory_version, location, research}` — client polls every 2 s |
| GET | `/api/churches/{sid}` | `?radius&sort&page&size` | `search.table(...)`; radius defaults to the remembered limit |
| POST | `/api/know_more` | `{session_id, action: "prepare"}` or `{session_id, church_ids[1..5]}` | `{jobs, research}`; 422 on invalid selection |
| POST | `/api/pin` | `{session_id, church_id, pinned: bool}` | `{jobs, research}` |
| POST | `/api/restart` | `{session_id, mode: "new"\|"continue"}` | `{session_id, research}` |
| POST | `/api/radius` | `{session_id, radius}` (1–50) | `{location}`; logs a user_edit and expands discovery |
| GET / POST | `/api/memory/{sid}` | POST `{key, text}` | `plain_summary`; 422 if the edit can't be applied |
| GET / POST | `/api/questions/{sid}` | POST `{church_id, text}` or `{id, text?, status?}` | `open_questions`; 404 for another session's question |
| POST | `/api/deep` | `{session_id, church_id, questions?[]}` | `{job_id}` |

Shared and v1:

| Method | Path | Returns |
|---|---|---|
| GET | `/reports/{job_id}` | Church Report HTML |
| GET | `/api/jobs/{job_id}` | `{status, progress, last_steps, report_url?}` |
| GET | `/api/log/{session_id}` | session step log (JSONL) |
| GET | `/healthz` | `{"status": "ok"}` |
| GET | `/v1` | v1 scripted interview page |
| POST | `/v1/api/chat`, `/v1/api/profile/confirm`, `/v1/api/search`, `/v1/api/medium`, `/v1/api/deep`; GET `/v1/results/{sid}` | v1 flow |

Errors from v2 handlers: `KeyError`/`ValueError` → 422 `{error}`; `NotImplementedError` → 501.

## 6. SQLite tables (`app/db.py`)

| Table | Columns | Notes |
|---|---|---|
| `sessions` | id, created_at, profile_json | v1 profiles |
| `churches` | church_id, website, last_checked | only place_id + a website we fetched; no other Google fields |
| `evidence` | church_id, feature, value, tier, quote, url, source_kind, how, checked_at, note | reusable across sessions |
| `jobs` | job_id, session_id, church_id, status, progress, started_at, finished_at, report_path, kind, cancel, done, total, label, result_json, generation, visible, announced, verified_at | added columns migrate on `init()` |
| `memory_log` | id, session_id, op_json, ts | append-only |
| `messages` | session_id, n, role, text, meta_json, ts | PK (session_id, n) |
| `coverage` | session_id, lat, lng, radius_mi, queries, ts | discovery ledger (D40) |
| `session_churches` | session_id, church_id, candidate_json, denom_json | Places data in session scope only |
| `questions` | id, session_id, church_id, text, status, answer, ts | Open questions |
| `session_state` | session_id, state_json | generation, pins, selection, discovery, restart choice |
| `research_sources` | church_id, url, scope, text, kind, title, speaker, published_at, checked_at, text_hash | saved public pages and sermon transcripts; medium 90 d, deep 365 d |
| `cache` | key, fetched_at, body | public page cache (versioned extraction) |

## 7. MCP server (`app/denom/mcp_server.py`)
Tools (thin wrappers of `DenomKB`): `find_denomination(text)`, `get_denomination(id)`, `compare_denominations(a, b, features)`, `match_profile(profile_json)`, `denomination_prior(id, feature)`. Run with `python -m app.denom.mcp_server` (stdio).

## 8. UI contract (`app/templates/app.html`, `app/static/app.js`, `app/static/app.css`)
The UI talks only to the §5 v2 routes.
- **Layout:** chat thread with Enter to send (Shift+Enter for a new line), option chips that send on click, a typing indicator, and near-bottom autoscroll.
- **Tabs:** Churches (radius, sort, size, pager, pin buttons, Learn more → checkboxes → Research selected, Ask a question / Deep dive per row, factual summary per row), About you (edit or remove items), Open questions (add, edit, drop).
- **Status:** a persistent research banner above the composer shows real stage, counts and elapsed time, plus report links or failures.
- **Background:** `updateFocus()` drives the decorative focus scene from real state: 0 no origin, 1 nearby list, 2 website research running, 3 website research done, 4 deep running or partial, 5 deep complete. It never shows invented percentages, is aria-hidden, and respects reduced motion.
- **State:** the session id, draft text and selection are kept in localStorage. Older responses are ignored once a newer request has been sent.

## 9. Prompts in use
`interview_skill.v8` (conversation) · `crisis_check.v2` · `denom_classify.v1` · `medium_extract.v1` · `deep_search.v5` · `sermon_analyse.v3` · `report.v1`; inline prompts `denom_answer`, `memory_edit`, `church_qa`. v1 flow: `interviewer.v1`, `readback.v1`. History and reasons: `docs/PROMPT_HISTORY.md`.

## 10. Contract history (most recent last)
1. **T0–T8 (afternoon):** v1 scripted interviewer → light search → cards → deep agent.
2. **W0–W7 (evening rebuild, `docs/REDESIGN.md`):** chat UI, memory log, lexicon, search coverage, background jobs, Q&A, v1 moved to `/v1`.
3. **X0–X5 (repair, `docs/REPAIR_PLAN.md`):**
   - session generation, pins and selection;
   - explicit research authorization;
   - broad factual medium research with saved sources;
   - seven-area deep coverage and a sermon corpus;
   - `read_source`, `review_coverage`, restart and radius routes.
4. **C1:** ChurchFocus name and focus scene.
5. **C2:** recovery of published embedded content.
6. **C3:** YouTube sermons and captions.
7. **C4:** `deep_search.v5` channel recovery.
8. **C5:** independent want/avoid and broad families (`interview_skill.v6`).
9. **C6:** head-pastor negation (v7).
10. **C7:** Protestant scope (v8).
11. **C8:** bounded one-or-seven-circle discovery with per-circle publishing. Supersedes C7's recursive refinement.
