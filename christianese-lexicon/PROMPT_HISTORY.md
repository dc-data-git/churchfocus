# Prompt history

The Track 1 build doc requires every prompt verbatim **and at least one earlier version
with what was wrong with it**. Current prompts live in `lexicon/prompts.py`.

When you change a prompt:
1. copy the old text here under its version,
2. write what went wrong (with an example from `work/calls.jsonl` or a reviewer note),
3. bump the version constant in `prompts.py`,
4. re-run from the affected stage (`python -m lexicon run --from 11`).

---

## draft.v3 (current, 2026-10-07)
See `DRAFT_SYSTEM` in `lexicon/prompts.py`. Rule 7 adds "everyday English words used in their
ordinary sense" to not_relevant, with a test question; new rule 8 caps features per sense, and
the semantic check sends back any sense with more than 6 features.

## draft.v2 (superseded 2026-10-07 by draft.v3)
### Text (rule 7)
    7. relevance (decide it LAST, after the senses and notes):
       - "church_vocabulary": a word or phrase churches or churchgoers use whose meaning a
         newcomer, or someone from another tradition, might need explained: doctrines,
         practices, sacraments, offices, polity, worship styles, tradition names
         (e.g. "diocese", "testimony", "communion", "altar call"). This is the usual answer.
       - "logistics_term": practical vocabulary for visiting a church ("nursery", "parking").
       - "not_relevant": ONLY filler, laughter, sign-offs, podcast or radio boilerplate,
         personal names, or phrase fragments ("yeah yeah", "thanks for listening").
         If you could write a meaningful church-related gloss, it is not "not_relevant".
`relevance` is the last schema property (kept in v3).
### What was wrong
Overcorrected v1: rejected only 5 of 150 terms. Drafted everyday words such as "okay",
"stuff", "conversation", "helpful", "dad". The "okay" entry was labeled `contested` and its
single sense listed 44 features (the whole vocabulary). 19 proposed features came from such
terms ("proposed.logistics.podcast", "proposed.logistics.url_structure", "proposed.pronoun_usage").
### Evidence
Second podcast run (2026-10-07 04:01): drafted 144, rejected 5; work_podcasts/out/lexicon.json.

## draft.v1 (superseded 2026-10-07 by draft.v2)
### Text (rule 7; the rest is unchanged)
    7. Set relevance to "not_relevant" if the term is generic boilerplate, a sign-off,
       or a phrase fragment that nobody would need translated; fill the other fields minimally.
Schema property order: relevance, aliases, category, term_types, senses, ...
### What was wrong
qwen2.5:14b marked **150 of 150** terms `not_relevant`, including clear church vocabulary,
while writing full, correct entries for them. Example ("bishops"): relevance `not_relevant`,
but senses = "Leaders who govern a denomination or diocese" -> `polity.governance: episcopal`,
with a disambiguation question and neutral options. Two causes: `not_relevant` was the only
relevance value named in the prompt, and with schema-constrained output (Ollama `format`)
`relevance` was the first token decision, made before any reasoning.
### Evidence
work_podcasts/11_draft/rejected.jsonl from the first podcast run: drafted 0, rejected 150
(rejected terms included diocese, testimony, communion, baptized, local church, anglican communion).

## seeker_extract.v1 (current)
See `SEEKER_EXTRACT_SYSTEM`.

## stance.v1 (current)
See `STANCE_SYSTEM`.

## dimension_name.v1 (current)
See `DIMENSION_SYSTEM`.

<!-- Template for a superseded version:

## draft.v0  (superseded YYYY-MM-DD by draft.v1)
### Text
...verbatim...
### What was wrong
e.g. "Model invented feature ids like worship.vibe; 31% of calls needed repair.
Fix: listed the vocabulary with allowed values and added rule 1."
### Evidence
calls.jsonl: draft repairs 0.31/call → 0.08/call after change (n=150).
-->
