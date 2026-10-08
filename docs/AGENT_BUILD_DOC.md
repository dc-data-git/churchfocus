# ChurchFocus — Agent Build Doc (Track 1)

_Owner: Carter. Fill remaining blanks from live logs before finalist submission._

## 1. User and burden
- User: people looking for a church for themselves or others; focus: helpers (campus pastors, nonprofit workers) who place many people.
- Burden: _[HUMAN: validated by NAME, ROLE — "quote" — see H5]_. Today: hours of website reading per church; statements of faith don't show practice.

## 2. Architecture
Stage 0 conversation → Stage 1 light search → Stage 2 medium → Stage 3 deep agent (see `docs/ARCHITECTURE.md`). Denomination KB behind MCP. Shared feature vocabulary `contracts/features.yaml` (71 features). Matching is deterministic (`app/match.py`); models only extract evidence and phrase questions (D20).

## 3. Prompts (versioned; history in `docs/PROMPT_HISTORY.md`)
App prompts live in `app/prompts/<name>.vN.md` (never edit a released prompt in place):
- `deep_search.v1` — guidebook + tools + stopping rule (not a fixed pipeline)
- `interviewer.v1`, `readback.v1`, `crisis_check.v1` — Stage 0
- `denom_classify.v1`, `page_extract.v1`, `sermon_analyse.v1`, `report.v1`, `prior_map.v1`

Denom-kb (local batch):
- verify.v1 → v2: v1 accepted true statements under the wrong field (~20% wrong among "supported"); v2 requires the value to answer the field (`denom-kb/PROMPT_HISTORY.md`).
- extract.v1 → v2: example values leaked into outputs; removed.

## 4. Stack and cost
Python 3.11+ / FastAPI / Jinja2+HTMX / SQLite; OpenAI models from `.env` on stage; local Ollama for denomination KB build. Every model call logs tokens/cost to `data/logs/calls.jsonl`. Cost per church: _fill from logs after a live deep dive_.

## 5. Tools and permissions
Read-only public web (robots.txt, ≥1s/domain, URL blocklist), Places API (New), Wayback CDX, web search, transcription (ffmpeg chunking >24MB). No writes, no contact with churches, no logins. Agent tools: `fetch_page`, `search_web`, `wayback_snapshots`, `denomination_lookup`, `denomination_locator_search`, `find_sermon_feeds`, `get_sermons`, `transcribe_sermon`, `analyse_sermons`, `record_evidence`, `escalate`, `finish`. Quote re-check (rapidfuzz ≥ 90) rejects non-verbatim evidence.

## 6. Evaluation + session log
Targets in `docs/EVAL_PLAN.md`. Offline seed run (`python -m eval.run` → `eval/results.md`): denomination resolve top-1 **77%** on 30 seed rows (kb_small); **100%** when confidence ≥ 0.8; pytest CI bar **114 passed**. Expand labels via H3 + full KB for submission. Denom-kb audit: 35% (extract) → 67% (+verify.v1) → v2 pending H1/H2. Attach one full session log from `/api/log/{session}` after a live deep dive.

## 7. Guardrails
PRD §5 G1–G6; cut list (D8); marriage rule (D11); sensitive-field human gate (G6); escalation cards for crisis (988), dealbreaker conflict, low denomination confidence, budget exhausted.

## 8. Reproduction
```
git clone <repo>
cd church-discorvery-hackathon
python -m venv .venv
# Windows: .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Unix:    source .venv/bin/activate && pip install -r requirements.txt
copy .env.example .env   # or cp
# fill keys + model ids
run.bat   # or ./run.sh
# open http://localhost:8000
```
Denomination KB rebuild: `denom-kb/README.md`. MCP: `python -m app.denom.mcp_server`.

## Bonuses claimed
MCP server (denomination KB); published eval set (`eval/` when H3/T8.1 land); cost metrics (`calls.jsonl`); reusable components (denom-kb, christianese-lexicon, features.yaml).

## Evening rebuild implementation
The default UI is a single conversation with Churches, About you and Open questions panels. Conversation uses `interview_skill.v1`; validated memory operations form an append-only SQLite log. Mapped christianese hints assist extraction. Location starts background search; church ranking updates from current memory. Website research runs in five workers with progress and cancellation; deep research has two workers. Factual Q&A requires a verbatim quote at the returned source URL. Open questions become deep-research targets and appear in the final report. Earlier v1 pages remain at `/v1`. Live evaluation figures and burden validation above still require team evidence; offline tests do not establish live API quality.

### Verified rebuild state
Final suite: 167 passed; desktop/mobile browser flow and real conversation/Places/website/capped deep-report checks passed. Interview prompt is now `interview_skill.v3` with a complete MemoryOp response schema. Verification methodology and explicit MVP limits are recorded in `docs/REBUILD_VERIFICATION.md`. These capped checks do not replace long-form sermon evaluation or human burden validation.
