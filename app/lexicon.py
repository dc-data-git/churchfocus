"""Christianese lexicon for the interview (D34, P11): church jargon -> soft feature hints.

Source: christianese-lexicon/work_podcasts/out/lexicon.json (first draft, features.v1 ids), mapped to features.v3,
filtered for relevance, plus a small hand glossary for common phrases the draft misses.
Hints are suggestions for the conversation model, never facts; every use is logged with the term (why='lexicon:<term>').
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from app.config import ROOT
from app.features import all_features

LEXICON_PATH = ROOT / "christianese-lexicon" / "work_podcasts" / "out" / "lexicon.json"

# features.v1 id -> (v3 id, {v1 value: v3 value}) ; None = dropped (cut by D8 or no v3 equivalent)
V1_TO_V3: dict[str, tuple[str, dict[str, str]] | None] = {
    "belief.scripture_authority": ("theology.scripture", {"inerrant": "inerrant", "infallible": "infallible", "inspired": "inspired_authoritative"}),
    "belief.soteriology": ("theology.salvation_framework", {"reformed": "reformed", "arminian": "arminian_wesleyan", "mixed": "mixed"}),
    "worship.charismatic_gifts": ("theology.gifts", {"public": "continuationist_public", "private_only": "continuationist_private", "absent": "cessationist"}),
    "belief.sacramental_view": ("theology.communion_view", {"sacramental": "real_presence", "memorial": "memorial"}),
    "belief.creeds": ("theology.creeds", {"yes": "uses_creeds", "no": "no_creed_but_bible"}),
    "belief.end_times": ("theology.end_times", {"dispensational": "premillennial_dispensational"}),
    "belief.baptism": ("theology.baptism", {"believers_immersion": "believers_immersion", "infant": "infant_and_adult", "both": "either"}),
    "belief.lgbtq_policy": ("lgbtq.marriage", {"affirming": "affirming", "non_affirming": "traditional"}),
    "belief.women_leadership": ("women.senior_pastor", {"all_roles": "yes", "distinct_roles": "no"}),
    "preaching.speakers_women": ("women.preach", {"regularly": "regularly", "occasionally": "occasionally", "never": "never"}),
    "community.youth_ministry": ("community.youth", {"present": "present", "absent": "absent"}),
    "community.kids_ministry": ("community.kids", {"present": "present", "absent": "absent"}),
    "worship.music.repertoire": ("worship.music_sources", {"modern_worship": "modern_worship_labels", "hymns": "hymns", "mixed": "mixed",
                                                            "gospel": "gospel", "chant": "chant", "psalms_only": "psalms"}),
    "worship.music.band": ("worship.style", {"present": "contemporary"}),
    "worship.music.organ": ("worship.instruments", {"present": "organ"}),
    "worship.music.choir": ("worship.choir", {"present": "present", "absent": "absent"}),
    "logistics.service_time": ("logistics.service_times", {}),
    "community.age_mix": None, "community.ethnic_mix": None, "worship.dress_norm": None,   # cut (D8)
    "worship.lyrics_display": None, "worship.vestments": None, "worship.lectionary": None, "worship.testimony": None,
    "polity.affiliation": None, "preaching.guest_frequency": None,
}

GLOSSARY: dict[str, tuple[str, list[tuple[str, str]]]] = {
    # phrase: (gloss, [(feature, value), ...])
    "bible-believing": ("Usually signals a high view of scripture (often inerrancy), typically conservative evangelical.",
                        [("theology.scripture", "inerrant"), ("identity.branch", "evangelical_protestant")]),
    "bible believing": ("Usually signals a high view of scripture, typically conservative evangelical.", [("theology.scripture", "inerrant")]),
    "high church": ("Formal, liturgical worship (set prayers, vestments, sacraments).", [("worship.liturgy", "high"), ("worship.style", "liturgical_traditional")]),
    "smells and bells": ("High-church liturgical worship.", [("worship.liturgy", "high")]),
    "seeker sensitive": ("Services designed for non-Christians/newcomers.", [("preaching.audience", "seekers_evangelism")]),
    "seeker-sensitive": ("Services designed for non-Christians/newcomers.", [("preaching.audience", "seekers_evangelism")]),
    "verse by verse": ("Expository preaching through books of the Bible.", [("preaching.style", "expository")]),
    "expository": ("Preaching that works through a Bible passage.", [("preaching.style", "expository")]),
    "egalitarian": ("Women may hold any role, including lead pastor.", [("women.senior_pastor", "yes")]),
    "complementarian": ("Pastoral/elder roles reserved to men.", [("women.senior_pastor", "no"), ("women.elder", "no")]),
    "open and affirming": ("Fully LGBTQ-inclusive congregation (UCC term).", [("lgbtq.inclusion", "full"), ("lgbtq.marriage", "affirming")]),
    "reconciling": ("LGBTQ-inclusive congregation (Methodist term).", [("lgbtq.inclusion", "full")]),
    "megachurch": ("Very large church (2,000+).", [("logistics.size", "mega")]),
    "family feel": ("Small church where people know each other.", [("logistics.size", "small")]),
    "hymns": ("Traditional hymn singing.", [("worship.style", "traditional_hymns"), ("worship.music_sources", "hymns")]),
    "praise band": ("Band-led contemporary worship.", [("worship.style", "contemporary")]),
    "worship band": ("Band-led contemporary worship.", [("worship.style", "contemporary")]),
    "latin mass": ("Traditional Catholic liturgy in Latin.", [("worship.style", "liturgical_traditional"), ("worship.liturgy", "high")]),
    "bible study": ("Midweek study groups.", [("community.small_groups", "present")]),
    "small group": ("Midweek small groups.", [("community.small_groups", "present")]),
    "young adults": ("A college/young-adults ministry.", [("community.young_adults", "present")]),
    "college students": ("A college/young-adults ministry.", [("community.young_adults", "present")]),
    "youth group": ("A youth ministry for teenagers.", [("community.youth", "present")]),
    "contemporary": ("Modern worship music, usually band-led.", [("worship.style", "contemporary")]),
    "traditional": ("Traditional service: hymns, piano/organ, set order.", [("worship.style", "traditional_hymns")]),
    "liturgical": ("Set liturgy: creeds, responsive readings, set prayers.", [("worship.liturgy", "high"), ("worship.style", "liturgical_traditional")]),
    "speaking in tongues": ("Charismatic gifts practiced openly.", [("theology.gifts", "continuationist_public")]),
}

GENERIC = {"anybody", "asking", "abraham", "acts", "amen", "anxiety", "fear", "hearts", "heaven", "humanity", "fathers",
           "brothers", "bless", "blessing", "commandments", "corinthians", "demons", "divine", "empire", "forgiveness",
           "god's", "grace", "holy", "honor", "doctrine", "christianity", "congregation", "congregations", "church family",
           "spirit", "holy spirit", "jesus", "christ", "god", "bible", "worship", "church", "faith", "prayer", "love", "gospel",
           "disciple", "disciples", "deeper", "authentic", "community", "fellowship"}


PLACEHOLDERS = {"miles", "schedule", "language_list", "ministry_list", "topic_list", "denomination_id", "network_name",
                "names_roles_tenure", "change_list", "report_list", "stated_position_text"}


def _ok(fid: str, val: str) -> bool:
    feats = all_features()
    return fid in feats and val not in PLACEHOLDERS and val in {str(v) for v in feats[fid]["values"]}


@lru_cache
def entries() -> list[dict]:
    """[{term, aliases, gloss, features:[(fid, val)], source}] usable with features.v3."""
    out: list[dict] = []
    for phrase, (gloss, feats) in GLOSSARY.items():
        feats = [(f, v) for f, v in feats if _ok(f, v)]
        if feats:
            out.append({"term": phrase, "aliases": [], "gloss": gloss, "features": feats, "source": "glossary"})
    have = {e["term"] for e in out}
    try:
        raw = json.loads(LEXICON_PATH.read_text(encoding="utf-8")).get("entries", [])
    except (OSError, ValueError):
        raw = []
    for e in raw:
        term = (e.get("term") or "").strip().lower()
        if not term or term in have or term in GENERIC or e.get("relevance") not in (None, "church_vocabulary"):
            continue
        for sense in e.get("senses", [])[:2]:
            feats = []
            for f in sense.get("features", []):
                fid, val = f.get("feature", ""), str(f.get("value", ""))
                if fid in all_features():
                    if _ok(fid, val):
                        feats.append((fid, val))
                    continue
                m = V1_TO_V3.get(fid)
                if m and m[1].get(val) and _ok(m[0], m[1][val]):
                    feats.append((m[0], m[1][val]))
            if feats:
                out.append({"term": term, "aliases": [a.lower() for a in e.get("aliases", [])], "gloss": sense.get("gloss", "")[:200],
                            "features": feats, "source": "lexicon"})
                break
    return out


def hints(text: str, limit: int = 8) -> list[dict]:
    """Terms found in the person's message, with their likely meaning in feature terms."""
    low = (text or "").lower()
    found, seen = [], set()
    for e in sorted(entries(), key=lambda e: -len(e["term"])):   # longest phrases first
        for t in [e["term"], *e["aliases"]]:
            if t and t not in seen and re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
                found.append(e)
                seen.add(e["term"])
                break
        if len(found) >= limit:
            break
    return found
