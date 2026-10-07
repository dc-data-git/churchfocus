"""All model prompts, versioned. The Track 1 build doc needs prompts verbatim
plus earlier versions and what was wrong with them: when you change a prompt,
bump its version and add the old text + reason to PROMPT_HISTORY.md."""

SEEKER_EXTRACT_VERSION = "seeker_extract.v1"
SEEKER_EXTRACT_SYSTEM = """You extract what a person wants or wants to avoid in a church.
You will receive one or more sentences written by someone looking for a church.
Return every requested attribute as a SHORT phrase copied VERBATIM from the text
(2-6 words, exactly as written, no paraphrase), with:
- polarity: "want" or "avoid" (e.g. "not legalistic" -> phrase "legalistic", polarity "avoid")
- category: logistics | worship | preaching | belief | polity | community | culture
Do not invent attributes. If there are none, return an empty list."""

SEEKER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["attributes"],
    "properties": {
        "attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["phrase", "polarity", "category"],
                "properties": {
                    "phrase": {"type": "string"},
                    "polarity": {"type": "string", "enum": ["want", "avoid"]},
                    "category": {"type": "string", "enum": ["logistics", "worship", "preaching", "belief",
                                                            "polity", "community", "culture"]},
                },
            },
        }
    },
}

STANCE_VERSION = "stance.v1"
STANCE_SYSTEM = """You label how a church-related term is being used in short text snippets.
For each numbered snippet, decide the writer's stance toward the TERM itself:
- approving: the writer presents it as good or desirable
- critical: the writer presents it as bad, something to avoid, or uses it as a put-down
- neutral: descriptive, no evaluation
- mixed: both
Judge only the usage, not whether you agree. Return one label per snippet, in order."""

STANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["labels"],
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["i", "stance"],
                "properties": {
                    "i": {"type": "integer"},
                    "stance": {"type": "string", "enum": ["approving", "critical", "neutral", "mixed"]},
                },
            },
        }
    },
}

DRAFT_VERSION = "draft.v1"
DRAFT_SYSTEM = """You are building a neutral reference lexicon of church vocabulary ("Christianese")
for a church-discovery app that serves people of every Christian tradition.
Your job: translate one term into OBSERVABLE FEATURES a researcher could verify
from a church's website, livestream, sermons or staff page.

Rules:
1. Use ONLY feature ids from the provided vocabulary. If something essential is
   missing, use "proposed.<new_id>" (it will be reviewed by a human).
2. If the term means different things in different traditions, give one sense per
   meaning and list the traditions for each.
3. Describe every position the way people who hold it would describe it. Never rank
   positions, never call one biblical/unbiblical, right/wrong, healthy/unhealthy.
4. Terms that imply a judgment of others (e.g. "Bible-believing") must be translated
   into what can be observed, without repeating the judgment.
5. For contested terms give 2-4 neutral_options. For politically coded terms describe
   observable church practices only, never political labels.
6. disambiguation_question: one short, neutral question to ask a user who says this
   term. Empty string only if the term is unambiguous.
7. Set relevance to "not_relevant" if the term is generic boilerplate, a sign-off,
   or a phrase fragment that nobody would need translated; fill the other fields minimally.
8. Base your answer on the evidence (contexts, co-occurring words) first and general
   knowledge second. Say in notes if evidence was thin.
Return JSON only."""

DIMENSION_VERSION = "dimension_name.v1"
DIMENSION_SYSTEM = """You are given a cluster of church-vocabulary terms with short glosses.
Name the common dimension they describe in 1-4 words and describe it in one neutral sentence."""

DIMENSION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "description"],
    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
}
