# Church Search — PRD

Status: v1 spec for the 2026 Gloo AI Hackathon (Track 1, Agents of Flourishing). Living document.
Owner: Daniel (integration, Stage 1). Theology: Katie. Shared research doc + Stage 3 sources: Carter. Stage 3 sources: Wade.
Deadlines: **preliminary submission 9:00 pm MT / 10:00 pm CT Wed Oct 7** (code + 250-word description); finalist submission 9:00 am MT Thu Oct 8; 90-second presentation.

Normative words: **must**, **must not**, **should**, **may**.

## 1. Problem and user

Choosing a church is a high-stakes, low-information decision. Websites and statements of faith say what a church *says* it believes; they rarely show what it *does* (who preaches, what sermons emphasise, how worship feels). Seekers either visit for months or rely on word of mouth. People who help others find a church — campus pastors placing students, nonprofit workers helping refugees or people in recovery reconnect — repeat this research by hand for every person.

**User (D1):** anyone looking for a church for themselves *or for someone else*. The Track 1 framing is the helper: a campus pastor or nonprofit worker who must recommend churches to many people and cannot research each one deeply.

**Burden validation:** _[HUMAN: name + one-line quote from a campus pastor / nonprofit worker who confirmed this burden. Required for the Agent Build Doc.]_

## 2. Product summary

An interactive church finder that **does not maintain a database of churches**. It researches churches on demand, keeps what it learned (with sources and dates) and re-checks it before reuse. Background knowledge is a denomination knowledge base (217 US groups, six layers) behind a tool interface (MCP).

| Stage | Name | What happens | Output | Time budget |
|---|---|---|---|---|
| 0 | Conversation | Scenario questions build a weighted preference profile; read-back checkpoint | `PreferenceProfile` + likely traditions/denominations | 3–6 min |
| 1 | Light search | Places API → church list; denomination inferred with confidence | ranked list of churches with denomination + why | < 60 s |
| 2 | Medium search | User picks 1–5 churches; website research | summary card per church + "deep-dive candidate" rating | < 2 min/church |
| 3 | Deep search | User picks 1–3; agent researches sermons, history, publications | **Church Report** (finished artifact) with tiered evidence, stated-vs-observed, questions to ask on a visit | 15 min – 2 h, background |

## 3. Requirements

### 3.1 Stage 0 — conversation
- R0.1 The interviewer **must** draw questions only from `contracts/features.yaml` (`ask: core` always; `standard` by default, skippable; `if_raised` only when the user brings it up; `advanced` only if the user opts into theology detail).
- R0.2 Questions **must** be phrased as observable scenarios (the `question` text), not jargon. User jargon is mapped to features (lexicon when available, else model mapping with the feature list).
- R0.3 Each answer becomes `{feature: {want: [values], weight}}` with weight ∈ dealbreaker / important / nice_to_have / dont_care. "Prefer not to say" = dont_care.
- R0.4 The women-in-ministry ladder is **one** question mapping to the six `women.*` features. The LGBTQ question is asked by default (D14) with neutral options incl. "doesn't matter" and "prefer not to say".
- R0.5 The interviewer **must** read the profile back in plain language and let the user correct it before Stage 1 (checkpoint).
- R0.6 The user may be searching for someone else ("for others" mode): the interviewer asks about that person and never asks the helper for that person's sensitive details beyond what matching needs.
- R0.7 Pastoral or crisis content (grief, abuse, self-harm) **must** stop the interview flow and escalate to a human (see §5).

### 3.2 Stage 1 — light search
- R1.1 Places API (New) Text Search, `includedType: church`, `locationBias` circle, paging up to 60 results; OSM/Overture fallback if Places fails.
- R1.2 Denomination resolution (D5): name/alias match against the denomination KB → denomination/network locator cross-search → website classification. Result carries `confidence` 0–1 and `method`.
- R1.3 Non-denominational workaround: affiliated churches removed from the "nondenom" bucket; churches that say non-denominational/unaffiliated and unknowns grouped; "secret denomination" detection yields a confidence, not a verdict.
- R1.4 Ranking is deterministic (§ARCHITECTURE Matcher) from profile × denomination priors × distance; each row shows *why*.
- R1.5 Google attribution displayed; only `place_id` cached indefinitely.

### 3.3 Stage 2 — medium search
- R2.1 For 1–5 selected churches, fetch key website pages (home, about/beliefs, staff, ministries, events, sermons) and fill `stage ≤ 2` features with tiered evidence.
- R2.2 Output a summary card: identity, service times, leaders (public), ministries, stated beliefs relevant to the profile, matched/unmatched/unknown features, and **deep-dive candidate** rating (evidence availability × remaining uncertainty on important features).

### 3.4 Stage 3 — deep search (agent)
- R3.1 A tool-using agent loop driven by `app/prompts/deep_search.v1.md` (the guidebook). No fixed step order (D9).
- R3.2 Stopping rule: stop when every dealbreaker/important feature is settled (`settled_rule` in features.yaml) or marked not-found, or when the time/call budget runs out.
- R3.3 The agent **may** try sources not on the list if it logs why and stays within guardrails.
- R3.4 Every step is written to the session log (§INTERFACES `StepLog`). The log is the auditable record for the Agent Build Doc.
- R3.5 Sermon analysis: up to 25 sermons per church, newest first (D22; `DEEP_MAX_SERMONS`). Throughput (audio minutes per wall minute, $ per sermon) **must** be logged. The agent may stop earlier when the observed features are settled.
- R3.6 Output: **Church Report** (HTML page + JSON), showing per feature: value, tier, quote, link, date; stated vs observed side by side where both exist; open questions to ask on a visit.
- R3.6b The report has a shareable link and prints cleanly to PDF so a helper can pass it on.
- R3.7 Runs in the background with progress; the user can leave and come back.

### 3.5 Cache and reuse
- R4.1 Research results are stored per church (keyed by `place_id` + website) with `checked_at`. Before reuse, a cheap re-check (site reachable, statement-of-faith hash unchanged) **must** run; stale items are re-researched.
- R4.2 Google Places content other than `place_id` **must not** be stored long-term.

### 3.6 Escalation to humans (Track 1 requirement)
- R5.1 Escalate (show a clear "talk to a person" card, no automated action) when: crisis/pastoral content appears; a dealbreaker feature has conflicting tier-A vs observed evidence; denomination confidence < 0.5 on a church the user wants to rely on; or the agent's budget ends with dealbreakers unsettled.
- R5.2 The escalation card drafts **questions the user can ask the church** (email/phone/visit). The app never contacts a church itself.
- R5.3 Who the "person" is: crisis/self-harm → 988 Suicide & Crisis Lifeline (call/text 988 in the US) and "a trusted pastor, counselor or friend"; abuse → local emergency services / a trusted person; church-fit questions → the church itself, or in "for others" mode the helper.

## 4. Non-goals (v1)
- No church accounts, reviews written by us, ratings of churches' faithfulness, or pastoral advice.
- No collection of congregant, donor, minor or staff-pay data (D8).
- No automated outreach (email/calls) to churches.
- No user accounts; sessions are anonymous.

## 5. Guardrails (normative)
- G1 Never collect or infer data about congregants, donors, minors, or staff pay. Named people are limited to publicly listed leaders and their published teaching.
- G2 Never fabricate theology. Every church claim carries a source tier; D-tier (inference) is labelled "inferred". Denominational priors are labelled "typical for <denomination>".
- G3 Social/political positions only when the church states them (tier A) or a cited outlet reports a public action (tier B). No party labels. Marriage rule per features.yaml.
- G4 No irreversible actions; the agent only reads public web content (robots.txt respected, polite rate limits).
- G5 No pastoral judgments ("this church is unhealthy/heretical"). Concerns = cited news reports, stated neutrally.
- G6 Sensitive denomination fields are applied only after human review.

## 6. Milestones
- M0 (by 4:30 pm CT): end-to-end Stage 0 → Stage 1 list in the browser.
- M1 (by 8:00 pm CT): Stage 2 cards + Stage 3 agent producing a Church Report for one church.
- M2 (by 9:30 pm CT): preliminary submission (code, license, 250-word description).
- M3 (overnight → 9:00 am MT Thu): polish, eval numbers, video backup, Agent Build Doc complete, MCP endpoint.

## 7. Decision log (append-only; supersessions explicit)

| ID | Decision |
|---|---|
| D1 | User = anyone seeking a church for self or others; helper (campus pastor / nonprofit worker) is the Track 1 framing. |
| D2 | Denomination knowledge sits behind an MCP server (tools, not a vector DB). |
| D3 | Models: local (Ollama) + OpenAI, routed by task difficulty; Gloo optional. |
| D4 | Students brainstorm deep-dive sources (result: HACKATHON SEARCH.docx). |
| D5 | Denomination resolution: Places → name/alias + locator/network cross-search → agent website step. |
| D6 | Track revealed vs stated beliefs (e.g. who actually preaches). |
| D7 | No fixed sermon count; measure throughput. |
| D8 | Cut: staff pay, congregation demographics, dress code from photos of congregants, worship-team membership, party labels. |
| D9 | Sermon limit held open (students' "1–2 max" not adopted). Deep search = reasoning guidebook + tools + stopping rule, not a fixed pipeline. |
| D10 | No fixed demo city; live demo, with a pre-started deep dive and a recorded backup. |
| D11 | Marriage rule: a marriage definition is the church's position on same-sex marriage; it does not settle LGBTQ membership/leadership. |
| D12 | Roles: Katie theology; Carter shared doc + Stage 3 sources; Wade Stage 3 sources; Daniel integration + Stage 1. |
| D13 | App name: Church Search. |
| D14 | Stage 0 asks the LGBTQ question by default (neutral options). |
| D15 | `contracts/features.yaml` (features.v2) is the single shared vocabulary. Source tiers A/B/C/D + prior. |
| D16 | Stack: Python 3.11+, FastAPI, Jinja2 + HTMX, SQLite. (Dev default; veto-able.) |
| D17 | Models: cloud (OpenAI) on stage; local models used for batch work (denom-kb, lexicon) and shown in logs. Model names live in `.env`. (Dev default.) |
| D18 | Sermons: podcast RSS + transcription first; YouTube captions a labelled extra; SermonAudio API if a key is available. (Dev default.) |
| D19 | christianese-lexicon not wired into tonight's build; shown as a reusable component; Stage 0 maps jargon with the model + features list. (Dev default.) |
| D20 | Matching is deterministic and explainable; models only extract evidence and phrase questions. (Dev default.) |
| D21 | Red-team fixes (features.v3): women ladder records minimum and/or maximum; ladder splits pastors vs elders; marriage and LGBTQ membership/leadership asked as two parallel questions; `community.young_adults` added; deep-search self-correction (quote re-check) made explicit. |
| D22 | Sermon cap = 25 per church (supersedes the open limit in D9). Deep-search budgets raised to 120 min / 150 tool calls to fit it. |
| D23 | Denomination KB accuracy 77% (audit round 3, n=30) accepted for the demo; impact bounded by prior-only use + sensitive-field human gate; improvement plan in eval/denomkb_audit.md (tracked as Q1). |

## 8. Open questions
- Q1 Burden validation quote (HUMAN, Daniel).
- ~~Q2 Sermon budget~~ — resolved by D22 (cap 25). Live demo uses a pre-started deep dive.
- Q3 Hosting for "runnable without a developer": Render/Railway vs local `run.bat`. Default: local + recorded demo tonight; hosted tomorrow morning if time.
