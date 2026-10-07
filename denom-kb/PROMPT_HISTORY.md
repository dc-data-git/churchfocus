# Prompt history

Current prompts: `denomkb/prompts.py` (match.v1, classify.v1, extract.v1, validate.v1).

When a prompt changes, copy the old text here with what went wrong and the evidence
(e.g. repair rate or dropped-quote rate from `work/calls.jsonl`, or reviewer findings).

## match.v1 (superseded 2026-10-07 by match.v2)
### Text
You check whether an encyclopedia article is about a specific US religious group.
The group comes from the 2020 US Religion Census. Answer "yes" only if the article is about this
exact body (or it is plainly the same organization under another name). Answer "no" if it is about a
broader tradition, a different body with a similar name, or a concept. Answer "unsure" otherwise.
### What was wrong
Too literal for national branches of worldwide churches. First real run (qwen2.5:7b, 3 groups):
"Catholic Church" (census) vs the Wikipedia article "Catholic Church" returned `no`. The census row is
the US part, the article the worldwide church; doctrine is identical, so the richest source was lost
(Catholic: 4 pages, 28 chunks vs SBC: 17 pages, 178 chunks). It also rejected the article on
non-denominational Christianity for the census category of the same name (fixed by override: a census
category has no organization to match).
### Fix
v2 accepts the worldwide body when the census group is its US part, and still rejects broad families
of separate denominations ("Baptists" for one convention).

<!--
## extract.v0 (superseded YYYY-MM-DD by extract.v1)
### Text
...
### What was wrong
### Evidence
-->
