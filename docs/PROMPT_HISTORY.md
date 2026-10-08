# Prompt history (app)

Every prompt change: new file `app/prompts/<name>.v(N+1).md`, and an entry here with the old version, what was wrong (with evidence: a log line, a failing case), and what changed. The Agent Build Doc quotes from this file.
denom-kb prompt history (match.v1→v2, extract.v1→v2, verify.v1→v2) is in `denom-kb/PROMPT_HISTORY.md`.

## Active prompts (October 7, late evening)
| Prompt | Active version | Loaded by | Earlier versions |
|---|---|---|---|
| interview_skill | **v8** | `app/stage0/conversation.py` | v1–v7 (archived) |
| crisis_check | **v2** | conversation.py, stage0/interviewer.py | v1 |
| medium_extract | **v1** | `app/stage2/summary.py` | replaced page_extract.v1/v2 for website research |
| deep_search | **v5** | `app/stage3/agent.py` | v1–v4 |
| sermon_analyse | **v3** | `app/stage3/sermons.py` | v1, v2 |
| denom_classify | v1 | `app/stage1/denomination.py` | — |
| report | v1 | `app/stage3/report.py` | — |
| prior_map | v1 | `app/denom/kb.py` | — |
| interviewer, readback | v1 | `/v1` flow only | superseded by interview_skill |
| denom_answer, memory_edit, church_qa | inline | conversation.py, qa.py | unversioned system strings; any change still gets an entry here |

Entries below are in the order the changes were made.

## v1 set (2026-10-07)
deep_search.v1, interviewer.v1, readback.v1, denom_classify.v1, page_extract.v1, sermon_analyse.v1, report.v1, crisis_check.v1, prior_map.v1 — initial versions. interviewer.v1 and deep_search.v1 were revised before first use after a persona red-team (D21): ladder min/max, two LGBTQ questions, explicit quote self-check.

## crisis_check.v1 → v2 (2026-10-07, code review R11)
v1 said "When unsure, escalate" and listed "acute grief" as an escalation reason. Together with a keyword list that included "died", "grief", "abuse" and "crisis", a normal answer like "Wichita. My mom died last spring and I want a church family" ended the interview with a 988 message. Grief and past hurt are among the most common reasons people look for a church.
v2: escalate only for self-harm, immediate danger, abuse happening now, a medical emergency, or an explicit request for crisis help; explicitly continue for past loss, past church hurt, loneliness, divorce, moving. Hard keyword triggers reduced to self-harm/danger phrases.

## sermon_analyse.v1 → v2 (2026-10-07, code review R7)
v1 never asked who preached by gender, but the aggregation counted `speaker_gender == "female"`. Every analysis defaulted to "unknown", so every church came out as women.preach = never ("0 of 6 sermons") — and that counted as *settled* evidence on a sensitive, often must-have feature.
v2 adds `speaker_gender` (female|male|unknown) taken only from the speaker's name or introduction, never guessed. Aggregation now counts only sermons with an identifiable speaker and emits nothing below 5 of them.

## deep_search.v1 → v2 (2026-10-07, code review R6/R10)
v1 told the agent to record sermon observations itself as tier D and, when a quote failed the verbatim check, to "record the item as tier D inferred". That let the agent settle sensitive features (e.g. women.preach = never) with no analysis behind them. It also named a single-sermon tool that made the model copy whole transcripts into tool arguments (context overflow, truncated JSON).
v2: sermon workflow by sermon_id (find → get → transcribe_sermons → analyse_sermons, which records its own evidence); record_evidence takes tiers A/B/C only, "unstated" allowed; failed quotes leave the feature open; tools may return errors to adapt to; the latest CURRENT STATE message replaces earlier ones.

## interviewer.v1 → interview_skill.v1 (2026-10-07, live testing U1-U11, D26-D27)
interviewer.v1 drove a fixed ladder of questions and asked the person to pick want / avoid / doesn't matter for each. Live testers said there were too many questions, it felt like a form, and asking about women in leadership and marriage "led the witness" (B1).
interview_skill.v1 is open-ended: one invitation to say what matters, 0-3 follow-ups, never raises belief topics first, never asks for want/avoid or a rating. The model infers memory ops (key, val, stance, strength, conf, src, ev, why) from everything said; the server validates them against features.yaml and the scoring reads them through fixed rules (memory.to_profile).

## page_extract.v1 → v2 (2026-10-07, live testing U18-U20)
v1 stored the placeholder word for free-form features (e.g. `language_list` instead of the languages), picked short fragments as quotes, and inferred multisite from a livestream. v2 returns the actual content for free-form features, asks for the best full-sentence quote, forbids the livestream inference, and keeps the marriage rule. Stage 2 now asks each page only for the person's features plus identity basics.

## New small prompts (2026-10-07, v2)
denom_answer, church_highlights, church_qa, memory_edit are inline system prompts in conversation.py / jobs.py / qa.py: each answers ONLY from the JSON facts given; church_qa sources are re-checked verbatim before an answer counts as confident.

## interview_skill.v2 (Oct 7 rebuild verification)
The v1 output accepted a single denomination reference, so comparison questions could only summarize one group. v2 permits two references and routes their comparison through the KB. The original v1 is preserved.

## interview_skill.v3 (live verification)
The real model returned `op: add`; valid preferences were dropped because MemoryOp accepts `assert`. v3 spells out the operation enum, the structured schema now includes full MemoryOp items, and the server normalizes the observed add synonym. Regression and real-model checks cover preference retention.


## October 7 reviewed repair
- interview_skill.v4: About-you current view plus history/reasons, Christianese clarification, no invented criteria or repeated questions; explicit medium authorization and reset off-ramp; unique church references and truthful launch acknowledgements. Supersedes v3 automatic-medium flow.
- medium_extract.v1: broad factual baseline/full public staff and roles, active ministry existence, precisely sourced excerpts, resource catalog, independent of current preferences. Replaces medium use of page_extract.v2; faith text retained for later retrieval.
- deep_search.v3: minimum thematic coverage plus adaptive nine tactics, rich sermons central, public reusable corpus, questions additional, broad no-preference scope, budgets/cancellation/gaps. Supersedes v2 preference-only stopping.
- sermon_analyse.v3: all-feature teaching scope; forbid name/photo-based gender inference; preserve source metadata and sourced sermon observations. Supersedes v2.

## ChurchFocus brand
interview_skill.v5 supersedesv4 solely for the ChurchFocus product identity; research/memory behavior unchanged. Released archives retained for audit.

Deep_search.v4 adds explicit YouTube caption workflow and full-service vs sermon distinction; v3 lacked supported video retrieval.

Deep_search.v5 supersedes v4: broken/incomplete channel hints require adaptive public source recovery; explicit identity verification, recent varied recording discovery, real-caption attempts, bounded retries and truthful incomplete-coverage reporting. v4 supported direct YouTube retrieval but lacked recovery instructions. Released v4 preserved.

Interview_skill.v6 fixes repeated liturgy questions, helping-other context, broad family vs denomination routing, additive hymns/liturgy and independent avoid preferences, unjustified inerrancy and fabricated hard requirements. v5 preserved.

Interview_skill.v7 clarifies value/stance negation; v6 permitted the observed avoid:no inversion.

interview_skill.v8: v7 could acknowledge Protestant scope without storing it; requires saved broad Protestant affiliation and explicit non-Protestant exclusions.
