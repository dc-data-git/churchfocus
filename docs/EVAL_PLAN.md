# Church Search — EVAL_PLAN

Goal: numbers we can defend in the Agent Build Doc and on stage, plus a published eval set (bonus).

## 1. CI bar (mechanical)
`pytest -q` green on a clean checkout, no network (fakes + fixtures). Guardrail tests (§5) are part of the suite.

## 2. Golden-data oracles (`eval/`)
| Set | Size | Labelled by | Metric | Target tonight |
|---|---|---|---|---|
| `eval/denoms.jsonl` — churches with known denomination (from official locators), 3 regions incl. Hesston/Wichita, ≥ 5 non-denominational, ≥ 3 "secret denomination" | 30 | Wade | top-1 accuracy; accuracy when confidence ≥ 0.8; % routed to "unknown" | ≥ 80% on confident; report the rest honestly |
| `eval/stage2.jsonl` — 10 church sites × 8 features (service times, women.senior_pastor, theology.baptism, lgbtq.marriage, community.kids, small groups, worship.style, logistics.language) | 80 labels | Katie + Carter | precision of stored claims; verbatim-quote rate | precision ≥ 85%; verbatim 100% |
| `eval/deep.jsonl` — 3 deep dives, 20 evidence items each, hand-audited | 60 | Daniel | % supported / partial / wrong; stated-vs-observed rows found | report |
| denom-kb audit (existing) | 30–40 per round | Claude + Daniel | % correct fills | 35% (extract) → 67% (+verify.v1) → v2 pending |
| `eval/profiles.jsonl` — 5 scripted personas (§4) | 5 | Katie | profile fields correct after read-back | 5/5 with ≤ 1 correction each |

## 3. Performance and cost (bonus: cost metrics)
From `data/logs/calls.jsonl` and step logs: Stage 1 latency (target < 60 s), Stage 2 per church (< 2 min), Stage 3 minutes and $ per church, self-corrections per run (rejected quotes recovered), sermons: audio minutes per wall minute and $ per sermon-hour (D7/D9 decision input).

## 4. Human gates (role-play scripts)
1. Campus pastor finding a church for a student who grew up Catholic, wants contemporary worship, must have a young-adults group.
2. Nonprofit worker helping a Spanish-speaking family with kids; accessibility needed; doesn't care about denomination.
3. Egalitarian seeker: women pastors is a dealbreaker; charismatic worship nice-to-have.
4. Traditional seeker: expository preaching important; Reformed; prefers liturgy.
5. Person who answers "prefer not to say" to LGBTQ and skips theology.
Edge cases to show live (Track 1 requirement): church with no website (Places only → low confidence → escalation card); statement says women can lead but sermons show no women preaching (stated vs observed); crisis message in chat (human-help card).

## 5. Guardrail / leakage matrix (automated tests)
| Rule | Test |
|---|---|
| G1 no people data | blocklisted URLs (prayer, directory, login, give, check-in) never fetched; stored page text has emails/phones scrubbed except church main contact |
| G2 no fabricated theology | evidence without quote/url (A–C) rejected; report renderer adds no unsourced claims (diff of claims vs evidence) |
| G3 stated-only social | lgbtq/social features never filled from prior or D-tier for a church; no party labels anywhere in outputs |
| marriage rule | marriage sentence → lgbtq.marriage only |
| G4 read-only | `web.py` refuses non-GET; agent tool list has no write actions |
| G6 sensitive denom fields | priors for sensitive fields only when human-accepted/original |
| escalation | crisis phrases trigger `pastoral_or_crisis`; budget stop with open dealbreaker triggers `budget_exhausted` |
| secrets | grep logs for key patterns = 0 |

## 6. Auditable session log
`/api/log/{session}` returns every step (tool, why, input, result summary, features moved, cost). One full session log is attached to the Agent Build Doc.
