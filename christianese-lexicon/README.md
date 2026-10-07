# christianese-lexicon

Mines a corpus of church-related text for the **church vocabulary ("Christianese")
that a church-discovery agent must translate**, measures which terms are ambiguous,
contested, or tradition-specific, and has a **local model** draft a lexicon entry for
each one that maps the term to **observable features** (things you can verify from a
church's website, livestream, sermons, or staff page).

Everything runs on your own machine through [Ollama](https://ollama.com) (or any
OpenAI-compatible local server). No cloud API is needed. The run is resumable, so it
can run overnight unattended.

Output: `work/out/lexicon.json`. That is the file the denomination MCP server loads,
plus a report with the numbers for the hackathon build doc.

---

## Quick start: Windows (PowerShell)

Needs Python 3.12 or 3.13 from python.org (every dependency has a prebuilt wheel) and Ollama for Windows.

```powershell
git pull
cd church-discorvery-hackathon\christianese-lexicon
ollama pull qwen2.5:14b; ollama pull qwen2.5:7b; ollama pull nomic-embed-text
py -3 -m venv .venv
.venv\Scripts\Activate.ps1                   # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy config.example.yaml config.yaml
#   put corpus files in data\raw\
$env:PYTHONUTF8 = "1"
python -m lexicon doctor
powershell -ExecutionPolicy Bypass -File scripts\run_overnight.ps1
python -m lexicon status
```

## Quick start: Linux / WSL2 / macOS

This folder lives inside the hackathon repo (`church-discorvery-hackathon/christianese-lexicon/`)
but is self-contained: run every command from this folder.

```bash
# 0. get the code on the machine that will run it
git clone https://github.com/dc-data-git/church-discorvery-hackathon.git   # or: git pull
cd church-discorvery-hackathon/christianese-lexicon

# 1. models (about 15 GB total; the 14B fits a 16 GB GPU at Q4)
ollama pull qwen2.5:14b
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# 2. python env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. config + data
cp config.example.yaml config.yaml
#    put corpus files in data/raw/   (format below)

# 4. check everything, then run
python -m lexicon doctor        # verifies files, model server, chat + embed models
python -m lexicon run           # resumable; Ctrl-C and re-run any time
python -m lexicon status        # what is done, how long each stage took
```

Try it with no models and no data first:

```bash
python tests/make_fixture.py data/raw      # SYNTHETIC corpus, mechanics only
python -m lexicon run --dry-run            # fake model outputs, writes to work_dryrun/
rm data/raw/synthetic.jsonl                # before a real run
```

To leave it running on another machine: `scripts/run_overnight.sh` (runs under `nohup`
and logs to `work/run.log`).

**Ollama on Windows, Python in WSL2:** both work together. If `doctor` can't reach
`localhost:11434` from WSL, either turn on WSL mirrored networking, or set
`OLLAMA_HOST=0.0.0.0` on Windows and point `llm.base_url` at the Windows host IP
(`ip route show default | awk '{print $3}'` inside WSL).

## Input format

Put `.jsonl` or `.csv` files in `data/raw/`. One record per post or page:

| field | required | meaning |
|---|---|---|
| `text` | yes | the text |
| `side` | yes | `seeker` (someone describing the church they want) or `church` (a church describing itself) |
| `tradition` | no | for church-side text: `baptist`, `anglican`, `pentecostal`, `nondenominational`, … (use consistent lowercase labels) |
| `source` | no | where it came from (platform, site); used in reports only |
| `date`, `id` | no | |

**Every other field is dropped on read** (author, username, handle, …). Text is scrubbed
of @handles, emails, phone numbers and URLs before anything else happens.

The more labeled church-side text per tradition, the better the tradition-marker and
"same word, different meaning" analyses. Aim for ≥ 20 documents per tradition label
(configurable). Seeker-side text is what drives priority: what people actually ask for.

## What it does (stages)

| # | stage | model? | what it produces |
|---|---|---|---|
| 01 | ingest | – | scrubbed, deduped docs; 20% of seeker docs held out for evaluation |
| 02 | counts | – | 1–3-gram counts by side and tradition; phrase cohesion (NPMI) → candidates |
| 03 | seeker | 7B | "looking for a church that…" sentences → requested attributes (want/avoid), verbatim-checked |
| 04 | keyness | – | how churchy each term is vs. general English; which tradition it marks |
| 05 | shortlist | – | filters generic words, common English, phrase fragments; keeps the top ~300 |
| 06 | contexts | – | one corpus pass: counts, negation, co-occurrence, balanced context samples |
| 07 | polysemy | embed | sense clusters; does the meaning drift between traditions? |
| 08 | stance | 7B | approving vs critical usage → contested terms |
| 09 | grounding | – | which observable words travel with each term ("contemporary" ↔ drums, band, coffee) |
| 10 | rank | – | one table, one score |
| 11 | draft | 14B | lexicon entries mapped to the controlled feature vocabulary, self-corrected |
| 12 | dimensions | embed + 14B | do the terms cluster into the dimensions we assumed? |
| 13 | coverage | 7B | % of *held-out* seeker requests the lexicon recognizes |
| 14 | report | – | `work/14_report/report.md`, `work/out/lexicon.json`, cost/latency table |

Method details and formulas: [SPEC.md](SPEC.md).

## Running it well

- **Time.** Dominated by stage 03 (one 7B call per request sentence, about 1–3 s each on a
  16 GB GPU) and stage 11 (one 14B call per drafted term, about 15–30 s each). Rough
  total: `requests × 2 s + 150 × 25 s + 300 × 5 s`, so a few hours for 5,000 requests.
  Lower `seeker.max_requests` or `draft.top_n` to shorten it.
- **Resuming.** Every model call is cached in `work/llm_cache.sqlite`. Re-running a stage
  costs nothing for calls already made. `--from 07` re-runs stage 07 and everything after it.
- **Changing a prompt.** Edit `lexicon/prompts.py`, bump its version, record the old
  version + why in `PROMPT_HISTORY.md` (the build doc requires this), then
  `python -m lexicon run --from 11`.
- **Swapping models.** Change `llm.chat_model` / `small_chat_model` / `embed_model`. For
  llama.cpp, vLLM or LM Studio set `provider: openai_compat` and `base_url`.
- **Faster, no-LLM pass.** `seeker.use_llm: false` and `stance.use_llm: false` use
  deterministic fallbacks. That's good for a first look at a new corpus.

## Human review, tiered by risk

Reviewing every entry twice would take about 15 person-hours. Instead, each drafted entry is
put in a tier automatically, and review effort goes where a bad entry would do real harm:

| tier | which entries | reviewers | why |
|---|---|---|---|
| **A** | contested, evaluative, or politically coded terms, plus terms whose meaning drifts across traditions | 2, independently | neutrality matters most here; inter-rater kappa is measured on these |
| **B** | the next 50 most-requested terms by seekers | 1 | the terms the app will actually hit |
| **audit** | 20 random terms from the rest | 1 | an honest error estimate for everything nobody reviewed |
| **C** | everything else | none | stays `vetted: false`; the app confirms its meaning with the user, as it does for unknown words |

Expect about **4–5 person-hours** total (about 3 min per tier-A entry per reviewer, about 1.5 min for B and audit).

**Before anyone reviews:** one person reads about 10 drafts. If the model makes the same
mistake repeatedly (wrong feature ids, slanted neutral options), fix the prompt and run
`python -m lexicon run --from 11`. That's cheaper than fixing it row by row.

```bash
python -m lexicon review plan                             # review/plan.csv + counts + time estimate
python -m lexicon review export --reviewer anna           # tiers A, B, audit -> review/review_anna.csv
python -m lexicon review export --reviewer ben --tiers A  # second reviewer: tier A only
#   fill keep / types_ok / features_ok / neutral_ok with y or n (notes optional)
python -m lexicon review kappa review/review_anna.csv review/review_ben.csv   # agreement on tier A
python -m lexicon review apply review/review_anna.csv review/review_ben.csv   # -> work/out/lexicon_reviewed.json
```

`apply` rules: any reviewer saying `keep=n` removes the entry. An entry is `vetted: true`
only when its tier's required number of reviewers all said keep and neutral. It writes
`review/summary.json` with the audit error rate (estimated share of unreviewed entries
with an error), lists anything still missing a reviewer, and re-runs the coverage eval.
For the build doc, report the vetted count, the tier-A kappa, the audit error rate and coverage.

Tier sizes are configurable under `review:` in the config.

## Outputs you'll use

| file | for |
|---|---|
| `work/out/lexicon.json` | the MCP server (draft; `vetted: false`) |
| `work/out/lexicon_reviewed.json` | the MCP server after review (every kept entry, each marked `vetted` true/false with its tier) |
| `review/summary.json` | vetted count, audit error rate, entries still needing a reviewer |
| `work/14_report/report.md` | build doc: top terms, tradition markers, contested terms, coverage, cost/latency |
| `work/11_draft/proposed_features.json` | features the model wanted that aren't in `data/features.yaml`; review and promote |
| `work/11_draft/rejected.jsonl` | terms the model judged not to be church vocabulary (spot-check these) |
| `work/13_coverage/coverage.json` | coverage % and the top *uncovered* seeker phrases (next additions) |
| `work/calls.jsonl` | auditable log of every model call |

## Editable knowledge files

- `data/features.yaml`: **the shared feature vocabulary.** It is the contract between the
  user's stated preferences and the evidence found about churches. Change it deliberately.
- `data/seed_terms.txt`: terms always analyzed, even if rare.
- `data/generic_terms.txt`: churchy words that need no translation ("church", "Sunday").
- `data/observables.txt`: concrete words used for grounding.

## Privacy and the Track 1 guardrail

The hackathon forbids building on scraped congregational data. This tool is designed to
keep only **aggregate term statistics**:

- identity fields are dropped and handles, emails, phones and URLs are scrubbed at ingest;
- contexts are short (±12 tokens) and only used for analysis and review;
- set `privacy.retain_clean_docs: false` to delete the cleaned corpus after the run;
- the exported lexicon contains term statistics and model-drafted definitions, not posts.

Prefer church-account and public-discussion text over individual congregants' posts, and
state the data sources and these measures in the build doc.

## License

Code: MIT (see LICENSE). Decide the license for the lexicon data separately. CC BY 4.0
would let other teams build on it (a Track 1 bonus).
