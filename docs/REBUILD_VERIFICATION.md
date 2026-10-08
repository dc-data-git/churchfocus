# Evening rebuild verification — October 7, 2026

Rebuild implementation and integration verification completed by Codex after Claude’s handoff. No commit, push, tag, or hackathon submission was performed.

## Verified
- Final full suite: **167 passed**, Python 3.11.9 in the project's original `.venv`, 66.21 seconds. Test data was isolated from production data.
- Desktop (1440×900) and phone (390×844): greeting, Enter-to-send, user messages, location radius, preferences panel, question creation/edit controls, deep job, report link, and report question section. No browser JavaScript errors or horizontal phone overflow. Browser workflow uses fixture services to remain repeatable.
- Real APIs: Hesston geocode, Places search (five candidates), two-page website research (three claims), tightly capped tool-driven deep research, and a saved HTML report. Completed in 24.7 seconds; five tool calls/two-minute cap, no audio downloads/transcription.
- Real model conversation through the chat API: location, traditional hymns, denomination avoidance, and correction from 10 to 20 miles persisted correctly. This caught and fixed the model's `add`/`assert` mismatch and omitted top-level location.
- Client syntax and tracked whitespace checks passed.

## Integration repairs
Report links now appear in chat. Questions refresh, show church names and answers, and can be added/edited/dropped. Free-text questions enter the deep agent state and the final report. Source quotes must belong to the returned URL; website evidence uses the fetched page URL. Unverified model prose is withheld. A new question prevents reuse of an older deep report. Search requests deduplicate overlapping coverage, and location changes clear saved candidates and coverage. Cross-session question edits are rejected. Memory operations have a complete response schema, and released prompts are preserved through interview_skill.v3. Multi-church answers and denomination comparisons are supported.

## MVP limits and remaining team work
Replies arrive as complete messages with a typing indicator, rather than token streaming. Research older than 30 days is re-read rather than hash-verified. Search queries have provider result caps, so geographic coverage cannot promise exhaustive church discovery. The live check did not exercise long-running audio transcription or a full 25-sermon research session; offline sermon regressions passed.

W2 interface and W6 documentation are complete. W7 integration is verified; submission remains a team action. H1 sensitive-prior review, H3 expanded labels, H5 burden validation, and full H6 sermon smoke test remain as recorded in tasks.json. The local app is available at http://127.0.0.1:8000.

Concurrent message writes now allocate sequence numbers in an immediate transaction. The regression sends 24 messages from eight workers and checks unique ordering; the final browser run checks HTTP 500 responses as well as JavaScript errors.
