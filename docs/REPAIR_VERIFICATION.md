# October 7 repair verification

All tests use isolated temporary DATA_DIR and provider fakes except the explicitly bounded live checks below. Prior user/team changes preserved; no commit, push, submission or church outreach.

## Evidence so far
- Full final integrated suite:231 passed,65.35seconds. New lifecycle, medium/QA, deep corpus/coverage/cancel/source-integrity regressions included. Final active-question context regression is included; old question history retained without obsolete actions.
- Actual backend desktop/mobile Playwright: no early jobs, draft refresh, hidden finished preparation, selected-only summaries/pins, pin refresh, direct pin/unpin cancellation, persistent deep running/completion, invalid-selection422, radius-memory sync, no overflow/browser errors. Final fresh-data harness passed. Mock harness also covers scroll preservation,50-fact compactness, partial/error statuses and new-chat API.
- Real Google Places: Hesston,Kansas geocoded correctly and a bounded search returned5 churches. Missing state was not the earlier cause.
- Real First Presbyterian Springfield scan: initial8-page check reproduced missing9amZoom from article extraction. HTML hero repair plus cache invalidation retested with4pages: both9amZoom and10:45sanctuary,7 public staff records retained. Later factual Q&A returned both times with exact source quotes. Actual broad scan default30pages/120seconds; limited scans declare unread coverage.
- Real configured strong-model deep-tools call accepted new schemas and selected fetch_page. No long live sermon/audio deep job was run; sermon persistence, analysis, RAG, cancellation, freshness and report isolation are verified with fakes.
- Deep minimum coverage is seven thematic information areas, separate from the recovered nine adaptive source tactics. Budget/missing-source gaps remain explicit in reports and UI.
- Medium freshness7days/deep30days; retention90/365days. Actual source dates preserved through reuse; older scoped/article-only cached results invalidated.

## Files and resumability
Read REPAIR_PLAN.md and REPAIR_REVIEW.md, then tasks.json. Use scripts/tasks.py only to change task state; annotate stores acceptance/files/verification/next_action. X0–X5 own this repair. W7 remains a separate hackathon submission task; not performed.

## Completed runtime verification
All X0–X5 repair tasks are done. Before restart, no research jobs were active and SQLite backup completed in the private chat work directory. Restarted the existing local app on127.0.0.1:8000 using production data. Health returned ok; new research state API and Learn more/Research selected controls verified. Temporary browser fixture stopped. Refresh open app pages to load the new script.

## Practical limits
Research remains bounded by page/time/tool/sermon limits, external site access and publicly available transcripts/audio. Unread/not-found/partial areas are explicit; no fabricated completeness or progress percentages. Full live audio deep run was not executed during this repair. Its orchestration, transcripts/retrieval, cancellation, scopes, source integrity and fresh-report isolation have offline tests; live configured deep tool API was checked separately. Continue normal user/team acceptance testing.

## State source of truth
tasks.json and scripts/tasks.py hold completed state and structured acceptance/files/verification. docs/REPAIR_TASKS.json is the initial registration specification, not current status. New issues require new tasks; do not silently reopen unrelated W7 hackathon submission work.
