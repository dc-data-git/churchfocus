# AGENTS.md — for Cursor and any coding agent

This repo is **ChurchFocus** (Gloo AI Hackathon 2026, Track 1). Before writing code:
1. Read `docs/CONVENTIONS.md` (rules + definition of done).
2. Use names from `docs/INTERFACES.md` only.
3. Use feature ids from `contracts/features.yaml` only.
4. Find your task in `tasks.json` / `docs/BUILD_PLAN.md`; touch only the files it lists. Mark it with `python scripts/tasks.py set <ID> in_progress` at the start and `done "<result>"` or `blocked "<reason>"` at the end.
5. Guardrails in `docs/PRD.md` §5 are non-negotiable.

Already built and tested (do not regenerate): `app/config.py`, `app/models.py`, `app/features.py`, `app/match.py`, `app/denom/*`, `tests/` (conftest, test_kb, test_match, test_features, fixtures/kb_small.json).

Existing sub-projects (do not modify unless the task says so): `denom-kb/` (builds the denomination KB), `christianese-lexicon/`.
