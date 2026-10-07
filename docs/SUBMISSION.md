# Preliminary submission — 250-word description (draft; edit freely)

**Church Search** — an AI research agent that helps people find a church by what it actually believes and practices, not just what its website claims.

Finding a church is high-stakes and low-information. Statements of faith say little about practice: who preaches, what sermons emphasise, how worship feels. Campus pastors and nonprofit workers who help others find a church repeat this research by hand for every person.

Church Search starts with a short conversation that turns everyday preferences ("a band, not an organ"; "women in leadership matters to me") into a weighted profile. It finds nearby churches through Google Places and works out each church's denomination from names, official denominational directories and the church's own pages — with a confidence, not a guess. For churches the user picks, an agent researches the church's website, sermon archives, historical snapshots and public records, deciding which source to try next and when it has enough evidence. Every claim carries its source, a quote and a reliability tier, and the report puts stated beliefs beside observed practice (for example, the share of sermons preached by women).

Behind it sits a knowledge base of 217 US religious groups, filled and fact-checked by local models with a human review gate on sensitive topics, served over MCP. The agent never collects data about congregants, never contacts churches, and hands hard questions back to the person with questions to ask on a visit.

Built at the 2026 Gloo AI Hackathon by a professor and three students from Tabor College.
