# ChurchFocus — CONVENTIONS (inline in every coding-agent task)

**Read first:** `docs/INTERFACES.md` (names and signatures; don't invent new ones without adding them there), `contracts/features.yaml` (the only feature vocabulary), and `docs/REPAIR_PLAN.md` for research lifecycle rules.

## Stack
Python 3.11+, FastAPI, Jinja2, SQLite (stdlib `sqlite3`), httpx, pydantic v2, PyYAML, rapidfuzz, feedparser, trafilatura, pypdf, yt-dlp, youtube-transcript-api, pytest. The v2 UI is plain JavaScript and CSS with no build step; v1 pages use HTMX. Windows and Linux must both work: use `pathlib` for paths and no shell-specific scripts in Python.

## Hard rules
1. **Containment:**
   - only `app/config.py` reads env vars;
   - only `app/llm.py` calls models;
   - only `app/stage1/places.py` calls Google;
   - only `app/web.py` fetches web pages;
   - only `app/db.py` runs SQL;
   - only `app/match.py` scores;
   - only `app/memory.py` turns the memory log into a profile;
   - only `app/jobs.py` starts, cancels or publishes research jobs.
2. Feature ids and values come from `contracts/features.yaml` through `app/features.py`. An unknown id is an error, not a new feature.
3. Every church claim is an `Evidence` (or a medium fact or staff row) with a source URL, a verbatim quote and a date. No source means it isn't stored or shown.
4. Prompts live in `app/prompts/<name>.vN.md`. To change a prompt, add a new version file, switch the `load_prompt` call, and add an entry in `docs/PROMPT_HISTORY.md` saying what was wrong and what changed. Never edit a released prompt in place.
5. Guardrails G1–G6 in `docs/PRD.md` §5 are requirements. Never fetch prayer, directory, login, giving-form or child check-in pages. Never store congregant, donor, minor or pay data. Never infer gender.
6. Every model call and every agent step is logged under `data/logs/`. Never log secrets.
7. No new dependency without adding it to `requirements.txt` in the same change.
8. No network in tests. Use the fakes in `tests/fakes.py`, recorded fixtures in `tests/fixtures/`, and a temporary `DATA_DIR`. Tests must never write to the real `data/`.
9. Background work must be cancellable and generation-safe. Check `jobs.is_cancelled()` between steps and before every write. Never acknowledge an action in chat before it exists.
10. Keep modules small, with type hints on public functions and no premature abstraction.

## Definition of done
- The tests for the change pass and the full suite stays green: `python -m pytest -q` (251 tests at last count, all offline).
- Names match INTERFACES.md. If you added a name, INTERFACES.md was updated in the same change.
- A user-visible change was checked in the browser at desktop and phone widths.
- Real-provider checks, when needed, are small and bounded and recorded in the task note.
- If you could not do part of the task, put **blocked: <specific reason>** at the top of your reply instead of improvising.

## Build state (tasks.json)
`tasks.json` is the single record of build state. Change it **only** with `python scripts/tasks.py`:
- `set <ID> in_progress | done "<result>" | blocked "<reason>"`;
- `register <spec.json>` to add tasks;
- `annotate <ID> <meta.json>` to record files, acceptance, verification and next action.

Running `python scripts/tasks.py` shows the board. Commit `tasks.json` together with the work it describes.

## Live server
The team may be using the app on port 8000. Before restarting it, check that no research jobs are pending or running (jobs that are running are marked as errors on startup). Back up `data/app.db` before data migrations. Say when you restart.

## Git
Small commits with the message `<area>: <what>`. Pull before you push. Never commit `.env`, `data/` or large media. Don't commit, push or tag unless the person you work for asks.
