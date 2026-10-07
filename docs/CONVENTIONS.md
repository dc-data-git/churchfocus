# Church Search — CONVENTIONS (inline in every Cursor task)

**Read first:** `docs/INTERFACES.md` (names/signatures — do not invent new ones), `contracts/features.yaml` (the only feature vocabulary).

## Stack
Python 3.11+, FastAPI, Jinja2 + HTMX, SQLite (stdlib `sqlite3`), httpx, pydantic v2, PyYAML, rapidfuzz, feedparser, trafilatura, pytest. Windows and Linux must both work (paths via `pathlib`, no shell-specific scripts in Python).

## Hard rules
1. Containment: only `app/config.py` reads env vars; only `app/llm.py` calls models; only `app/stage1/places.py` calls Google; only `app/web.py` fetches web pages; only `app/db.py` runs SQL; only `app/match.py` scores.
2. Feature ids and values come from `contracts/features.yaml` via `app/features.py`. Unknown ids are an error, not a new feature.
3. Every church claim is an `Evidence` with tier, source, date. No tier → not stored, not shown.
4. Prompts live in `app/prompts/<name>.vN.md`. Changing a prompt = new version file + entry in `docs/PROMPT_HISTORY.md` (old text, what was wrong). Never edit a released prompt in place.
5. Guardrails G1–G6 in `docs/PRD.md` §5 are requirements. Never fetch prayer/directory/login/giving-form/child check-in pages; never store congregant, donor, minor or pay data.
6. Every model call and every agent step is logged (`data/logs/`). Never log secrets.
7. No new dependencies without adding them to `requirements.txt` in the same change.
8. Network calls in tests are forbidden: use fakes (`tests/fakes.py`) and recorded fixtures (`tests/fixtures/`).
9. Keep modules small; no premature abstraction; type hints on public functions.

## Definition of done (per task)
- The task's listed tests pass: `pytest -q tests/<file>` and the full suite stays green (`pytest -q`).
- Names match INTERFACES.md exactly.
- If you could not do part of the task, say **blocked: <specific reason>** at the top of your reply instead of improvising.

## Build state (tasks.json)
`tasks.json` (repo root) is the single record of build state. Change it **only** with `python scripts/tasks.py`:
`set <ID> in_progress` when you start, `set <ID> done "<short result, e.g. 9 tests green>"` when the checks pass, `set <ID> blocked "<specific reason>"` when stuck. `python scripts/tasks.py` shows the board; `python scripts/tasks.py next <name>` shows what you can start. Commit tasks.json together with the work it describes.

## Git
Small commits, one module per commit, message `<area>: <what>`. Pull before you push. Do not commit `.env`, `data/`, or large media.
