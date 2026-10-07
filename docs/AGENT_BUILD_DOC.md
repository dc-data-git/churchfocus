# Church Search — Agent Build Doc (Track 1)

_Fill from logs and PROMPT_HISTORY. Owner: Carter._

## 1. User and burden
- User: people looking for a church for themselves or others; focus: helpers (campus pastors, nonprofit workers) who place many people.
- Burden: _[validated by: NAME, ROLE — "quote"]_. Today: hours of website reading per church; statements of faith don't show practice.

## 2. Architecture
Stage 0 conversation → Stage 1 light search → Stage 2 medium → Stage 3 deep agent (see docs/ARCHITECTURE.md diagram). Denomination KB behind MCP. Shared feature vocabulary `contracts/features.yaml`.

## 3. Prompts (verbatim, with an earlier version and what was wrong)
- deep_search.v1 (app/prompts) — paste.
- denom-kb verify.v1 → v2: v1 accepted true statements filed under the wrong field (audit: 20% wrong among "supported"); v2 requires the value to answer the field. Paste both from denom-kb/PROMPT_HISTORY.md.
- denom-kb extract.v1 → v2: example values leaked into outputs; removed.

## 4. Stack and cost
Python/FastAPI/SQLite; OpenAI (models: _fill_) on stage; local Ollama (qwen2.5:7b, gpt-oss:120b-cloud verifier) for the denomination KB build. Cost per church: _fill from logs_.

## 5. Tools and permissions
Read-only public web (robots.txt respected), Places API, Wayback CDX, web search, transcription. No writes, no contact with churches, no logins.

## 6. Evaluation + session log
Results from docs/EVAL_PLAN.md: _fill_. Denomination KB: 35% → 67% → _v2_. Attach one full session log.

## 7. Guardrails
PRD §5 G1–G6; cut list (D8); marriage rule (D11); sensitive-field human gate; escalation cards.

## 8. Reproduction
`git clone …`; `python -m venv .venv`; `pip install -r requirements.txt`; copy `.env.example` → `.env`; `run.bat` / `run.sh`; open http://localhost:8000. Denomination KB rebuild: `denom-kb/README.md`.

## Bonuses claimed
MCP server (denomination KB); published eval set (`eval/`); cost metrics; reusable components (denom-kb, christianese-lexicon, features.yaml).
