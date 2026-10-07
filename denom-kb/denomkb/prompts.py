"""Versioned prompts. When you change one: copy the old text into PROMPT_HISTORY.md with what
was wrong, bump the version, and re-run (cached calls for unchanged prompts are reused)."""

MATCH_VERSION = "match.v2"
MATCH_SYSTEM = """You check whether an encyclopedia article is a usable source about a specific US religious group.
The group comes from the 2020 US Religion Census. Answer "yes" if the article is about:
- this exact body, or the same organization under another name, or
- the single worldwide church or communion of which this census group is simply the US part
  (e.g. census "Catholic Church" and the article "Catholic Church"; census "Greek Orthodox
  Archdiocese of America" and an article on the Greek Orthodox Church), because its doctrine is the same.
Answer "no" if it is about a different body with a similar name, a broad family of many separate
denominations (e.g. "Baptists" for one specific Baptist convention), or a concept. Otherwise "unsure"."""
MATCH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["same_group", "reason"],
    "properties": {"same_group": {"type": "string", "enum": ["yes", "no", "unsure"]}, "reason": {"type": "string"}},
}

CLASSIFY_VERSION = "classify.v1"
CLASSIFY_SYSTEM = """Using ONLY the excerpt, say whether this religious group identifies as Christian.
Answer "yes", "no", or "unclear", and copy a short verbatim quote (under 30 words) from the excerpt
that supports the answer."""
CLASSIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["is_christian", "quote"],
    "properties": {"is_christian": {"type": "string", "enum": ["yes", "no", "unclear"]}, "quote": {"type": "string"}},
}

EXTRACT_VERSION = "extract.v2"
EXTRACT_SYSTEM = """You fill a neutral reference database about US religious groups.
Use ONLY the numbered source excerpts provided. Never use outside knowledge, even if you are sure.

For each requested field:
- If the excerpts state it: status "stated", a short value (at most 20 words, neutral, describing the
  group the way the group describes itself), the excerpt number, and a
  supporting quote copied EXACTLY from that excerpt (8-40 words, no ellipses, no paraphrase).
- If the excerpts do not clearly state it: status "not_stated" with empty value, quote and excerpt 0.
Do not guess. Do not infer a position from silence. Interpretive summaries must start with "Interpretation:".
Return one entry for every requested field id, using the ids exactly as given."""
EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["fields"],
    "properties": {"fields": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["field_id", "status", "value", "excerpt", "quote"],
        "properties": {
            "field_id": {"type": "string"},
            "status": {"type": "string", "enum": ["stated", "not_stated"]},
            "value": {"type": "string"},
            "excerpt": {"type": "integer"},
            "quote": {"type": "string"},
        }}}},
}

VALIDATE_VERSION = "validate.v1"
VALIDATE_SYSTEM = """You check existing database entries about a US religious group against source excerpts.
Use ONLY the numbered excerpts. For each entry decide:
- "supported": an excerpt states the same thing (wording may differ)
- "contradicted": an excerpt clearly states something incompatible; give the corrected short value
- "not_addressed": the excerpts do not settle it
For supported/contradicted, give the excerpt number and a quote copied EXACTLY from it (8-40 words).
Be conservative: differences of wording or detail are NOT contradictions.
Return one entry for every field id given."""
VALIDATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["fields"],
    "properties": {"fields": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["field_id", "verdict", "corrected_value", "excerpt", "quote"],
        "properties": {
            "field_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["supported", "contradicted", "not_addressed"]},
            "corrected_value": {"type": "string"},
            "excerpt": {"type": "integer"},
            "quote": {"type": "string"},
        }}}},
}

VERIFY_VERSION = "verify.v1"
VERIFY_SYSTEM = """You audit claims in a reference database about US religious groups.
Each claim says: for GROUP, the FIELD has VALUE, and gives a QUOTE as evidence.
Judge ONLY whether the quote itself supports the value for that field. Ignore what you
know from elsewhere; a true value with an irrelevant quote is NOT supported.
- "supported": the quote clearly states the value (wording may differ, meaning must match)
- "partial": the quote is related but the value adds, overstates or narrows something
- "unsupported": the quote does not state the value, is about something else, or contradicts it
Give a short reason (under 20 words). Return one verdict per claim id."""
VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "verdict", "reason"],
        "properties": {"id": {"type": "integer"},
                       "verdict": {"type": "string", "enum": ["supported", "partial", "unsupported"]},
                       "reason": {"type": "string"}}}}},
}

VERSIONS = [MATCH_VERSION, CLASSIFY_VERSION, EXTRACT_VERSION, VALIDATE_VERSION, VERIFY_VERSION]
