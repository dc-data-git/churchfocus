# Prompt history

The Track 1 build doc requires every prompt verbatim **and at least one earlier version
with what was wrong with it**. Current prompts live in `lexicon/prompts.py`.

When you change a prompt:
1. copy the old text here under its version,
2. write what went wrong (with an example from `work/calls.jsonl` or a reviewer note),
3. bump the version constant in `prompts.py`,
4. re-run from the affected stage (`python -m lexicon run --from 11`).

---

## draft.v1 (current)
See `DRAFT_SYSTEM` in `lexicon/prompts.py`.

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
