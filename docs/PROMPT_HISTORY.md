# Prompt history (app)

Every prompt change: new file `app/prompts/<name>.v(N+1).md`, and an entry here with the old version, what was wrong (with evidence: a log line, a failing case), and what changed. The Agent Build Doc quotes from this file.
denom-kb prompt history (match.v1→v2, extract.v1→v2, verify.v1→v2) is in `denom-kb/PROMPT_HISTORY.md`.

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
