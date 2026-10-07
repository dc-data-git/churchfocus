# denom-kb

Validates and fills the denomination knowledge base
(`US_Religious_Groups_217_All_Six_Layers.xlsx`: 217 census groups × 184 fields, about 93% "Unknown")
with a **local model on an 8 GB GPU**, using **only cited sources**.

The model never fills a field from its own memory. For every group it:

1. **finds sources**: the matching Wikipedia article (a model check confirms it's the same body),
   Wikidata facts, the group's official website (crawled for belief / history / governance /
   worship pages, robots.txt respected), and any URLs already in the workbook's Sources sheet;
2. **fills deterministic facts from Wikidata** (founding year, founders, founding location, website);
3. **extracts** each Unknown field from the best-matching source excerpts. Every value must come with
   a quote copied from the excerpt, and the quote is **verified against the text** (unverifiable answers
   are dropped; the model is shown its error and gets two chances to fix it);
4. **validates** existing values: supported (confirm), contradicted (conflict, existing value kept),
   or not addressed;
5. marks Christian-specific fields **Not applicable** for non-Christian groups (Muslim, Jewish,
   Hindu, Buddhist, …) instead of searching them.

Nothing is overwritten silently. Every change is a **proposal** with old value, new value, quote,
URL, source type and confidence, ready for human review.

---

## Quick start (WSL2 / Ubuntu; Ollama on Windows or Linux)

```bash
git pull
cd church-discorvery-hackathon/denom-kb

ollama pull qwen2.5:7b                 # ~4.7 GB; fits 8 GB VRAM with 8k context
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
#   edit config.yaml: web.user_agent -> put a contact email or URL (Wikipedia asks for one)

python -m denomkb doctor               # workbook, Wikipedia, model server
python -m denomkb run --max 3          # try the 3 largest groups (~30-40 min), then read work/out/report.md
bash scripts/run_overnight.sh          # everything, largest groups first; resumable
python -m denomkb status               # progress per group
```

Try it without a model first: `python -m denomkb run --dry-run --max 2` (fake model answers,
real web; writes to `work_dryrun/`).

**Ollama on Windows, Python in WSL:** if `doctor` can't reach `localhost:11434`, turn on WSL
mirrored networking or set `OLLAMA_HOST=0.0.0.0` on Windows and point `llm.base_url` at the
Windows host IP (`ip route show default | awk '{print $3}'`).

## Outputs (`work/out/`, committed so the MCP student can pull them)

| file | what it is |
|---|---|
| `US_Religious_Groups_filled.xlsx` | copy of the input: fills in **green**, conflicts in **red** (value unchanged), Not applicable in grey; every changed cell has a comment with the quote + URL; new `Proposals` and `Run Info` sheets; Evidence/Sources sheets extended |
| `proposals.csv` | one row per proposed change, with empty `review_decision` / `review_notes` columns |
| `denominations_kb.json` | merged knowledge base for the MCP server: every field's value, status and evidence list |
| `report.md` | coverage before/after by layer, per-group results, conflicts to review, model cost and latency |

The input workbook is never modified.

## Time on an 8 GB GPU

About 5 fields per model call, about 15–25 s per call with qwen2.5:7b. Rough figures:
- a well-profiled Christian group (fill about 80 fields + validate about 50): **10–15 min**
- a group with only a short Wikipedia article: **3–6 min** (fields with no matching excerpt skip the model entirely)
- a non-Christian group: **3–5 min** (54 Christian-specific fields are marked Not applicable without a call)

All 217 groups take about 20–30 hours. The **largest 60 cover 98.8% of adherents** and finish
in about 10 hours. Groups run largest first, each is saved as soon as it finishes, and outputs are
re-exported every 5 groups, so you can stop at any time and use what's done.

Every model call and web page is cached (`work/llm_cache.sqlite`, `work/web_cache/`). Re-runs
cost nothing for work already done. `--force` re-processes groups (e.g. after an override).

## Fixing what it gets wrong

- **Wrong Wikipedia match or missing website:** add the group to `data/overrides.yaml`:
  ```yaml
  usrc2020_031:            # Amish Groups, undifferentiated
    wikipedia: Amish
    website: https://example.org
    extra_urls: [https://example.org/statement-of-faith.pdf]
  ```
  then `python -m denomkb run --only usrc2020_031 --force`.
- **A field is searched badly:** edit its `keywords` in `data/fields.yaml`.
- **Field shouldn't be filled from documents:** set `extract: false` (census counts and the
  editorial "distinguishing metadata" fields are already off).
- **Prompt changes:** edit `denomkb/prompts.py`, bump the version, and record the old text and what
  was wrong in `PROMPT_HISTORY.md` (the Track 1 build doc needs this).

## Review

Treat every proposal as a draft (`model_extracted_needs_review`). Suggested order:
1. **conflicts** (red): the model says a source disagrees with an existing value. Read the quote.
2. **theology and governance fills for the top 20 groups**: highest impact on matching.
3. spot-check about 20 random fills elsewhere and record the error rate. That's the honest
   accuracy number for the build doc.

Use `review_decision` (accept / reject / edit) in `proposals.csv`.

## Sources and licensing

- Wikipedia text is CC BY-SA. Values drafted from it are cited per field.
- Wikidata is CC0.
- Official sites are read politely: robots.txt is honored, there's a 1.5 s delay per domain, and
  pages are cached so nothing is fetched twice.
- Only organizational documents are read: beliefs, polity, history. No congregational or personal
  data is collected.
- Code: MIT.
