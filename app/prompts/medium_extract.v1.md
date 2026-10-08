# Medium factual scan v1
Read one public church page. Ignore site boilerplate. Capture factual information broadly, regardless of user preferences.
Return JSON {features: [...], facts: [...], staff: [...]}.
features: requested feature IDs only, allowed values only; status stated or not_stated; exact contiguous quote and source URL.
facts: {label, value, quote}; preserve useful factual details beyond the checklist. Prioritize worship/service times and formats,
lead pastor, full public staff roles, stated affiliation, active ministry existence, small-group ministry existence, visit/accessibility details.
For staff return EVERY publicly listed staff member {name, position, quote}; name and role must both appear in the exact source quote.
Never infer gender from names/photos. No congregants, members, donors, minors, private contact details, pay, or group member listings.
Ministry-level facts only: do not enumerate individual small groups. Distinguish undated advertised ministry from explicit evidence of current activity.
Calendar dates, individual events and registration listings are NOT baseline facts; these resources are queried when asked.
Faith statements are usable for explicit preferences and future questions; do not turn generic welcome into inclusion policy.
Marriage definitions settle marriage only, never LGBTQ inclusion. Never infer from silence or denomination.
All quotes copied exactly and contiguously from the supplied text, never paraphrase or stitch separate passages.
Free-form feature types require actual values, never the type name itself. Return [] when information is absent.

Attribution is essential: extract facts ONLY about the named TARGET CHURCH itself and its own ministries/staff/services.
A church-hosted PDF may list external churches, charities, community services, and their hours: those are not the target church's
service schedule, staff, or programs. Exclude all such facts and listings. If a passage does not clearly belong to the target church,
do not attribute it. General city resource directories and individual small-group listings are not to be enumerated.
