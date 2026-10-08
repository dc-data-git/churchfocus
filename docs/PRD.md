# ChurchFocus — PRD

Status: **built and in user testing** (2026 Gloo AI Hackathon, Track 1, Agents of Flourishing). This document reflects the app after the October 7 evening rebuild and repair (tasks W0–W7, X0–X5, C1–C8). Living document. The decision log (§7) is append-only.
Team: Daniel (integration, Stage 1, lead), Katie (theology), Carter (shared research doc, Stage 3 sources), Wade (Stage 3 sources).
Deadlines: preliminary submission 9:00 pm MT / 10:00 pm CT Wed Oct 7 (code + 250-word description); finalist submission 9:00 am MT Thu Oct 8; 90-second presentation.

Normative words: **must**, **must not**, **should**, **may**.

## 1. Problem and user

Choosing a church is a high-stakes, low-information decision. Websites and statements of faith say what a church *says* it believes. They rarely show what it *does*: who preaches, what sermons emphasize, how worship feels. Seekers either visit for months or rely on word of mouth. People who help others find a church repeat this research by hand for every person. Examples are campus pastors placing students and nonprofit workers helping refugees or people in recovery reconnect.

**User (D1):** anyone looking for a church for themselves *or for someone else*. The Track 1 framing is the helper: a campus pastor or nonprofit worker who recommends churches to many people and can't research each one deeply.

**Burden validation:** _[HUMAN: name + one-line quote from a campus pastor / nonprofit worker who confirmed this burden. Required for the Agent Build Doc. Task H5.]_

## 2. Product summary

ChurchFocus is a conversational church finder that **does not maintain a database of churches**. It researches churches on demand, keeps what it learned with sources and dates, and re-checks saved research before reuse. Background knowledge is a denomination knowledge base (217 US groups, six layers) behind an MCP tool interface.

| Stage | What happens | Started by | Output |
|---|---|---|---|
| 0 Conversation | The person describes, in their own words, where they're starting and what matters. The model proposes memory entries; the server validates them. | always on | Append-only memory log → current `PreferenceProfile`; About-you tab |
| 1 Discovery | Places search around the starting place (OSM fallback); cheap denomination match; non-Nicene groups removed | a starting place (silent, background) | Churches tab: fit label, distance, denomination, why lines; re-ranks as memory changes |
| 2 Website research | Bounded recursive scan of the public site; factual extraction with verbatim quotes | **Learn more → Research selected (1–5)**, or a pin | Factual summary per church: services, full public staff, active ministries, beliefs, coverage gaps |
| Q&A | Factual questions answered only from saved sources | a chat question | Sourced answer, or an Open question |
| 3 Deep research | Tool-using agent, seven coverage areas, sermons central, the person's open questions | a Deep dive button or chat request | **Church Report** (shareable HTML, prints to PDF) |

## 3. Requirements (current)

### 3.1 Conversation (Stage 0)
- R0.1 The conversation **must** be open-ended (D24). It asks for a starting place, then invites the person once to say what matters and what to avoid, and asks follow-ups only when an answer would change results. It **must not** ask the person to pick want/avoid/doesn't-matter or rate importance (D27).
- R0.2 Everything the person says **should** update memory, not just the answer to the last question. Church jargon is read with lexicon hints (D34). Hints are guesses and never become hard filters.
- R0.3 Memory **must** be an append-only log of operations with evidence and reasons (D27). The current profile is computed from the log with fixed rules, and the person can see and correct it (D32). Corrections are logged, never overwritten.
- R0.4 The bot **must not** raise belief topics the person hasn't mentioned, such as women in leadership, marriage/LGBTQ or baptism (D33, supersedes D14). If the person raises one, it follows their lead neutrally.
- R0.5 In "for others" mode the bot **must not** ask about the private life of the person being helped.
- R0.6 Crisis content **must** stop the normal reply and show the 988 / trusted-person message (§3.6). Grief and past church hurt are not crises (R11).
- R0.7 Side questions **should** be answered: denomination summaries and comparisons from the KB, labeled as typical for the denomination (D35), and factual questions about churches (R2.4).
- R0.8 The bot **must not** claim an action happened that the server didn't perform. A deep dive is acknowledged only after the job exists, and an ambiguous church name gets a clarifying question. "Start over" offers a new chat or a changed search before resetting.

### 3.2 Discovery (Stage 1)
- R1.1 Places API (New) Text Search with a `locationBias` circle and paging up to 60 results, with an OSM Overpass fallback. Discovery starts in the background once a place is known (D39) and **must not** post chat announcements (U41).
- R1.2 Coverage expands on demand up to 50 miles (D38). It uses one query circle up to 12 mi, or a centre plus a ring of six, and queries again only when the radius grows beyond what is covered (D40, C8). Results appear as each circle finishes.
- R1.3 Denomination: a cheap name/KB match for every church, and website classification only for rows shown with low confidence. The result carries `confidence` and `method`, and unverified affiliation is labeled.
- R1.4 Groups outside historic Trinitarian Christianity **must** be removed everywhere (D29).
- R1.5 Ranking is deterministic and explainable (D20, ARCHITECTURE §7). Fit labels: Strong, Possible, Not enough info yet, Unlikely, Poor (D30/D31). Sorted by fit then distance. Radius filter defaults to 15 mi or the person's limit, and page size is adjustable.
- R1.6 Practice and belief preferences change fit only. Hard exclusion happens only for an avoided denomination, tradition or branch, or a confirmed mismatch with an explicit required affiliation (D28, C5).
- R1.7 Google attribution is displayed. Only `place_id` is kept long-term.

### 3.3 Website research (Stage 2)
- R2.1 Website research **must not** start until the person asks (U42). **Learn more** may prepare the top five invisibly. **Research selected** (1–5) or a pin authorizes and shows them, and unselected, unpinned work is cancelled. Unpinning cancels unfinished work.
- R2.2 Research **must** cover practical basics regardless of preferences (U44, U47): service times (day, time, online/in person), the full publicly listed staff with roles, active ministries, and stated beliefs. The scan is recursive within the site, bounded (default 30 pages / 120 s), and states what was not read.
- R2.3 Every fact and staff entry **must** carry a verbatim quote from the page it came from. Summaries shown to the person are plain-language and factual, with no internal ids or tier jargon.
- R2.4 Factual questions **must** be answered only from saved sources, with exact quotes at the cited URL. Otherwise the question goes to Open questions, which the person can add, edit or drop (D25). Q&A never starts a deep dive by itself.

### 3.4 Deep research (Stage 3)
- R3.1 A tool-using agent driven by the `deep_search` guidebook (now v5), with no fixed step order (D9).
- R3.2 Scope is broad (U47). The agent investigates seven minimum areas and chooses adaptively among nine source tactics (ARCHITECTURE §11). Preferences and the person's questions set priorities; they never limit scope. Research with no stated preferences still proceeds.
- R3.3 Sermons are central. Up to 25 per church (D22): captions first, then transcripts, then transcription. Recovery of broken or missing channel links is allowed, but the congregation's identity must be verified before using a channel's teaching (C3/C4). Transcripts are saved for later Q&A.
- R3.4 Every step is logged (`StepLog`) with the agent's stated reason. The log is the auditable record.
- R3.5 Output: a **Church Report** with sources, stated vs observed, research coverage, still unknown, questions to ask on a visit, and the person's questions answered or explicitly unanswered. A report with gaps is labeled partial. It has a shareable link and prints to PDF.
- R3.6 Runs in the background with persistent visible progress: real stage, counts and elapsed time (U45). It can be cancelled, and a failure shows explicitly.

### 3.5 Reuse and retention
- R4.1 Website facts younger than 7 days may be reused. A deep dive builds a fresh report per person but reuses saved sources, re-checking site pages older than 30 days. A source's date **must not** advance through a cache copy.
- R4.2 Retention: website research 90 days, deep research 365 days (D43). Facts may be reused across sessions, but another user's preferences or reports never are.
- R4.3 Google Places content other than `place_id` **must not** be stored long-term.

### 3.6 Escalation to humans (Track 1 requirement)
- R5.1 Escalate when crisis or pastoral content appears, a dealbreaker has conflicting evidence, denomination confidence is too low for what the person relies on, or the budget ends with important questions open. Escalation is a clear message, never an automated action.
- R5.2 Reports draft **questions the person can ask the church**. The app never contacts a church.
- R5.3 Who the "person" is: for crisis or self-harm, 988 (call or text in the US) and "a trusted pastor, counselor or friend"; for danger, emergency services; for church-fit questions, the church itself, or in "for others" mode the helper.

## 4. Non-goals
- No church accounts, reviews written by us, ratings of churches' faithfulness, or pastoral advice.
- No collection of congregant, donor, minor or staff-pay data (D8).
- No automated outreach to churches.
- No user accounts. Sessions are anonymous; the session id lives in the browser.

## 5. Guardrails (normative)
- G1 Never collect or infer data about congregants, donors, minors or staff pay. Named people are limited to publicly listed leaders, their roles and their published teaching. Gender is never inferred from names, voices or photos.
- G2 Never fabricate theology. Every church claim carries a source tier and quote. D-tier is labeled as observed from sermons. Denominational priors are labeled "typical for <denomination>" and never stated as a congregation's practice.
- G3 Social and political positions only when the church states them (tier A) or a cited outlet reports a public action (tier B). No party labels. Marriage rule per features.yaml (D11).
- G4 No irreversible actions. Read-only public web: robots.txt respected, polite rate limits, no logins, no forms.
- G5 No pastoral judgments. Concerns are cited news reports, stated neutrally.
- G6 Sensitive denomination fields are applied only after human review.

## 6. Milestones
- M0 (4:30 pm CT) Stage 0 → Stage 1 in the browser: **met**.
- M1 (8:00 pm CT) Stage 2 + Stage 3 report: **met** in the rebuild. Live H6 smoke test still open in tasks.json.
- M2b (8:30 pm CT) integration of the v2 rebuild: **met** (W7 integration verified).
- M2 (10:00 pm CT) preliminary submission: team action, tracked as W7.
- M3 (9:00 am MT Thu) finalist submission: Agent Build Doc complete, eval numbers, video backup.

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
| D13 | App name: ChurchFocus (briefly "Church Search"; ChurchFocus everywhere since C1). |
| D14 | ~~Stage 0 asks the LGBTQ question by default.~~ Superseded by D33. |
| D15 | `contracts/features.yaml` (features.v3) is the single shared vocabulary. Source tiers A/B/C/D + prior. |
| D16 | Stack: Python 3.11+, FastAPI, Jinja2, SQLite. v1 pages use HTMX; the v2 chat is plain JavaScript. |
| D17 | Models: cloud (OpenAI) on stage; local models for batch work (denom-kb, lexicon). Model names live in `.env`. |
| D18 | Sermons: podcast RSS + transcription; YouTube captions (made first-class in C3); SermonAudio if a key is available. |
| D19 | ~~christianese-lexicon not wired in tonight.~~ Superseded by D34. |
| D20 | Matching is deterministic and explainable; models only extract evidence and phrase text. |
| D21 | Red-team fixes (features.v3): women ladder min/max; pastors vs elders; marriage and LGBTQ membership/leadership separate; `community.young_adults`; deep-search quote self-check. |
| D22 | Sermon cap = 25 per church. Deep budgets 120 min / 150 tool calls. |
| D23 | Denomination KB accuracy 77% (audit round 3, n=30) accepted for the demo; bounded by prior-only use + sensitive-field gate; improvement plan in eval/denomkb_audit.md (Q1). |
| D24 | Open-ended interview: starting place, then "what matters to you" in their own words; follow-ups only when needed; search offered right away. |
| D25 | The search leads into a conversation: background website research with progress, factual Q&A from what was read, Open questions panel, deep dive targets those questions, report has "Your questions". |
| D26 | Deep-dive fix: `reasoning_effort="none"` on tool calls (U15), configurable as `TOOLS_REASONING_EFFORT`. |
| D27 | Formulaic back end, natural conversation: memory is an append-only machine-readable change log; the bot never asks want/avoid/don't-care. |
| D28 | No elimination for practice/belief preferences — they move fit only. Hard filtering only at the denomination/tradition/branch level. |
| D29 | Remove all non-Nicene groups from the app entirely. |
| D30–D31 | Fit labels Strong · Possible · Not enough info yet · Unlikely · Poor; sort by fit then distance; dynamic table, 10 per page. |
| D32 | "About you" panel shows the current profile in plain words; every item editable; edits logged. |
| D33 | Never raise belief topics the person hasn't mentioned (supersedes D14). They are still researched and shown. |
| D34 | Use the christianese lexicon (mapped to features.v3, filtered, plus a hand glossary) as interview hints; log the reason for every inference (supersedes D19). |
| D35 | Denomination is the main identity filter but the bot never prompts for it; it answers denomination questions from the KB. |
| D36 | Read-back = short summary in the bot's voice; no confirmation step before searching. |
| D37 | Origin from free text (landmarks OK); travel time → straight-line miles (~45 min ≈ 30 mi), logged. |
| D38 | Gather churches out to 50 mi; radius filter defaults to 15 mi or the person's limit. |
| D39 | Discovery starts automatically in the background once a place is known (announcements narrowed by D45). |
| D40 | Extra Places queries only when the radius grows beyond what is covered; coverage ledger per origin. |
| D41 | Standard AI-chat interface; right-side tabs on desktop, tabs above the chat on phones. |
| D42 | ~~Automatically run website research on the top 5.~~ Superseded by D46. Cancellation of unpicked work remains. |
| D43 | Save every research result: website 90 days, deep 1 year. Reuse windows refined by D47. |
| D44 | Fix everything we can tonight; MVP level. |
| D45 | (U41) No unsolicited completion announcements; discovery updates the Churches tab silently. Medium results appear on the church row. A finished deep dive posts one message with its report link. |
| D46 | (U42, REPAIR_PLAN) Website research only after explicit authorization: Learn more → Research selected (1–5) or a pin. Learn more may prepare the top five invisibly. Unpin cancels. |
| D47 | (REPAIR_PLAN) Freshness: website facts reused < 7 days; deep dives reuse saved sources and re-check site pages > 30 days; source dates never advance via cache copies. Retention 90/365 days. |
| D48 | (U44/U47) Research scope is broad and factual, not limited to preferences: medium covers services, full public staff, ministries and beliefs; deep covers seven minimum areas with nine adaptive tactics; preferences and questions prioritize only. |
| D49 | (U45/U46) Truthful actions: create the job before acknowledging it; resolve church names uniquely or ask; persistent visible deep-dive status with real counts; no invented percentages. |
| D50 | Session generations: every reset/new place increments a generation; stale discovery and jobs cannot publish. "Start over" offers new chat vs change this search. |
| D51 | (C3/C4) YouTube sermons are first-class: captions first; recover broken channel hints with identity verification; save transcripts for future Q&A. |
| D52 | (C5–C7) Memory semantics: want and avoid are independent; assertions add, revisions replace; broad families map to tradition; ordinary priorities are "important", dealbreaker only for explicit mandatory language; explicit Protestant scope persists. |
| D53 | (C8) Discovery stays bounded (1 or 7 circles) and publishes per circle; completeness is not promised where Places caps results. |

## 8. Open questions
- Q1 Burden validation quote (HUMAN, Daniel; task H5).
- ~~Q2 Sermon budget~~ resolved by D22.
- Q3 Hosting for "runnable without a developer": currently local `run.bat` shared through Tailscale Funnel. Hosted deploy (Render/Railway with a persistent disk) only if time allows.
- Q4 Live 25-sermon audio deep dive not yet run end to end (H6); offline tests cover the pipeline.
