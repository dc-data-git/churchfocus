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

## extract.v1 (superseded 2026-10-07 by extract.v2)
### What changed
v1 listed up to 3 "example values from other groups" per field to keep style consistent.
### What was wrong
qwen2.5:7b copied the examples. A 40-claim hand audit of the first full run (213 groups, 2,599 fills)
found fills such as American Carpatho-Russian Orthodox typical_worship_style = "Expressive prayer, preaching
and music; local differences" and Conservative Judaism divorce_remarriage = "Marriage indissolubility;
annulment..." (both copied example styles unrelated to the quote).
Audit result for v1 fills: 14/40 supported (35%), 11/40 partial (28%), 15/40 unsupported (38%).
Failure modes: (1) example leakage, (2) a real but irrelevant quote attached to the value (the verbatim
check proves a quote exists, not that it supports the value), (3) field misread (e.g. parent_denomination
set to the group itself).
### Fix
v2 drops the examples. Separately, verify.v1 (a second model, yes/no per claim) now gates every fill:
only "supported" claims are applied.

## verify.v1 (superseded 2026-10-07 by verify.v2)
### Text
You audit claims in a reference database about US religious groups. Each claim says: for GROUP, the FIELD
has VALUE, and gives a QUOTE as evidence. Judge ONLY whether the quote itself supports the value for that
field. Ignore what you know from elsewhere; a true value with an irrelevant quote is NOT supported.
supported / partial / unsupported. Give a short reason (under 20 words). Return one verdict per claim id.
### Result
gpt-oss:120b-cloud, 3,403 claims: 50% supported, 17% partial, 33% unsupported. Hand audit of 30 random
"supported" fills: 20 correct (67%), 4 partial (13%), 6 wrong (20%) (up from 35% correct before verification).
### What was wrong
All 6 errors were field mismatches: the quote supported the value, but the value did not answer the field
(sexual-ethics resolution filed as clergy_celibacy; immigration statement as scripture_interpretation;
"includes ... the LGBTQ+ community" (a list of people served) as lgbtq_relationships policy).
### Fix
v2 requires (a) quote states value AND (b) value answers the field, with those failure examples in the prompt.
Separately, ethically sensitive fields (LGBTQ, same-sex marriage, abortion, women's ordination/preaching/senior
pastors, divorce) are never auto-applied: a person must accept them in proposals.csv (`denomkb apply-review`).

<!--
## extract.v0 (superseded YYYY-MM-DD by extract.v1)
### Text
...
### What was wrong
### Evidence
-->
