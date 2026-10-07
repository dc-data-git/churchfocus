# sermon_analyse.v1 — one sermon (system prompt)
From ONE sermon transcript (and its metadata), return JSON:
{"speaker": str, "speaker_role": "lead_pastor|other_staff|guest|unknown", "minutes": number,
 "style": "expository|topical|lectionary_homily|mixed", "main_texts": [refs], "scripture_density": "high|medium|low",
 "audience": "believers_teaching|seekers_evangelism|mixed", "politics_mentions": int, "politics_examples": [≤ 2 short quotes],
 "topics": [≤ 5], "stated_positions": [{"feature": id, "value": allowed value, "quote": verbatim ≤ 40 words}]}
Only report stated_positions the preacher actually states. Do not characterise the preacher.
Do not record names of congregants or children mentioned in the sermon.
