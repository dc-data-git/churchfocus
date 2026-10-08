# ChurchFocus

On-demand church research for people finding a church for themselves or someone else (Gloo AI Hackathon 2026, Track 1).

Conversation → light Places search → website cards → deep agent report with tiered evidence and stated-vs-observed practice. No church database; denomination knowledge sits behind an MCP tool interface.

## Quick start

```bat
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Fill `GOOGLE_PLACES_API_KEY`, `OPENAI_API_KEY`, and model ids in `.env` (see `docs/BUILD_PLAN.md`).

```bat
run.bat
```

Or: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000`

Open http://localhost:8000 — health check at `/healthz`.

Linux/macOS: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env && ./run.sh`

## Tests

```bat
.\.venv\Scripts\python.exe -m pytest -q
```

No network in tests (fakes + fixtures).

## Layout

| Path | Role |
|---|---|
| `app/` | FastAPI app, stages 0–3, matcher, web/llm/db |
| `contracts/features.yaml` | Shared feature vocabulary |
| `app/denom/` | Denomination KB + MCP server |
| `denom-kb/` | Offline KB build pipeline |
| `docs/` | PRD, architecture, interfaces, build plan |
| `tasks.json` | Build board (`python scripts/tasks.py`) |

Denomination MCP (stdio): `python -m app.denom.mcp_server`

## Docs

Start with `AGENTS.md`, then `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/INTERFACES.md`.

## License

MIT — see `LICENSE`.
