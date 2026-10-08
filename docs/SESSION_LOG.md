# SESSION_LOG

## Window 1 — Planning → docs (Oct 6–7)
**Participants:** Daniel (owner), Claude (dev role); students via HACKATHON SEARCH.docx (Katie theology; Carter doc, Stage 3; Wade Stage 3).
**Purpose:** settle forks, build background data, write the core docs for the Cursor build.

### Produced
| Artifact | Status |
|---|---|
| christianese-lexicon/ (local-model lexicon miner) | new; first draft run on lexicon machine |
| denom-kb/ (fills + verifies the 217-group workbook locally) | new; full run + verify.v1; verify.v2 running on Legion |
| contracts/features.yaml (features.v2), contracts/source_registry.md | new |
| docs/PRD, ARCHITECTURE, INTERFACES, CONVENTIONS, BUILD_PLAN, EVAL_PLAN, AGENT_BUILD_DOC, SUBMISSION, PROMPT_HISTORY | new |
| app/prompts/*.v1.md | new |

### Key decisions
D1–D20 (PRD §7).

### Methods worth templating
- Hand-audit a sample of model fills before trusting a pipeline (35% → 67% story); add a verifier pass + human gate for sensitive fields.
- Students' brainstorm doc mined into a single feature contract before build.
- "Guidebook + tools + stopping rule" instead of a fixed pipeline for open-ended research agents.

### Handoff → Window 2 (build)
Goal: M0 by 4 pm CT, M1 by 8 pm, submit by 10 pm CT. Start with T0 (Daniel). Nothing blocks starting if `.env` has keys.

## Window 2 — T2 built in planning chat (Oct 7, ~1:30 pm)
- Built `app/denom/kb.py` (find / get / prior / variability / compare / match_profile), `app/denom/curated.yaml`, `app/match.py`, `app/denom/mcp_server.py` (mcp 2.x MCPServer, 1.x fallback), plus shared `config.py`, `models.py`, `features.py`, `requirements.txt`. 23 tests green.
- Finding: KB prior coverage is thin (e.g. women.senior_pastor 17 groups, lgbtq.marriage 6, baptism 45 of 217 after the sensitive gate). Matching leans on church evidence; priors only nudge. More coverage after verify.v2 + Katie's sensitive review + redo-empty.
- features.v3 fixes: women.elder prior now from governance.women_ordination; dropped bad prior links (women.deacon←deacons_role, covenant_framework←israel_church_relationship). Unknown dealbreaker penalty set to −0.1×w.

## Window 3 — Build day (Oct 7 afternoon)
- Stages 0–3 + routes + offline + offline eval landed; pytest ~120 green (incl. guardrails).
- Offline denom resolve seed: top-1 77%, confident 100% (`eval/results.md`).

## T8.3 — Persona role-play (Katie) — fill live

Run three personas in the live app (`run.bat`). Log issues with severity (blocker / major / minor).

| Persona | Script | Result | Issues (severity) |
|---|---|---|---|
| 1 Campus pastor / Catholic student / YA group dealbreaker | EVAL_PLAN §4 #1 | _pending_ | |
| 2 Nonprofit / Spanish family / kids / accessibility | EVAL_PLAN §4 #2 | _pending_ | |
| 3 Egalitarian (women pastors dealbreaker) | EVAL_PLAN §4 #3 | _pending_ | |
| Edge: crisis phrase in chat | expect 988 card | _pending_ | |
| Edge: stated vs observed (women preach) | report rows | _pending_ | |

## Evening rebuild — Codex takeover
Completed interface/report/question integration and real-model memory fixes. Final suite: 167 passed in the original Python 3.11 environment. Desktop/mobile fixture browser flow passed; live Places/website/capped deep dive and two-turn conversation passed. Details and limits: `docs/REBUILD_VERIFICATION.md`. W7 submission remains a separate team action.


## October7: authorized repair design/implementation
User authorized persistent AI instructions, six-role review, state tracking and implementation after discussion. Recorded REPAIR_PLAN/REPAIR_REVIEW/contracts; registered X0–X5 via CLI. Implemented durable job authorization/generation/pins, historical memory, broad medium/hero/fullstaff/source retrieval, broad deep/sermon persistence, small UI controls/progress. Full230tests, actual desktop/mobileAPIchecks and bounded realPlaces/websiteQA/deep-tool checks passed. Final QAcontext and serverrestart tracked in X5; see REPAIR_VERIFICATION for current status.

Final verification:231tests passed65.35sec; completed10 actualAPI browser scenarios,12 mocked scenarios, boundedlivePlaces/medium/QA/deep-schema checks. SQLitebackup/noactivejobs; serverrestartedhealthy onexisting8000. X0–X5 done; no commit/publish/outreach. Open app pages should refresh. Researchbudget/sourceavailability and unexecuted longliveaudio check documented.

User requested ChurchFocus name everywhere and background sharpening with deeper research. C1 registered; updated current app/docs/report/metadata/user agent identity, active prompt version, decorative native SVG scene tied to visible research state. No image dependencies or layout redesign.

ChurchFocus C1 verified: 231 tests passed in 68.26 seconds. Browser verified title/header, six focus stages, partial report handling, reset, reduced motion and dark mobile layout with no page errors. Production had zero active research jobs; backed up before restart. Historical released prompts and stored conversation messages remain archival.

C2 complete: recovered published embedded page/menu JSON without executing JavaScript or editor defaults. Verified live Conroe homepage Sunday10:30am, stated active small groups and staff Raymond McDonald/Senior Pastor. Shell-only responses marked extraction errors, not cached; rating withheld on failed coverage; versions invalidate failed old reuse. 233 tests pass. Server restarted with zero active research jobs. Runtime logs show model API credit exhaustion; fresh model summary remains unverified. Other reported issues unchanged.

C4: deep_search.v5 activated with broken/incomplete YouTube evidence recovery, public-source identity verification, adaptive alternate routes, captions-first collection, varied samples and explicit failure limits. 241 tests pass in65.42s. Restarted with zero active research. No live broken-link recovery scenario run. Existing limits unchanged.

C5 completed: independent wanted/avoided preferences, broad Lutheran/Anglican and Protestant scope, ELS family fallback without invented practices, hymns retained apart from liturgy, authority not equated with inerrancy, explicitly prioritized liturgy upgraded, helping-other context, honest worship mismatches and no unrelated avoidance reward. Active interview_skill.v6. 248 tests passed in67.52s; real model smoke preserved hymns/liturgy/Catholic avoid and broad families. Deleted three confirmed Madison test chats and179 unshared church records plus related research/logs/report. Restart interrupted a newly active unrelated Bel Aire medium job; newer deep dive retained/running. No additional restart after cleanup.

## C6 — Head-pastor preference negation
Corrected active session 06e54e2c56b44acb8b8db05f83b38b64 by retracting the inverted preference and asserting want:no, preserving the audit history and other role preferences. Verified effective want=[no], avoid=[]. Added interpretation guard and interview_skill.v7; regression suite: 249 passed in 66.91s. Deployment pending safe server restart: unrelated deep job 0f3aad9127ea4c1c93ea254ba2a5b72d was running, so it was not interrupted.

## C6/C7 live verification
Restarted after verifying no pending/running research. Head-pastor negation guard and interview_skill.v8 now active. Explicit Protestant scope persists even without model operations; saturated Places circles refine to depth2 (still provider-limited). Restored missing Our Saviour candidate via targeted Places lookup. Live API shows Our Saviour at rank2, 3.19 miles, strong fit; all listed Catholic/Orthodox results excluded. Full suite: 251 passed in 86.24s.

## C8 — Restore fast discovery
Removed recursive saturated-circle expansion (up to147 calls); original one/seven circles restored, with per-circle publishing and generation checks. User authorized immediate restart despite ongoing deep research. Server restarted, live discovery session62cc9cb0d36c4f0fbdfc5f1945c1d795 completed with51 listed churches; Madison Our Saviour remains visible on first page. Full suite251 passed in77.31s.

## Docs refresh — Oct 7, 10:45 pm CT (Claude, task D1)
Brought every repo doc in line with the code at commit 874aab0 (after X0–X5 and C1–C8). Verified by reading the code and running the suite: 251 passed offline.
- **README:** what the app does now; Windows/macOS/Linux setup; configuration including `TOOLS_REASONING_EFFORT`; how to use each tab; guardrails; layout; docs map; known limits.
- **ARCHITECTURE, INTERFACES:** rewritten for the current modules, routes, tables, job lifecycle, coverage areas and active prompts. INTERFACES gets a contract-history section in place of the appended deltas.
- **PRD:** current requirements; decisions D24–D44 copied in from ISSUES; D45–D53 added for the repair and the C fixes.
- **CONVENTIONS, AGENTS.md, .cursor rules:** current containment rules, tasks.py commands (register, annotate), and live-server restart etiquette.
- **AGENT_BUILD_DOC, SUBMISSION (243 words), EVAL_PLAN:** current architecture, prompt evolution, tools, verification evidence and the remaining human items.
- **PROMPT_HISTORY:** active-prompt table at the top.
- **REDESIGN, BUILD_PLAN, CODE_REVIEW, ISSUES:** marked as historical records with pointers to what superseded them.
No code changed.
