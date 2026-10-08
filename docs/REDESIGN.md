# ChurchFocus — Redesign v2 (MVP, 2026-10-07 evening)

Source of truth for the evening rebuild. Decisions D24–D44 and issues U1–U40 are in `docs/ISSUES.md`; tasks are W0–W6 in `tasks.json`.
Rule: **file ownership is strict** (one owner per file) so parallel work merges cleanly. Contracts below are binding; change them here first.

## 1. What the person experiences
1. A standard AI-chat screen (D41). Right panel tabs: **Churches** · **About you** · **Open questions** (phones: tabs above the chat).
2. Bot: "Where are you starting from?" As soon as a place is known, the first search starts in the background (D39) and the Churches tab fills.
3. Bot: "Tell me what matters to you in a church." It reads every message, updates the memory (D27), answers side questions (D35), asks at most a few follow-ups (P7/P8), never raises belief topics unprompted (D33), never asks want/avoid/don't-care.
4. Churches table re-ranks live: fit label (Strong · Possible · Not enough info yet · Unlikely · Poor), sort by fit then distance, radius filter (default 15 mi or the person's limit, max 50), 10 per page (D30, D31, D38).
5. Top 5 fits are read automatically in parallel (Stage 2, D42); progress bars per church; the bot posts "I've read X's website — here's what stands out…". The person can ask factual questions; answers come only from what was read; anything unsure goes to Open questions (P10).
6. "Would you like to know more about any of these churches?" → the person picks churches → running Stage 2 jobs for unpicked churches are cancelled (D42).
7. "Find these out" → deep dive with the church's open questions + the person's strong preferences as targets.

## 2. Memory (D27) — `app/memory.py` (owner: Claude)
Append-only log per session, stored in SQLite table `memory_log` (via db.py).
```python
class MemoryOp(BaseModel):          # one line of the log
    t: int                           # turn number
    op: Literal["assert", "revise", "confirm", "retract"]
    key: str                         # feature id from features.yaml, or "location", or "denomination"
    val: str | list[str] | dict      # value(s); for location {"text","lat","lng","limit_miles"}
    stance: Literal["want", "avoid", "neutral"] = "want"
    strength: float = 0.5            # 0..1 how much it matters
    conf: float = 0.5                # 0..1 how sure we are they meant it
    src: Literal["stated", "inferred", "confirmed", "user_edit", "lexicon"]
    ev: str = ""                     # the person's words (short)
    why: str = ""                    # machine-readable reason, e.g. "lexicon:hymns->worship.style=traditional_hymns"
    supersedes: int | None = None
def append(session_id: str, ops: list[MemoryOp]) -> None
def log(session_id: str) -> list[MemoryOp]
def current(session_id: str) -> dict[str, MemoryOp]          # latest non-retracted op per key
def to_profile(session_id: str) -> PreferenceProfile        # formulaic (below)
def plain_summary(session_id: str) -> list[dict]             # for the About-you panel: {key,label,text,src,conf}
```
**to_profile rules (formulaic):** `want` → Preference(want=[val]); `avoid` → Preference(avoid=[val]); weight from strength: ≥0.8 dealbreaker, ≥0.5 important, >0.15 nice_to_have, else dont_care; conf < 0.5 → one weight step lower; sensitive features (features.yaml `sensitive: true`) with src="inferred" → at most nice_to_have until confirmed.

## 3. Models (owner: Claude)
`Preference` gains `avoid: list[str] = []`, `strength: float = 0.5`, `conf: float = 1.0`. `MatchResult` gains `fit: Literal["strong","possible","unknown","unlikely","poor"]` and `known_share: float`.

## 4. Matching (owner: Claude, `app/match.py`)
- No exclusion for practice/belief preferences at any stage (D28). `excluded` = only when the church's denomination/tradition/branch is in an `avoid`.
- Value in `avoid` → m = −1. Dealbreaker → weight 5 (fit only).
- `fit`: known_share = Σweight of features with evidence or prior ÷ Σweight. If known_share < 0.4 → "unknown". Else score ≥ 70 strong, ≥ 58 possible, ≥ 45 unlikely, else poor.
- Non-Nicene groups (KB `identity.nicene_trinitarian == False`, plus LDS/JW/Christian Science name patterns) are removed everywhere (D29): `DenomKB.is_christian()` returns False for them.

## 5. Search (owner: Daniel, `app/stage1/search.py`)
```python
def ensure_coverage(session_id: str, lat: float, lng: float, radius_mi: float) -> int   # runs Places only if radius > covered (D40); returns new churches added
def table(session_id: str, *, radius_mi: float, sort: Literal["fit","distance","name"], page: int, size: int) -> dict
    # {"rows":[{church_id,name,address,distance_miles,denomination,denom_confidence,fit,score,why[<=3],stage2:"none|queued|running|done",progress}], "total", "page", "pages", "radius_mi", "covered_mi"}
```
Coverage per session in table `coverage` (lat,lng,radius_mi,queries,ts). Results cached per session in memory + `session_churches` table (church_id, candidate_json, denom_json) — Google fields only in memory/session scope, never long-term beyond place_id (R1.5). Non-Nicene removed. Denomination resolve: name/KB for all; website step only for rows actually shown (top page) or Stage-2 runs.

## 6. Jobs (owner: Carter, `app/jobs.py` + `app/stage2/*`)
```python
def submit(session_id: str, church_id: str, kind: Literal["medium","deep"], *, questions: list[str] = ()) -> str   # job_id; reuses fresh saved result (D43)
def cancel(job_id: str) -> None                 # cooperative: workers check is_cancelled() between pages/steps
def is_cancelled(job_id: str) -> bool
def status(session_id: str) -> list[dict]       # [{job_id, church_id, name, kind, status, done, total, label}]
```
Pool: 5 workers for medium, 2 for deep. Progress label e.g. "Reading Bethel's website — 4 of 6 pages". Retention (D43): medium 90 days, deep 365 days; reuse if < 30 days; 30–90/365 days → quick verify (re-fetch home + beliefs page; same text hash → reuse, else re-run). On medium completion: post a bot message via `chat.post(session_id, text, meta)` (see §8).
Stage-2 fixes: U18 (free-form features store the content, not the placeholder), U19, U20 (best evidence per feature), ask each page only for the person's features + identity, ≤5 pages, pages in parallel.

## 7. Q&A + Open questions (owner: Wade, `app/qa.py`)
```python
def answer(session_id: str, church_id: str, question: str) -> dict
    # {"answer": str, "sources": [{url, quote}], "confident": bool}; uses ONLY stored evidence + cached page text; if not confident → adds to open questions
def open_questions(session_id: str, church_id: str | None = None) -> list[dict]   # {id, church_id, text, status: open|answered|dropped, answer?}
def add_question(session_id: str, church_id: str, text: str) -> int
def update_question(qid: int, *, text: str | None = None, status: str | None = None) -> None
```
Deep dive (`stage3/agent.py`) receives the church's open questions as extra targets; report gets a "Your questions" section.

## 8. Conversation (owner: Claude, `app/stage0/conversation.py`, `app/chat.py`, prompts `interview_skill.v1.md`)
One model call per turn (strong), in parallel with the crisis check (fast). Output:
```json
{"reply": "...", "memory_ops": [MemoryOp...], "location": {"text","limit_miles"}|null, "intent": "chat|search_now|church_question|denomination_question|know_more|find_these_out",
 "church_ref": "church_id|null", "show_options": ["..."]|null}
```
Server validates every op against features.yaml (unknown keys/values dropped and logged). Lexicon hints (P11, `app/lexicon.py`) are added to the prompt for terms found in the message.
`app/chat.py`: message store (table `messages`: session_id, n, role, text, meta_json, ts) — `post(session_id, text, meta)`, `since(session_id, n)`.

## 9. HTTP (owner: Claude writes routes in `app/main.py`; others only add functions in their modules)
| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/` | | `app.html` (new single page) |
| POST | `/api/chat` | `{session_id?, text}` | `{session_id, messages:[new bot messages]}` |
| GET | `/api/state/{sid}` | `?since=n` | `{messages:[...after n], jobs:[...], table_version:int, memory_version:int}` (poll every 2 s) |
| GET | `/api/churches/{sid}` | `?radius&sort&page&size` | `search.table(...)` |
| POST | `/api/know_more` | `{session_id, church_ids[1..5]}` | `{jobs}` (cancels other running medium jobs) |
| GET/POST | `/api/memory/{sid}` | POST `{key, text}` user edit | plain_summary |
| GET/POST | `/api/questions/{sid}` | POST `{church_id, text}` / `{id, status|text}` | open_questions |
| POST | `/api/deep` | `{session_id, church_id}` | `{job_id}` |
| GET | `/reports/{job_id}` | | report HTML |
UI (owner: Katie, `app/templates/app.html`, `app/static/app.js`, `app/static/app.css`) talks only to these routes.

## 10. Order and cut line
W0 (contracts, this file) → W1–W5 in parallel → integrate 8:30 pm → submit 9:30 pm. At 8:30, anything not working falls back to the current v1 pages (`/v1`), which stay mounted.

## Integration verification notes
The rebuild supports message polling with a typing indicator; token-by-token streaming remains outside this MVP. Results older than 30 days are re-researched rather than hash-verified. Coverage expands on demand and deduplicates overlapping requests; a Places result cap means geographic coverage is not a guarantee that every church has been found. Free-text questions are passed into the agent state and retained in reports, with verified answers or explicit unknowns. Submission is a separate team action.
