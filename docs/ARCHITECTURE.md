# Church Search — ARCHITECTURE

## 1. Stack (D16)
Python 3.11+, FastAPI, Jinja2 + HTMX (no JS build step), SQLite, httpx, pydantic v2, PyYAML, rapidfuzz, feedparser, trafilatura (HTML→text), pypdf, `mcp` (Python SDK) for the denomination server. Background jobs: a thread pool inside the app (`concurrent.futures`), job state in SQLite. No Docker required.

## 2. Data flow

```
 user ──chat──▶ Stage 0 Interviewer ──PreferenceProfile──▶ Stage 1 light_search
                     │  uses features.yaml + DenomKB.match_profile          │
                     ▼                                                       ▼
              read-back checkpoint                      Places Text Search (→ OSM fallback)
                                                        denomination.resolve() per church
                                                        match.score()  → ranked list + why
                                                                             │ user picks 1–5
                                                                             ▼
                                                        Stage 2 medium_search (website pages → Evidence)
                                                                             │ user picks 1–3
                                                                             ▼
                                                        Stage 3 deep_search (agent loop, background)
                                                        tools → Evidence → stopping rule → ChurchReport
 everything ─────────────────────────────▶ data/logs (calls.jsonl, <session>/<church>.jsonl)
```

## 3. Models and routing (D3, D17)
`app/llm.py` is the only model client. Backend per `LLM_BACKEND`; tasks request a tier:

| Task | Tier | Notes |
|---|---|---|
| interviewer turn, read-back | strong | short context; JSON output `{say, options, updates[]}` |
| jargon → feature mapping | fast | constrained to feature ids/values |
| denomination website classification | fast | must return quote; verbatim-checked |
| Stage 2 page extraction | fast | per page, features with stage ≤ 2 |
| deep-search agent turns | strong | tool calling |
| sermon analysis | fast per sermon, strong for synthesis | |
| report writing | strong | only rephrases Evidence; never adds claims |

All JSON calls use schema-constrained output + one repair retry; failures logged, never silently swallowed. Every call logs tokens and cost (for the cost-metrics bonus). Local models (Ollama) are supported by the same interface; batch tools (denom-kb, lexicon) already run locally.

Web search for the agent: `llm.web_search` uses the OpenAI web search tool when `LLM_BACKEND=openai`. If unavailable, the agent falls back to known-URL patterns (locators, `site/sermons`, `/feed`, Wayback) — the run must still complete.

## 4. Stage 0 — interviewer
State machine over the question bank from `features.yaml`:
1. `core` questions in order: location/distance → "for whom" → branch/tradition history → worship style → women ladder.
2. `standard` questions, skipping any already answered implicitly; user can say "skip the rest".
3. `if_raised` features are only added when the model's mapping of a user utterance hits them.
4. `advanced` theology only if the user says yes to "Do you want to get specific about theology?"
5. Weights: after each answer the interviewer asks or infers weight ("Is that a must-have?") — must ask for anything it would mark dealbreaker.
6. `DenomKB.match_profile` proposes likely denominations; read-back shows profile + likely traditions; user confirms/edits.
Crisis/pastoral detection runs on every user turn (fast tier classifier + keyword list) → `Escalation(pastoral_or_crisis)`.

## 5. Stage 1 — light search
**Places API (New) Text Search** `POST https://places.googleapis.com/v1/places:searchText`
- body: `{"textQuery": "church", "includedType": "church", "strictTypeFiltering": true, "locationBias": {"circle": {"center": {...}, "radius": <= 50000}}, "pageSize": 20}`; follow `nextPageToken` up to 60 results. (`locationRestriction` only accepts a rectangle — do not pass a circle.)
- field mask: `places.id,places.displayName,places.formattedAddress,places.location,places.websiteUri,places.nationalPhoneNumber,places.regularOpeningHours,places.businessStatus,places.types,places.googleMapsUri,nextPageToken`. `websiteUri`/hours/phone are billed at a higher SKU — acceptable at demo volume. Reviews/ratings are a further SKU: off by default.
- also run a second query `textQuery: "<tradition> church"` for the top likely tradition, merge by id.
- filter: `businessStatus == OPERATIONAL`; drop > `max_miles`.
- Display "Google Maps" attribution; store only `place_id` long-term.
- Fallback: Overpass `amenity=place_of_worship` + `religion=christian` within radius (OSM `denomination=*` tag is a free tier-C hint).

**Denomination resolution** (`denomination.resolve`, D5, R1.3), cheapest first, stop when confidence ≥ 0.8:
1. Name/alias match: tokens in name vs KB names/abbreviations/aliases + a small hand list (e.g. "UMC", "First Baptist" → Baptist family only, not SBC). `method=name`, confidence 0.6–0.9 depending on specificity.
2. OSM `denomination` tag if present: 0.6.
3. Locator/network cross-search: `site:` web search on the locator domains in `contracts/source_registry.md` with church name + city; a hit on an official directory = 0.95 `method=locator`.
4. Website: fetch home + about/beliefs; fast model classifies with a verbatim quote ("member of", "affiliated with", footer logos' alt text). 0.7–0.9 `method=website`.
5. Non-denom bucket: explicit "non-denominational"/"independent" → label "Non-denominational", 0.8; unknown → "Unknown (likely independent)", ≤ 0.4. "Secret denomination" signals (network names, seminary of pastor, confession named) raise a candidate denomination with its confidence rather than a verdict.
Steps 3–4 run only for the top 20 by distance/prior score to bound latency (< 60 s total, parallelised).

## 6. Matcher (D20) — `match.score`
For each preference p with weight w (dealbreaker = 5, important = 3, nice_to_have = 1, dont_care = 0):
- Best evidence e for p.feature: highest tier among church evidence (A > B > C > D); else denomination prior.
- Strength s: A 1.0, B 0.8, C 0.5, D 0.6 (observed ≥ 5 data points) or 0.4, prior 0.5 × denomination confidence × (1 − variability).
- Variability: KB `distinguishing_metadata.<x>_variability` text → number (contains "low" 0.15, "moderate"/"medium" 0.3, "high" 0.5, else 0.3), where <x> = doctrinal for `theology.*`, `social.*`, `lgbtq.*`; worship for `worship.*`, `preaching.*`; governance for `polity.*`, `women.*`; cultural for everything else.
- m = +1 if e.value ∈ p.want, −1 if not, 0 if unknown/unstated. A **dealbreaker with m = 0** contributes −0.1 × w (small penalty, like weak contrary evidence), is listed first under `unknown`, and generates a question for the visit.
- contribution = w × s × m. Score = 50 + 50 × Σcontrib / Σ(w) clipped to 0–100.
- Distance: soft penalty beyond 60% of `max_miles`.
- **Excluded** iff a dealbreaker has m = −1 with tier A/B or observed D ≥ 0.6 strength (priors never exclude — they only lower).
- `why`: top two positive and top two negative/unknown contributions in plain language, naming the tier ("their beliefs page says…", "typical for the ELCA").

## 7. Stage 2 — medium search
`website.site_pages` picks up to 8 pages by link text/URL keywords (about, beliefs, what-we-believe, staff, leadership, ministries, events, calendar, sermons, watch, media, give). Each page → fast-tier extraction of `stage ≤ 2` features in one JSON call with verbatim quotes (verbatim check via rapidfuzz partial_ratio ≥ 90, as in denom-kb). Card + `deep_dive_candidate` = strong if a sermon feed/media page was found AND ≥ 1 important feature is still open; weak if no sermons and no beliefs page.

## 8. Stage 3 — deep search agent
- Loop: `chat_tools` with the guidebook prompt (`deep_search.v1`), the profile's open important/dealbreaker features, current evidence, budget remaining, and the tool list (INTERFACES §4).
- Each turn the model must emit a short `why` with the tool call; the runner logs a `StepLog`.
- After each `record_evidence`, the runner recomputes settled/open (features.yaml `settled_rule`) and injects it into the next turn.
- Stop when: all dealbreaker+important settled/not-found, or `finish` called, or budget hit (`DEEP_MAX_MINUTES`, `DEEP_MAX_TOOL_CALLS`) → budget escalation if dealbreakers open.
- Sermons (cap `DEEP_MAX_SERMONS` = 25, D22): `find_sermon_feeds` (site links, `/feed`, podcast links, YouTube channel link, SermonAudio) → `get_sermons` newest first → `transcribe_sermon` (skip if a transcript exists) → `analyse_sermons` per sermon (topics, scripture density, audience, politics mentions, speaker) → aggregate to observed Evidence (tier D, `how=observed`, note with counts). Throughput (minutes of audio per minute of wall time, cost) is logged for D7/D9.
- Wayback: CDX API `http://web.archive.org/cdx/search/cdx?url=<site>/*&from=YYYY&to=YYYY&output=json&filter=statuscode:200&collapse=timestamp:6` on staff/beliefs pages → leadership tenure, statement changes.
- Report: `report.py` renders HTML from `ChurchReport` (no new claims at render time). The report is the finished artifact.

- Self-correction in the runner: every `record_evidence` quote is re-checked against the fetched text (rapidfuzz partial_ratio ≥ 90); a failure is returned to the agent as an error with the reason, logged as `action=correction`, and the agent must re-fetch or downgrade to tier D. Count corrections per run (EVAL).
- Transcription: audio > 24 MB is converted with the bundled ffmpeg (imageio-ffmpeg) to mono 32 kbps and split into ≤ 20-minute chunks before `llm.transcribe`.
- Report sharing: `/reports/{job_id}` is a stand-alone HTML page (print stylesheet → "Save as PDF"), so a helper can send it to the person they are helping.

## 9. Cache and reuse (R4)
`churches` + `evidence` rows persist (our own derived evidence, not Google content). On reuse within `CACHE_MAX_AGE_DAYS`: refetch home page (HEAD/GET) and the beliefs page; if the beliefs text hash changed or site unreachable → mark A-tier evidence stale and re-run Stage 2 extraction. Older than the window → re-research.

## 10. Security and guardrails
- Fetcher (`web.py`): robots.txt, ≥ 1 s per domain, 20k-char cap per page, no login pages, no form posts, GET only.
- PII filter before storing any page text: drop email addresses/phone numbers that are not the church's main contact; never store prayer-request, member-directory or giving pages (URL/keyword blocklist: prayer, directory, members, login, give/donate forms, child check-in).
- Prompts carry the guardrails verbatim (G1–G6). The report renderer refuses features outside features.yaml.
- Secrets only from `.env`; logs scrub keys.

## 11. Deployment
Tonight: `run.bat` / `run.sh` → `uvicorn app.main:app` on localhost; `.env` from `.env.example`. Tomorrow (if time): Render/Railway web service with a persistent disk for `data/`. MCP server runs separately (`python -m app.denom.mcp_server`).

## 12. Cost model (to fill with measured numbers)
Per search: Places ≤ 3 requests; denomination resolution ≤ 20 fast calls; Stage 2 ≈ 8 fast calls/church; Stage 3 ≈ 40–150 strong turns + up to 25 sermon transcriptions (transcribe up to 3 in parallel). Report measured $/church and minutes/church in EVAL results.
