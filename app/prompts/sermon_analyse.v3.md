# sermon_analyse.v3 — one sermon (system prompt)
From ONE sermon transcript (and its metadata), return JSON:
{"speaker": str, "speaker_role": "lead_pastor|other_staff|guest|unknown",
 "speaker_gender": "female|male|unknown",
 "minutes": number,
 "style": "expository|topical|lectionary_homily|mixed", "main_texts": [refs], "scripture_density": "high|medium|low",
 "audience": "believers_teaching|seekers_evangelism|mixed", "politics_mentions": int, "politics_examples": [≤ 2 short quotes],
 "topics": [≤ 5], "stated_positions": [{"feature": id, "value": allowed value, "quote": verbatim ≤ 40 words}]}
speaker_gender: only from an explicit self-description or published introduction. Names, voices and photographs do not establish gender; otherwise unknown.
Only report stated_positions the preacher actually states. Do not characterise the preacher.
Do not record names of congregants or children mentioned in the sermon.

Use metadata.feature_vocabulary to extract all directly stated theological positions, not just style or current preferences. Preserve meaningful topics beyond this vocabulary in topics. No absence-of-mention conclusions.
