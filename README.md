# ChurchFocus

ChurchFocus helps people find a church that fits, for themselves or for someone they are helping. You talk with it in plain words. It finds nearby churches, ranks them against what you said, reads church websites when you ask, answers factual questions with quotes from the church's own pages, and can run a deep-research agent that produces a sourced Church Report.

Built for the 2026 Gloo AI Hackathon (Track 1, Agents of Flourishing) by Daniel and three Tabor College students: Katie (theology), Carter (research doc and Stage 3 sources) and Wade (Stage 3 sources).

ChurchFocus has no church database. It researches churches on demand, keeps what it learned with sources and dates, and re-checks saved research before reusing it. Denomination background comes from a knowledge base of 217 US religious groups, served through an MCP tool interface.

## How it works

1. **Conversation.** The person says where they're starting from and, in their own words, what matters to them. The model turns what they say into entries in an append-only memory log. The person can see and correct those entries in the **About you** tab. Every change is kept with its reason.
2. **Nearby churches.** A starting place starts a background search with Google Places, falling back to OpenStreetMap. The **Churches** tab fills as each map query finishes. Each church gets a denomination guess and a fit label: Strong fit, Possible fit, Not enough info yet, Unlikely fit or Poor fit. The list re-ranks as the conversation goes on. A distance filter (1–50 miles, default 15) only queries again when the radius grows past what has already been searched. Groups outside historic Trinitarian Christianity are left out.
3. **Learn more (website research).** Nothing is read until the person asks. **Learn more** opens a picker; the person chooses 1–5 churches and presses **Research selected**, or pins a church. ChurchFocus then reads up to 30 public pages per church. It records service times, the full public staff list, active ministries and stated beliefs, each with a verbatim quote, and shows a short factual summary on the church's row.
4. **Questions.** The person can ask factual questions about a church in the chat. Answers use only saved public pages and sermon text, and every quote is checked against its source. If the answer isn't there, the question goes to the **Open questions** tab.
5. **Deep dive.** A tool-using agent researches one church broadly. It covers identity and governance, the public staff list, services and worship, ministries, stated beliefs, sermons (YouTube captions, podcast transcripts or transcription) and public history. It also works on the person's open questions. It writes a **Church Report** with sources, stated-versus-observed practice, research coverage, what is still unknown and questions to ask on a visit. Progress stays visible above the message box.

Research runs in background jobs that survive a page reload. Jobs are cancelled when they are no longer wanted, such as an unpinned church or a new starting place. Saved website research is reused for 7 days. A deep dive always writes a fresh report for the person, but it reuses saved sources and sermon transcripts and re-checks website pages older than 30 days. Website research is kept for 90 days and deep research for a year.

## Quick start (Windows)

```bat
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env` (see [Configuration](#configuration)), then:

```bat
run.bat
```

Open http://localhost:8000. The health check is at http://localhost:8000/healthz. At startup the app prints any required settings that are missing.

**macOS / Linux:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill it in
./run.sh
```

Python 3.11+ is required. ffmpeg comes bundled through `imageio-ffmpeg`, so you don't need to install it.

### Sharing with your team

The app binds to `127.0.0.1:8000`. To let teammates use it from their own devices without joining your tailnet, use Tailscale Funnel: `tailscale funnel 8000`. This makes the app public to anyone with the link, so turn it off afterwards with `tailscale funnel 8000 off`.

## Configuration

All settings are read from `.env` by `app/config.py`. `.env` is gitignored, so never commit it.

| Variable | Needed for | Notes |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | Finding churches | Places API (New), Text Search |
| `OPENAI_API_KEY` | All model calls | Also transcription and web search |
| `OPENAI_MODEL_FAST` | Extraction, Q&A, crisis check | We use `gpt-5.6-luna` |
| `OPENAI_MODEL_STRONG` | Conversation, deep-research agent | We use `gpt-5.6-terra` |
| `OPENAI_TRANSCRIBE_MODEL` | Sermon audio without captions | We use `gpt-transcribe` |
| `TOOLS_REASONING_EFFORT` | Deep-research agent | Keep `none`: terra rejects function tools with any other effort on Chat Completions |
| `LLM_BACKEND` | | `openai` (default) or `ollama` |
| `OLLAMA_URL`, `OLLAMA_MODEL_FAST`, `OLLAMA_MODEL_STRONG` | Local models | Only when `LLM_BACKEND=ollama` |
| `DEEP_MAX_MINUTES` / `DEEP_MAX_TOOL_CALLS` / `DEEP_MAX_SERMONS` | Deep-dive budget | Defaults 120 / 150 / 25 |
| `DENOM_KB_PATH`, `FEATURES_PATH`, `DATA_DIR` | | Defaults point inside the repo; runtime data goes to `data/` |
| `USER_AGENT` | Web fetcher | Put a contact address in it |

To list the model ids your key can use:

```bat
.\.venv\Scripts\python.exe -c "from openai import OpenAI; from dotenv import load_dotenv; load_dotenv(); print(sorted(m.id for m in OpenAI().models.list()))"
```

Every model call is logged with tokens and estimated cost to `data/logs/calls.jsonl`.

## Tests

```bat
.\.venv\Scripts\python.exe -m pytest -q
```

251 tests run with no network: model, web and Places calls use fakes and recorded fixtures, and each test gets its own temporary data directory.

## Using it

| Where | What you can do |
|---|---|
| Chat | Say where you're starting and what matters. Ask "What time are services at First Pres?", "What's the difference between the ELCA and the LCMS?", "Do a deep dive on Bethel". Paste a church's website to add and pin it. "Start over" offers a new chat or a changed search here. |
| Churches tab | Distance, sort (fit, distance or name) and page size. Pin a church to research it. Use **Learn more**, then **Research selected** for 1–5 churches. Each row has **Ask a question** and **Deep dive** buttons. |
| About you | What ChurchFocus believes about your preferences, and why. Edit or remove any item; edits are logged. |
| Open questions | Questions the saved research couldn't answer, per church. Add, edit or drop them. Deep dives work on them. |
| Reports | `/reports/<job_id>` is a stand-alone Church Report that prints cleanly to PDF. |

The original step-by-step interview flow from the first build is still mounted at `/v1`.

## Guardrails

- Public sources only, read-only. robots.txt is respected, requests are rate-limited, and the app never logs in, submits forms or contacts a church.
- The app never collects data about congregants, donors, children or staff pay. Prayer-request, member-directory, login, giving and child check-in pages are blocked.
- Every church claim carries a source tier and a verbatim quote. Denomination "typical" positions are labeled as priors, never as facts about a congregation.
- Belief topics such as women in leadership and marriage are never raised unless the person brings them up. Sensitive denomination fields need human review before they are used.
- Crisis language gets a 988 message instead of an interview reply.

Full rules: `docs/PRD.md` §5.

## Repository layout

| Path | Contents |
|---|---|
| `app/main.py` | FastAPI routes |
| `app/stage0/conversation.py` | The chat turn: interview skill, memory ops, intents, crisis check |
| `app/memory.py` | Append-only preference log → current profile |
| `app/lexicon.py` | Christianese phrase hints for the interview |
| `app/stage1/` | Places/OSM search, denomination resolution, the churches table |
| `app/match.py` | Deterministic scoring and fit labels (the only place that scores) |
| `app/jobs.py` | Background research jobs: selection, pins, cancellation, reuse |
| `app/stage2/` | Website scanning and factual summaries |
| `app/qa.py` | Source-checked answers and Open questions |
| `app/stage3/` | Deep-research agent, tools, sermons, Church Report |
| `app/denom/` | Denomination KB and its MCP server |
| `app/prompts/` | Versioned prompts (`name.vN.md`; released versions are never edited) |
| `app/templates/`, `app/static/` | Chat UI (`app.html`, `app.js`, `app.css`) and report template |
| `contracts/features.yaml` | The shared vocabulary of 71 church features |
| `denom-kb/` | Offline pipeline that built the denomination KB |
| `christianese-lexicon/` | Offline pipeline that built the church-vocabulary lexicon |
| `eval/` | Evaluation sets, runner and results |
| `tests/` | Offline test suite |
| `docs/` | Product, architecture and interface docs (below) |
| `tasks.json`, `scripts/tasks.py` | Build board. Run `python scripts/tasks.py` to see it |

Denomination MCP server (stdio): `python -m app.denom.mcp_server`. Tools: `find_denomination`, `get_denomination`, `compare_denominations`, `match_profile`, `denomination_prior`.

## Docs

| Doc | Read it for |
|---|---|
| `AGENTS.md` | Rules for coding agents working in this repo |
| `docs/PRD.md` | Requirements, guardrails, decision log |
| `docs/ARCHITECTURE.md` | How each stage works |
| `docs/INTERFACES.md` | Names, signatures, routes and tables (the anti-drift contract) |
| `docs/CONVENTIONS.md` | Coding rules and definition of done |
| `docs/AGENT_BUILD_DOC.md` | Hackathon Agent Build Doc |
| `docs/SUBMISSION.md` | 250-word description |
| `docs/EVAL_PLAN.md`, `eval/results.md` | How we measure, and results so far |
| `docs/PROMPT_HISTORY.md` | Every prompt version and why it changed |
| `docs/ISSUES.md` | Live-testing issues and decisions D24–D44 |
| `docs/REPAIR_PLAN.md`, `docs/REPAIR_VERIFICATION.md` | The October 7 lifecycle and research repair, and its proof |
| `docs/SESSION_LOG.md` | Build history |

Historical records, kept for the build story: `docs/BUILD_PLAN.md` (first Cursor build), `docs/REDESIGN.md` (evening v2 contracts), `docs/CODE_REVIEW.md` (R1–R12), `docs/REBUILD_VERIFICATION.md`, `docs/MADISON_REPAIR.md`.

## Known limits

- Discovery is limited by the providers: each Places query returns at most 60 results, so a dense 50-mile area is not guaranteed to be complete.
- Research depth depends on what churches publish. Coverage gaps are stated in summaries and reports, never filled in.
- Replies arrive whole (with a typing indicator), not streamed token by token.
- The denomination KB is about 77% accurate on audited fields. It is used only as labeled priors, and sensitive fields need human review (`eval/denomkb_audit.md`).
- A full live 25-sermon deep dive with audio transcription has only been tested offline. Live runs so far used captions and capped budgets.

## License

MIT. See `LICENSE`.
