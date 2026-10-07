# Church Search — INTERFACES (name registry; anti-drift contract)

Every module, function, schema, route and env var used across tasks is named here. If you need a new name, add it here first.
Containment rules are normative: **nothing outside the named module does that job.**

## 1. Repository layout

```
church-discorvery-hackathon/
  AGENTS.md                    # read-me-first for Cursor / any coding agent
  LICENSE                      # MIT
  .env.example
  contracts/features.yaml      # shared vocabulary (D15) — read via app.features only
  denom-kb/                    # existing; produces denom-kb/work/out/denominations_kb.json
  christianese-lexicon/        # existing; not imported by app tonight (D19)
  docs/                        # PRD, ARCHITECTURE, INTERFACES, CONVENTIONS, BUILD_PLAN, EVAL_PLAN, AGENT_BUILD_DOC, SESSION_LOG
  app/
    __init__.py
    main.py                    # FastAPI app, routes only
    config.py                  # Settings (env vars) — only place that reads os.environ
    features.py                # loads contracts/features.yaml
    models.py                  # pydantic schemas in §3
    db.py                      # SQLite access — only place that runs SQL
    llm.py                     # model client + routing + call log — only place that calls a model
    log.py                     # session/step log writer
    stage0/interviewer.py      # conversation state machine
    stage1/places.py           # Google Places — only place that calls Google
    stage1/osm.py              # Overpass fallback
    stage1/denomination.py     # resolve denomination for a candidate
    stage2/website.py          # fetch + pick pages from a church site
    stage2/summary.py          # Stage 2 card
    stage3/agent.py            # deep-search loop
    stage3/tools.py            # tool functions exposed to the agent (§4)
    stage3/sermons.py          # find feeds, download, transcribe, analyse
    stage3/report.py           # Church Report HTML/JSON
    evidence_rules.py          # allowed values + marriage rule, shared by Stage 2 and Stage 3
    match.py                   # deterministic matcher — only place that scores
    web.py                     # polite fetcher (robots, rate limit, cache) — only place that does HTTP to non-API sites
    denom/kb.py                # denomination KB access (in-process)
    denom/mcp_server.py        # MCP wrapper over denom/kb.py
    denom/curated.yaml         # hand-curated branch/tradition/aliases for the ~50 largest groups
    prompts/*.md               # versioned prompts (name.vN.md); loaded by llm.load_prompt
    templates/*.html           # Jinja2 + HTMX
    static/
  tests/
  data/                        # runtime: app.db, cache/, logs/, reports/  (gitignored)
```

## 2. Environment variables (`app/config.py` → `Settings`)

| Var | Default | Use |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | — (required for Stage 1) | Places API (New) |
| `OPENAI_API_KEY` | — | cloud models + transcription + web search tool |
| `OPENAI_MODEL_FAST` | set by team | cheap extraction/classification |
| `OPENAI_MODEL_STRONG` | set by team | interviewer, deep-search agent, report writing |
| `OPENAI_TRANSCRIBE_MODEL` | set by team | sermon transcription |
| `LLM_BACKEND` | `openai` | `openai` \| `ollama` (whole app) |
| `OLLAMA_URL` | `http://localhost:11434` | local models |
| `OLLAMA_MODEL_FAST` / `OLLAMA_MODEL_STRONG` | `qwen2.5:7b` / `qwen2.5:14b` | local routing |
| `DENOM_KB_PATH` | `denom-kb/work/out/denominations_kb.json` | denomination KB |
| `FEATURES_PATH` | `contracts/features.yaml` | vocabulary |
| `DATA_DIR` | `data` | db, cache, logs, reports |
| `DEEP_MAX_MINUTES` | `120` | Stage 3 time budget (D22) |
| `DEEP_MAX_TOOL_CALLS` | `150` | Stage 3 call budget (D22) |
| `DEEP_MAX_SERMONS` | `25` | Stage 3 sermon cap per church (D22) |
| `CACHE_MAX_AGE_DAYS` | `30` | reuse window before re-check |
| `USER_AGENT` | `ChurchSearch/0.1 (+contact email)` | fetcher |

Secrets live only in `.env` (gitignored). Never log keys.

## 3. Schemas (`app/models.py`, pydantic v2)

```python
Weight = Literal["dealbreaker", "important", "nice_to_have", "dont_care"]
Tier   = Literal["A", "B", "C", "D", "prior"]

class Preference(BaseModel):
    feature: str                    # id in features.yaml
    want: list[str]                 # allowed values from features.yaml (or free value for list/number types)
    weight: Weight
    said: str = ""                  # the user's words this came from

class PreferenceProfile(BaseModel):
    session_id: str
    for_whom: Literal["self", "other"] = "self"
    origin: dict                    # {"text": "Hesston, KS", "lat": .., "lng": ..}
    max_miles: float = 15
    preferences: list[Preference]
    likely_denominations: list[str] = []   # denomination ids from the KB, best first
    confirmed: bool = False         # read-back accepted

class Evidence(BaseModel):
    feature: str
    value: str
    tier: Tier
    quote: str = ""                 # verbatim, <= 60 words
    url: str = ""
    source_kind: str                # from features.yaml source_kinds
    how: Literal["stated", "observed", "inferred", "prior"]
    checked_at: datetime
    note: str = ""                  # e.g. "inferred from 12 sermons: 3 by women"

class DenomGuess(BaseModel):
    denomination_id: str | None     # KB id, or None
    label: str                      # display, e.g. "Mennonite Church USA" or "Non-denominational (likely)"
    confidence: float               # 0..1
    method: Literal["name", "locator", "network", "website", "inferred", "unknown"]
    evidence: list[Evidence] = []

class Church(BaseModel):
    church_id: str                  # = place_id, or "osm:<id>"
    name: str
    address: str
    lat: float; lng: float
    website: str | None
    phone: str | None
    distance_miles: float
    denomination: DenomGuess
    evidence: list[Evidence] = []   # accumulated across stages
    stage_done: int = 1

class MatchResult(BaseModel):
    church_id: str
    score: float                    # 0..100
    excluded: bool                  # violated a settled dealbreaker
    matched: list[str]; unmatched: list[str]; unknown: list[str]   # feature ids
    why: list[str]                  # <= 4 plain-language lines

class StepLog(BaseModel):           # one line in data/logs/<session>/<church>.jsonl
    ts: datetime
    session_id: str; church_id: str | None
    stage: int
    step: int
    action: str                     # tool name or "think"/"stop"/"escalate"
    why: str                        # agent's stated reason
    input: dict
    result_summary: str
    features_moved: list[str]
    tokens: int = 0; cost_usd: float = 0; ms: int = 0

class Escalation(BaseModel):
    reason: Literal["pastoral_or_crisis", "dealbreaker_conflict", "low_denom_confidence", "budget_exhausted"]
    message: str                    # plain language for the user
    questions_to_ask: list[str]     # for the user to ask the church

class ChurchReport(BaseModel):
    church: Church
    profile_session: str
    match: MatchResult
    settled: list[str]; open: list[str]
    stated_vs_observed: list[dict]  # {feature, stated: Evidence|None, observed: Evidence|None, agrees: bool|None}
    sermons_analysed: int
    escalations: list[Escalation]
    questions_for_visit: list[str]
    generated_at: datetime
    log_path: str
```

## 4. Function signatures

```python
# app/config.py
def get_settings() -> Settings          # cached; reads .env via python-dotenv

# app/web.py
def fetch(url: str, max_chars: int = 20000) -> dict   # {url, status, text, links: [{href, text}], from_cache: bool}; raises Blocked for blocklisted/non-robots-allowed URLs
class Blocked(Exception): ...

# app/db.py   (one sqlite file at DATA_DIR/app.db; init() creates tables)
def init() -> None
def save_profile(p: PreferenceProfile) -> None;  def get_profile(session_id: str) -> PreferenceProfile | None
def upsert_church(church_id: str, website: str | None) -> None;  def church_last_checked(church_id: str) -> datetime | None
def add_evidence(church_id: str, ev: list[Evidence]) -> None;  def get_evidence(church_id: str) -> list[Evidence]
def create_job(job_id: str, session_id: str, church_id: str) -> None
def update_job(job_id: str, **fields) -> None;  def get_job(job_id: str) -> dict
def cache_get(key: str) -> tuple[datetime, str] | None;  def cache_put(key: str, body: str) -> None

# app/log.py
def write_step(step: StepLog) -> None   # appends to DATA_DIR/logs/<session>/<church or 'session'>.jsonl
def read_session(session_id: str) -> list[dict]

# app/features.py
def load_features() -> dict            # parsed features.yaml (cached)
def feature(fid: str) -> dict
def stage0_questions(level: Literal["core","standard","if_raised","advanced"]) -> list[dict]
    # returns only features that HAVE a `question` key, in YAML order. The ladder question fills all women.*;
    # "for whom" is a hard-coded turn right after location (not a feature).
def settled_open(evidence: list[Evidence], feature_ids: list[str]) -> tuple[list[str], list[str]]
    # applies features.yaml settled_rule; the ONLY implementation (used by Stage 2, Stage 3, report)

# app/llm.py
def load_prompt(name: str) -> str      # "deep_search.v1" -> app/prompts/deep_search.v1.md
# task name = prompt name without version: interviewer, readback, crisis_check, denom_classify, page_extract,
# sermon_analyse, report, deep_search, prior_map. FakeLLM (tests/fakes.py) keys canned outputs by these names.
def complete_json(task: str, messages: list[dict], schema: dict, tier: Literal["fast","strong"]="fast", **kw) -> dict
def chat_tools(task: str, messages: list[dict], tools: list[dict], tier="strong") -> dict   # tool-calling turn
def transcribe(audio_path: str) -> str
def web_search(query: str, max_results: int = 8) -> list[dict]   # [{title,url,snippet}]
# every call appends to data/logs/calls.jsonl: {ts, task, model, ms, prompt_tokens, completion_tokens, cost_usd, ok}

# app/stage0/interviewer.py
class Interviewer:
    def __init__(self, session_id: str): ...
    def next_turn(self, user_text: str | None) -> dict   # {"say": str, "options": list[str]|None, "done": bool, "escalation": Escalation|None}
    def profile(self) -> PreferenceProfile
    def readback(self) -> str

# app/stage1/places.py
def search_churches(lat: float, lng: float, radius_m: int, query: str = "church", max_results: int = 60) -> list[dict]
def geocode(text: str) -> tuple[float, float]       # Places Text Search on the text; first result location
# app/stage1/osm.py
def search_churches_osm(lat: float, lng: float, radius_m: int) -> list[dict]
# app/stage1/denomination.py
def resolve(candidate: dict) -> DenomGuess          # candidate = {name, website, address, types}
def light_search(profile: PreferenceProfile) -> list[tuple[Church, MatchResult]]

# app/stage2/website.py
def site_pages(url: str, max_pages: int = 8) -> list[dict]   # [{url, kind, text}] kind in home/about/beliefs/staff/ministries/events/sermons/other
# app/stage2/summary.py
def medium_search(church: Church, profile: PreferenceProfile) -> dict   # card dict incl. deep_dive_candidate: "strong"|"possible"|"weak", reason

# app/stage3/agent.py
def deep_search(church: Church, profile: PreferenceProfile, job_id: str) -> ChurchReport
# app/stage3/tools.py   (each returns a dict that is JSON-serialisable; all logged)
# Every tool takes a first parameter ctx: dict = {session_id, church_id, job_id, profile} filled by the runner
# and hidden from the model's tool schema.
def fetch_page(ctx, url: str) -> dict                       # {url, status, text (<= 20k chars), links: [..]}
def search_web(ctx, query: str) -> dict                     # {results: [...]}
def wayback_snapshots(ctx, url: str, years: list[int]) -> dict    # {snapshots: [{timestamp, url}]}
def denomination_lookup(ctx, name_or_id: str) -> dict       # via denom/kb.py
def denomination_locator_search(ctx, church_name: str, city: str) -> dict
def find_sermon_feeds(ctx) -> dict                      # {feeds: [{url, kind}]} for ctx["church"] (model never supplies a church)
def get_sermons(ctx, feed_url: str, limit: int = 25) -> dict    # {sermons: [{sermon_id, title, date, speaker, has_audio, has_transcript}]}; items kept in ctx
def transcribe_sermons(ctx, sermon_ids: list[str]) -> dict      # 3 in parallel; total capped by DEEP_MAX_SERMONS; transcripts kept in ctx
def transcribe_sermon(ctx, sermon_id: str) -> dict              # wrapper over transcribe_sermons
def analyse_sermons(ctx, sermon_ids: list[str] | None = None, features: list[str] | None = None) -> dict
    # analyses transcripts in ctx and RECORDS observed tier-D evidence + verbatim tier-A stated positions itself
def record_evidence(ctx, evidence: list[Evidence]) -> dict  # tiers A/B/C only; url required; 'unstated' allowed;
                                                       # quote re-checked verbatim, then marriage rule (app/evidence_rules.py)
def escalate(ctx, reason: str, message: str, questions: list[str]) -> dict
def finish(ctx, summary: str) -> dict

# app/match.py
def score(church: Church, profile: PreferenceProfile, kb: DenomKB | None = None) -> MatchResult
def score_evidence(church_id, profile, best: dict[str, Evidence], denom_conf=0.0, variability=lambda f: 0.3,
                   denom_label="its denomination", distance_miles=None) -> MatchResult   # core; also used by DenomKB.match_profile

# app/denom/kb.py
def get_kb() -> DenomKB                 # cached singleton from DENOM_KB_PATH
class DenomKB:
    def __init__(self, path: str): ...
    def find(self, text: str, k: int = 5) -> list[dict]          # name/alias search -> [{id, name, score}]
        # score: 100 exact name/alias/abbrev; ~90-98 alias phrase inside a church name; 85-90 fuzzy; 70 family-word guess
        # (e.g. "Trinity Lutheran" -> largest Lutheran bodies). resolve() maps score -> confidence: 100→0.9, ≥88→0.8, ≥85→0.6, 70→0.3.
    def is_christian(self, denom_id: str) -> bool
    def variability(self, denom_id: str, feature_id: str) -> float  # ARCHITECTURE §6 table
    def get(self, denom_id: str) -> dict
    def prior(self, denom_id: str, feature_id: str) -> Evidence | None   # via features.yaml denom_field; tier="prior"
        # value mapping: rule table first, then llm task prior_map (cached). identity.denomination -> groups[].id.
        # sensitive features: prior only if the KB field has a value with evidence == [] (original workbook value)
        # or its latest evidence has human_decision == "accept"; otherwise None.
    def compare(self, a: str, b: str, features: list[str]) -> list[dict]
    def match_profile(self, profile: PreferenceProfile, k: int = 8) -> list[dict]   # likely denominations
```

### KB JSON shape (denom-kb/work/out/denominations_kb.json, schema denom-kb.v1)
`{"groups": [{"id", "census_name", "name", "census_2020": {...}, "processed": bool,
  "fields": {"<layer.field>": {"value": str|null, "status": str, "evidence": [{"action","quote","url","source_type","confidence","model","checked_at","human_decision"?}]}}}]}`
Aliases/abbreviations are free text at `fields["identity.aliases"].value` / `fields["identity.abbreviations"].value` (may be null; split on `[;,]`).
Tests use `tests/fixtures/kb_small.json` (5 groups incl. SBC, ELCA, Mennonite Church USA, Assemblies of God, Catholic Church; real shape).

## 5. HTTP routes (`app/main.py` — only Daniel edits this file; others deliver handler functions in their modules)

| Method | Path | Returns |
|---|---|---|
| GET | `/` | start page (chat) |
| POST | `/api/chat` `{session_id?, text}` | `next_turn` result (+ new session_id) |
| POST | `/api/profile/confirm` `{session_id}` | `PreferenceProfile` |
| POST | `/api/search` `{session_id}` | list of `{church, match}` (Stage 1) |
| POST | `/api/medium` `{session_id, church_ids[1..5]}` | list of cards (Stage 2) |
| POST | `/api/deep` `{session_id, church_ids[1..3]}` | `{job_ids}` (Stage 3 starts in background) |
| GET | `/api/jobs/{job_id}` | `{status, progress, last_steps[5], report_url?}` |
| GET | `/reports/{job_id}` | Church Report HTML |
| GET | `/api/log/{session_id}` | session log (JSONL) for auditing |
| GET | `/healthz` | ok |

## 6. SQLite tables (`app/db.py`)

`sessions(id, created_at, profile_json)`; `churches(church_id, website, last_checked)` — `website` stored only after the site itself was fetched; no other Google fields (Church objects are rebuilt from Places each session);
`evidence(church_id, feature, value, tier, quote, url, source_kind, how, checked_at, note)`; `jobs(job_id, session_id, church_id, status, progress, started_at, finished_at, report_path)`;
`cache(key, fetched_at, body)` for web pages (non-Google).

## 6b. Dependencies (`requirements.txt`)
fastapi, uvicorn, jinja2, python-multipart, httpx, pydantic, python-dotenv, PyYAML, rapidfuzz, feedparser, trafilatura, pypdf, mcp, openai, imageio-ffmpeg (bundled ffmpeg for audio chunking), pytest.

## 7. MCP server (`app/denom/mcp_server.py`)

Tools (thin wrappers of `DenomKB`): `find_denomination(text)`, `get_denomination(id)`, `compare_denominations(a, b, features)`, `match_profile(profile_json)`, `denomination_prior(id, feature)`.
Run: `python -m app.denom.mcp_server` (stdio). Optional HTTP/SSE transport for the "open endpoint" bonus.
