# denom-kb fill accuracy audit

Hand audit by Claude of random samples of model-filled workbook values (seeded, reproducible from proposals.csv).

| Round | Pipeline | Sample | Correct | Partial | Wrong |
|---|---|---|---|---|---|
| 1 | extract.v1 only (no verifier) | 40 fills | 35% | — | — |
| 2 | extract.v2 + verify.v1, fills marked supported | 30 | 67% | 13% | 20% |
| 3 | extract.v2 + verify.v2 (field-fit rule), fills marked supported | 30 | **77%** (23) | 10% (3) | 13% (4) |

Round 3: seed 20261007, pool = 1,284 supported non-Wikidata fills, verifier gpt-oss:120b-cloud, export 2026-10-07 11:24 CT.
Remaining failure mode is the same as round 2 — a true quote filed under the wrong field (belief vs practice, identity vs ecclesiology) — at a lower rate.
Note: many verifier verdicts have an empty `reason` (gpt-oss omitted it; normalize() fills ''), which weakens the audit trail; next prompt version should require it.
Sample size is small (±~15 points at 95%); treat as directional.

## Round 3 items

1. **correct** — Bruderhof Communities, Inc. · `practice_culture.alcohol` = "Do not prohibit the consumption of alcohol"
2. **correct** — Full Gospel Baptist Church Fellowship · `history.founding_location` = "New Orleans, Louisiana"
3. **partial** — Christian Churches and Churches of Christ · `history.historical_relationships` = "Historical relationships with the Christian Church (Disciples of Christ) and other Restoration Movement congregations." — relationship to Disciples supported; 'other Restoration Movement congregations' added
4. **wrong** — Evangelical Lutheran Synod · `theology.nature_of_church` = "Conservative, Confessional Lutheran body" — identity statement filed as nature of church (the exact failure verify.v2 names)
5. **correct** — Schmiedeleut Hutterite Group 1 · `identity.geographic_concentrations` = "Colonies in South Dakota, North Dakota, Manitoba, and Minnesota"
6. **correct** — Wesleyan Church · `history.separation_reasons` = "primarily over their objections to slavery, though they had secondary issues as well, such as ecclesiastical polity"
7. **correct** — Old German Baptist Brethren, New Conference · `theology.scripture_inerrancy` = "Infallible and inerrant"
8. **wrong** — Church of Jesus Christ of Latter-day Saints · `practice_culture.tongues_in_worship` = "Affirmed; frequency and order vary" — belief in the gift of tongues filed as tongues *in worship*; 'frequency and order vary' unsupported
9. **correct** — Cumberland Presbyterian · `history.historical_controversies` = "Dispute over ecclesiastical jurisdiction and ordination practices"
10. **correct** — Reformed Church in America · `theology.scripture_authority` = "Bible is the only rule of faith and practice"
11. **correct** — Friends General Conference · `governance.lay_participation` = "Friends volunteer in ministry for the various programs and operations of the organization, from policy and procedure to "
12. **correct** — Bible Way Church of Our Lord Jesus Christ World Wide, Inc. · `history.historical_relationships` = "split from the Church of Our Lord Jesus Christ of the Apostolic Faith (COOLJC)"
13. **partial** — Church of Jesus Christ of Latter-day Saints · `practice_culture.health_diet` = "Follows Word of Wisdom." — quote describes the Word of Wisdom; adherence implied, not stated
14. **correct** — Mennonite Church USA · `identity.self_identification` = "Mennonite Christian denomination"
15. **correct** — Protestant Reformed Churches in America · `governance.local_autonomy` = "High"
16. **correct** — Seventh-day Adventist Church · `governance.denominational_authority` = "Central authority through the General Conference with shared governance."
17. **correct** — American Baptist Association · `theology.god_trinity` = "personal triune God: Father, Son, and Holy Spirit, equal in divine perfection"
18. **correct** — Presbyterian Church (U.S.A.) · `history.historical_controversies` = "Old School–New School Controversy, Cumberland Presbyterian Church split, and Second Great Awakening divisions."
19. **correct** — United Reformed Churches in North America · `theology.baptism_recipients` = "Infants and adults"
20. **correct** — Advent Christian Church · `identity.geographic_concentrations` = "Eastern coast of the United States, with a strong concentration of churches in most states, and also in 30 countries int"
21. **correct** — Advent Christian Church · `governance.polity` = "Congregational in government"
22. **correct** — Church of the Nazarene · `practice_culture.alcohol` = "Total abstinence from alcohol and any other intoxicant, including cigarettes."
23. **correct** — Association of Free Lutheran Congregations · `theology.scripture_authority` = "The Bible as the complete written Word of God, preserved by the Holy Spirit for salvation and instruction."
24. **correct** — American Baptist Association · `distinguishing_metadata.distinctives` = "Independent Baptist churches voluntarily associating in their efforts to fulfill the Great Commission"
25. **correct** — Heritage Reformed Congregations · `identity.self_identification` = "Reformed"
26. **correct** — Biblical Mennonite Alliance · `identity.primary_family` = "Conservative Mennonite Anabaptist"
27. **wrong** — Reformed Church in the United States · `distinguishing_metadata.internal_subgroups` = "Eureka Classis; 'continuing' Reformed Church in the United States." — Eureka Classis is the origin of the current body, not an internal subgroup
28. **correct** — Bulgarian Eastern Orthodox Diocese of the USA, Canada and Australia · `practice_culture.formal_confession` = "Sacramental confession"
29. **wrong** — American Baptist Churches in the USA · `theology.continuationism` = "Affirmed" — 'gifts of ministry should be shared' misread as continuationism
30. **partial** — General Association of General Baptists · `identity.self_identification` = "General Baptists" — quote is about origin, only weakly self-identification

## Status: work in progress (tracked as Q1 in tasks.json)

**Decision (D23):** 77% is acceptable for the demo, because the app is built so KB errors have bounded impact:
- Denominational values are only *priors* ("typical for X; may differ locally"), weighted at 0.5 × denomination confidence × (1 − variability). They lower a score but **never exclude** a church. A church's own words (tier A) or observed behaviour (tier D) always override them.
- Sensitive fields (women in ministry, marriage, LGBTQ, abortion, divorce) are never auto-applied; a person must accept them.
- Every value carries its quote and URL, so a user can see the evidence.

**How we keep improving it (in order of expected gain per hour):**
1. **Field-definition prompts.** The remaining errors are true quotes filed under the wrong field (belief vs practice, identity vs ecclesiology). Give extract and verify the field's definition plus one negative example per confusable pair (e.g. "belief in tongues ≠ tongues in worship"). → extract.v3 / verify.v3.
2. **Require a reason on every verdict.** Many verdicts lack one; reject and retry when missing. Restores the audit trail.
3. **Two-model agreement for high-value fields.** For fields used by matching (≈25 of 130), accept a fill only when two different verifiers (e.g. gpt-oss:120b and qwen2.5:14b) both say "supported".
4. **Human review queue ordered by impact.** Review first the fields that matching uses, for the 30 largest denominations (covers most US churchgoers). Katie's H1 is the first pass.
5. **Coverage.** `run --redo-empty` with qwen2.5:14b for the ~112 groups with no sources.
6. **Bigger audits.** Round 4 with n = 100 (±10 points) after steps 1–4; report per-field accuracy, not just overall.
7. **Feedback from the app.** When deep search finds a church contradicting its denomination's prior, log it; repeated contradictions flag the prior for review.
