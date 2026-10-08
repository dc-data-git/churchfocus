# AGENTS.md — for Cursor, Codex, Claude and any coding agent

This repo is **ChurchFocus** (Gloo AI Hackathon 2026, Track 1). The app is built and in user testing. Expect to fix or extend existing code, not scaffold new modules.

Before writing code:
1. Read `docs/CONVENTIONS.md` (rules and definition of done).
2. Use names from `docs/INTERFACES.md` only. If you need a new name, add it there first.
3. Use feature ids from `contracts/features.yaml` only.
4. Track your work in `tasks.json`, changing it **only** with `python scripts/tasks.py`. Register or claim a task and set it `in_progress` at the start. At the end, set it `done "<result + how verified>"` or `blocked "<reason>"`. New issues get new tasks; do not reopen unrelated ones.
5. Guardrails in `docs/PRD.md` §5 are non-negotiable.
6. Run `python -m pytest -q` (all 251+ tests, no network) before saying anything is done.

Where things are decided:
- Current architecture: `docs/ARCHITECTURE.md`. Current contracts: `docs/INTERFACES.md`.
- Research lifecycle, timing and scope rules: `docs/REPAIR_PLAN.md`. It supersedes `docs/REDESIGN.md` where they conflict.
- Prompts: `app/prompts/<name>.vN.md`. Never edit a released prompt. Add a new version, switch the `load_prompt` call, and record why in `docs/PROMPT_HISTORY.md`. The active versions are listed at the top of that file.

Containment (one module per job): settings in `app/config.py`; model calls in `app/llm.py`; Google in `app/stage1/places.py`; web fetching in `app/web.py`; SQL in `app/db.py`; scoring in `app/match.py`; preference memory in `app/memory.py`; job lifecycle in `app/jobs.py`.

Sub-projects (do not modify unless the task says so): `denom-kb/` builds the denomination KB, `christianese-lexicon/` builds the church-vocabulary lexicon.

Live server: the team may be using the app on port 8000. Check for running research jobs before restarting it, and say when you restart.

Do not commit, push, tag, submit to the hackathon or contact anyone unless the person you work for asks.
