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
