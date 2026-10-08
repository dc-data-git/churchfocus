# ChurchFocus — Agent Build Doc (Track 1, Agents of Flourishing)

_Owner: Carter. Bracketed items need team evidence before the finalist submission._

## 1. User and burden
- **User:** people looking for a church for themselves or for someone else. The focus is helpers, such as campus pastors and nonprofit workers, who place many people and can't read every church's website, statement of faith and sermons for each one.
- **Burden:** _[HUMAN: validated by NAME, ROLE — "quote" — task H5]_. Today this means hours of website reading per church. Statements of faith don't show practice (who preaches, what sermons emphasize), and "non-denominational" hides affiliation.
- **What the agent changes:** a helper describes the person in plain words, gets a ranked nearby list in about a minute, factual summaries of 1–5 churches in a few minutes, and a sourced Church Report they can hand on.

## 2. Architecture (details: `docs/ARCHITECTURE.md`)
- **Conversation (Stage 0).** A strong model follows the `interview_skill` prompt and proposes memory operations. The server validates them against a shared vocabulary of 71 church features (`contracts/features.yaml`) and stores them in an append-only log. The person sees and edits the result in **About you**. The model talks naturally; the back end is formulaic (D27).
- **Discovery (Stage 1).** Google Places with an OpenStreetMap fallback, then cheap denomination matching against a 217-group knowledge base. Churches are ranked by a deterministic matcher (`app/match.py`) into fit labels. Models never score (D20).
- **Website research (Stage 2).** Only after the person asks: Learn more → Research selected (1–5), or a pin. The scan is bounded and recursive; every fact, staff entry and evidence item needs a verbatim quote, and full page text is saved for Q&A.
- **Q&A.** Answers come only from saved sources, with quotes re-checked at the cited URL. Otherwise the question goes to the **Open questions** tab.
- **Deep research (Stage 3).** A tool-using agent works from a guidebook prompt with no fixed pipeline. It covers seven minimum areas, sermons are central, and it works on the person's open questions. It produces a shareable **Church Report**.
- **Denomination KB.** Served through an MCP server (`python -m app.denom.mcp_server`): find, get, compare, match_profile, prior.

## 3. Prompts (versioned; history and reasons in `docs/PROMPT_HISTORY.md`)
App prompts live in `app/prompts/<name>.vN.md`. Released versions are never edited, so the history shows exactly what changed and why.

| Prompt | Active | How it evolved (evidence in PROMPT_HISTORY) |
|---|---|---|
| `interview_skill` | v8 | v1 replaced the scripted `interviewer.v1` after testers said it felt like a form. v2–v3 fixed real-model schema slips (`add` vs `assert`). v4 explicit research authorization. v5 name. v6 additive and independent preferences (Madison test). v7 head-pastor negation. v8 saves explicit Protestant scope. |
| `deep_search` | v5 | v1 guidebook. v2 sermon-id workflow and A/B/C-only recording. v3 broad seven-area coverage instead of stopping at preferences. v4 YouTube captions. v5 recovery of broken channel links with identity verification. |
| `medium_extract` | v1 | Replaced `page_extract.v2`: broad factual baseline and full public staff, independent of preferences. |
| `sermon_analyse` | v3 | v2 added speaker gender from introductions (R7). v3 forbids name- or photo-based gender inference and covers all feature categories. |
| `crisis_check` | v2 | v1 escalated grief and past hurt; v2 escalates only self-harm, danger, current abuse or explicit crisis requests (R11). |
| `denom_classify`, `report`, `prior_map` | v1 | |

The denomination KB build ran on local models: `verify.v1` → `v2` (v1 accepted true statements under the wrong field) and `extract.v1` → `v2` (example values leaked into outputs). See `denom-kb/PROMPT_HISTORY.md`.

## 4. Stack and cost
Python 3.11 / FastAPI / Jinja2 / SQLite / plain JS. OpenAI on stage: fast = `gpt-5.6-luna`, strong = `gpt-5.6-terra`, transcription = `gpt-transcribe`. Local Ollama models built the denomination KB and the lexicon. Every model call logs tokens and estimated cost to `data/logs/calls.jsonl`. With data sharing on, OpenAI gives complimentary daily tokens for the luna/terra group; transcription (about $0.0045 per minute) is not covered, and captions are free.
Measured cost per church: _[fill from calls.jsonl after a live deep dive: $/medium church, $/deep dive, minutes/deep dive]_.

## 5. Tools and permissions
- **Read-only public web (`app/web.py`):** GET only, public hosts only (also checked on redirects), robots.txt respected, at least 1 s per domain, URL blocklist (prayer, directories, login, giving, child check-in), PII scrubbing. No form posts, no logins, no contact with churches.
- **External APIs:** Places API (New), the OpenAI web search tool, Wayback CDX, YouTube public metadata and captions, OpenAI transcription.
- **Agent tools:** `fetch_page`, `read_source`, `search_web`, `wayback_snapshots`, `denomination_lookup`, `find_sermon_feeds`, `get_sermons`, `transcribe_sermons`, `analyse_sermons`, `record_evidence`, `review_coverage`, `escalate`, `finish`.
- **Self-correction:** `record_evidence` re-checks each quote word for word against the fetched text at the exact URL. A failure goes back to the agent as an error, logged as a correction; the agent re-fetches or leaves the feature open. "Supported" coverage requires fetched source URLs.
- **Budgets:** 120 minutes, 150 tool calls and 25 sermons per church. Cancellation is checked between tools and before every write.

## 6. Human in the loop and escalation
- The person authorizes all research (Learn more / Research selected / pin / Deep dive), sees and corrects what the app believes about them (About you), and owns the Open questions list.
- Escalations: crisis language → 988 and trusted-person message. A dealbreaker conflict, low denomination confidence or exhausted budget → explicit message plus questions to ask the church. The app never acts on the person's behalf.
- Humans review sensitive denomination fields before they are used as priors (G6, task H1).

## 7. Evaluation and session log (`docs/EVAL_PLAN.md`)
- **CI bar:** 251 offline tests passing. They cover guardrails, the job lifecycle and race conditions, cancellation, generation fencing, quote verification, Q&A source integrity, sermon retrieval, and the memory semantics regressions.
- **Browser checks:** desktop (1440×900) and phone (390×844) flows with real backend APIs and fixtures, with no JavaScript errors or HTTP 500s (`docs/REPAIR_VERIFICATION.md`).
- **Live checks:**
  - real Places discovery (Hesston, KS);
  - website research recovering service times and 7 staff members from a real site;
  - sourced Q&A;
  - a real model conversation preserving preferences;
  - a capped real deep dive with a saved report;
  - a YouTube caption retrieval run (8 captions, 5 analysed).
- **Denomination resolution (seed set, n = 30):** top-1 77%, 100% when confidence ≥ 0.8.
- **Denomination KB field audit:** 35% (extract) → 67% (+verify.v1) → 77% correct / 10% partial / 13% wrong (+verify.v2); improvement plan in `eval/denomkb_audit.md`.
- **Not yet measured live:** a full 25-sermon audio deep dive, and cost per church.
- **Session log:** `/api/log/{session}` returns every step: tool, stated reason, input, result summary, features moved and cost. _[Attach one full session log from a live deep dive.]_

## 8. Guardrails
PRD §5 G1–G6; cut list (D8); marriage rule (D11); no belief topics raised unprompted (D33); non-Nicene groups excluded (D29); sensitive-field human gate (G6); never infer gender; truthful actions (the job exists before it is acknowledged); explicit coverage gaps instead of claimed completeness.

## 9. How it was built (human + AI workflow)
1. **Planning:** with Claude, as living docs: PRD, ARCHITECTURE, INTERFACES, CONVENTIONS and BUILD_PLAN, plus a persona red-team that produced features.v3.
2. **First build (T0–T8):** in parallel Cursor sessions, one owner per file, tracked in `tasks.json` through `scripts/tasks.py`.
3. **Code review (R1–R12):** found 12 problems, all fixed with regression tests.
4. **Live team testing:** produced issues U1–U47 and decisions D24–D53.
5. **Evening rebuild (W0–W7):** turned the app into a chat with a memory log, background jobs and Q&A. Codex finished integration and verification.
6. **Six-role design review (X0–X5):** senior developer, project lead, UI, frontend, backend and testing. It produced the repair of authorization, cancellation and research scope.
7. **Targeted fixes (C1–C8):** each came from a specific live test session.

The record is in `docs/SESSION_LOG.md`, `docs/ISSUES.md` and `docs/REPAIR_VERIFICATION.md`.

## 10. Reproduction
```
git clone <repo> && cd church-discorvery-hackathon
python -m venv .venv
# Windows: .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Unix:    source .venv/bin/activate && pip install -r requirements.txt
copy .env.example .env      # or cp; fill keys + model ids
python -m pytest -q          # 251 passed, offline
run.bat                      # or ./run.sh → http://localhost:8000
```
Denomination KB rebuild: `denom-kb/README.md`. Lexicon: `christianese-lexicon/README.md`. MCP: `python -m app.denom.mcp_server`.

## Bonuses claimed
MCP server (denomination KB). Published eval sets (`eval/*.jsonl`). Cost metrics (`data/logs/calls.jsonl`). Reusable components: denom-kb, christianese-lexicon, features.yaml.
