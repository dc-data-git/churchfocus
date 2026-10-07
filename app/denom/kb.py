"""Denomination knowledge base access (INTERFACES §4). Reads denom-kb's denominations_kb.json.

Priors are what a denomination TYPICALLY holds; they are never facts about a particular church
(tier="prior", how="prior"). Free-text KB values are mapped to features.yaml values by a
deterministic rule table; anything the rules can't map confidently returns None (no prior).
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

from ..config import get_settings
from ..features import all_features, feature
from ..models import Evidence, PreferenceProfile

NON_CHRISTIAN = re.compile(r"\b(Muslim|Islam|Judaism|Jewish|Hindu|Buddhis|Baha'?i|Sikh|Jain|Zoroastrian|Taoist|Shinto|"
                           r"Unitarian|Vajarayana|Theravada|Mahayana|Yoga)", re.I)
NOT_APPLICABLE = re.compile(r"^\s*not applicable", re.I)
VARIES = re.compile(r"\b(varies|vary|not uniform|differ(s|ing)? (by|from|across)|no single|not explicitly stated)\b", re.I)

# (pattern, value) — first match wins. Order matters: negatives before positives.
RULES: dict[str, list[tuple[str, str]]] = {
    "women.senior_pastor": [(r"not permitted|reserved to men|men only|male only|restrict|exclu|not ordain|may not", "no"),
                            (r"permitted|affirm|may lead|serve as (lead |senior )?pastor|women serve as pastors|ordained", "yes")],
    "women.pastor_other": [(r"not permitted|reserved to men|men only|male only|restrict|exclu|may not|not ordain", "no"),
                           (r"permitted|affirm|ordain|may be commissioned", "yes")],
    "women.elder": [(r"not permitted|reserved to men|men only|male only|restrict|exclu|may not|not ordain", "no"),
                    (r"^permitted|affirm", "yes")],
    "women.preach": [(r"may not|not permitted|prohibit|reserved to men", "never"),
                     (r"preach|ordain|permitted", "occasionally")],
    "lgbtq.marriage": [(r"man and (a )?woman|one man|not recogni|oppos|contrary|unacceptable|sinful", "traditional"),
                       (r"authori[sz]ed|affirm|bless|permit|celebrat", "affirming")],
    "lgbtq.inclusion": [(r"^affirming of lgbtq|full inclusion|fully includ", "full")],
    "theology.baptism": [(r"infant", "infant_and_adult"), (r"believer|professing|accountable|confession of faith", "believers_immersion")],
    "theology.communion_view": [(r"transubstantia|becomes? the (true )?body", "real_presence_transformation"),
                                (r"true body|real presence|body and blood", "real_presence"),
                                (r"spiritual", "spiritual_presence"), (r"memorial|remembrance", "memorial")],
    "theology.gifts": [(r"cessation", "cessationist"), (r"^affirmed|continu|tongues", "continuationist_public")],
    "theology.spirit_baptism": [(r"initial (physical )?evidence|speaking in (other )?tongues", "required_evidence_tongues"),
                                (r"distinct from conversion|empowerment", "taught_not_required")],
    "theology.salvation_framework": [(r"reformed|calvin", "reformed"),
                                     (r"arminian|wesleyan|prevenient|grace-enabled|not strict unconditional", "arminian_wesleyan")],
    "theology.justification": [(r"faith alone|alone through faith|sola fide", "faith_alone"),
                               (r"sacrament|participation|theosis|cooperat", "sacramental_process")],
    "theology.scripture": [(r"inerran|^affirmed", "inerrant"), (r"infallib", "infallible"), (r"tradition", "tradition_and_scripture")],
    "theology.end_times": [(r"dispensation", "premillennial_dispensational"), (r"amillenn", "amillennial"), (r"postmillenn", "postmillennial")],
    "theology.creeds": [(r"confession|catechism|articles of religion|book of concord", "confessional"), (r"creed", "uses_creeds")],
    "polity.governance": [(r"episcopal|bishop", "episcopal_bishops"), (r"presbyter", "presbyterian_elders"), (r"congregational", "congregational_vote")],
    "worship.style": [(r"expressive|spirit-empowered|pentecostal|charismatic", "charismatic_expressive"),
                      (r"liturg|divine liturgy|\bmass\b|word and sacrament|rite", "liturgical_traditional"),
                      (r"a cappella|hymn", "traditional_hymns")],
    "worship.communion_frequency": [(r"weekly", "weekly"), (r"monthly", "monthly"), (r"quarterly|four times", "quarterly"),
                                    (r"annual|yearly", "occasional")],
    "social.abortion": [(r"right to (an )?abortion|should be legal|pro-choice|reproductive (rights|choice)", "pro_choice"),
                        (r"oppos|pro-life|sanctity of (human )?life", "pro_life")],
    "social.pacifism": [(r"peace[- ]church|nonresist|pacif|rejects war|way of peace", "pacifist"), (r"just war", "just_war")],
    "community.giving": [(r"tith|ten percent|10%", "tithe_expected")],
}
RULE_RE = {f: [(re.compile(p, re.I), v) for p, v in rs] for f, rs in RULES.items()}

VARIABILITY_FIELD = {"theology": "doctrinal", "social": "doctrinal", "lgbtq": "doctrinal",
                     "worship": "worship", "preaching": "worship", "polity": "governance", "women": "governance"}


def map_value(fid: str, text: str | None) -> str | None:
    """Free-text KB value -> allowed feature value, or None when not confidently mappable."""
    if not text or NOT_APPLICABLE.match(text):
        return None
    for rx, val in RULE_RE.get(fid, []):
        if rx.search(text):
            # "varies" language only blocks the mapping when no strong signal precedes it
            if VARIES.search(text) and rx.search(text).start() > VARIES.search(text).start():
                return None
            return val
    return None


def variability_number(text: str | None) -> float:
    if not text:
        return 0.3
    t = text.lower()
    if "low" in t:
        return 0.15
    if "high" in t:
        return 0.5
    return 0.3


GENERIC = {"church", "churches", "of", "the", "first", "second", "community", "fellowship", "ministries", "ministry",
           "center", "chapel", "congregation", "parish", "inc", "usa", "us", "america", "in", "and", "st", "saint", "christian"}
FAMILY_WORDS = ["lutheran", "baptist", "methodist", "presbyterian", "mennonite", "pentecostal", "episcopal", "nazarene",
                "brethren", "reformed", "orthodox", "catholic", "adventist", "wesleyan", "anglican", "apostolic", "holiness"]


def _strip_generic(t: str) -> str:
    return " ".join(w for w in re.findall(r"[a-z]+", t.lower()) if w not in GENERIC)


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"[;,]", v or "") if x.strip()]


class DenomKB:
    def __init__(self, path: str | Path, curated_path: str | Path | None = None):
        with open(path, encoding="utf-8") as f:
            self.data = json.load(f)
        self.groups = {g["id"]: g for g in self.data["groups"]}
        cp = Path(curated_path) if curated_path else Path(__file__).with_name("curated.yaml")
        self.curated = (yaml.safe_load(cp.read_text(encoding="utf-8")) or {}).get("groups", {}) if cp.exists() else {}
        self._names: list[tuple[str, str]] = []      # (lowercase name, group id) for fuzzy search
        self._aliases: list[tuple[str, str]] = []    # (alias as written, group id) for exact/word match
        for gid, g in self.groups.items():
            for n in {g["census_name"], g["name"]}:
                self._names.append((n.lower(), gid))
                self._aliases.append((n, gid))
            f = g["fields"]
            for key in ("identity.aliases", "identity.abbreviations", "identity.former_names"):
                for a in _split((f.get(key) or {}).get("value")):
                    self._aliases.append((a, gid))
            for a in self.curated.get(gid, {}).get("aliases", []):
                self._aliases.append((a, gid))

    # ---------------------------------------------------------------- lookup
    def is_christian(self, gid: str) -> bool:
        g = self.groups[gid]
        if gid in self.curated:
            return self.curated[gid].get("branch") != "non_christian"
        return not NON_CHRISTIAN.search(g["census_name"] + " " + g["name"])

    def _size_boost(self, gid: str) -> float:
        a = self.groups[gid]["census_2020"].get("adherents") or 0
        return min(5.0, math.log10(a + 1) / 1.6)

    def find(self, text: str, k: int = 5) -> list[dict]:
        """Name/alias search. Accepts a denomination name, an abbreviation, or a church's name.
        Scores: 100 exact name/alias/abbreviation; ~90-98 alias phrase inside the text; 85-90 fuzzy name match;
        70 family-word guess (e.g. "Trinity Lutheran Church" -> largest Lutheran bodies). Callers turn score into confidence."""
        text = (text or "").strip()
        if not text:
            return []
        scores: dict[str, float] = {}
        tokens = set(re.findall(r"[A-Za-z&]+", text))
        low = text.lower()
        for alias, gid in self._aliases:
            bare = alias.replace("&", "").replace("(", "").replace(")", "")
            if len(bare) <= 6 and bare.isupper():   # abbreviation: exact token only (avoid 'AG' in 'AGAPE')
                s = 100 if (alias == text or alias in tokens or ("(" in alias and alias in text)) else 0
            elif alias.lower() == low:
                s = 100
            elif re.search(r"(?<![A-Za-z])" + re.escape(alias.lower()) + r"(?![A-Za-z])", low):
                s = 88 + min(10, len(alias) / 3)
            else:
                s = 0
            if s:
                scores[gid] = max(scores.get(gid, 0), s)
        core = _strip_generic(low)
        if core:
            for name, sc, idx in process.extract(core, [_strip_generic(n) for n, _ in self._names],
                                                 scorer=fuzz.token_sort_ratio, limit=5):
                if sc >= 85 and len(name.split()) >= 2 and len(core.split()) >= 2:   # single words are too ambiguous
                    gid = self._names[idx][1]
                    scores[gid] = max(scores.get(gid, 0), sc * 0.9)
        if not scores:
            for word in FAMILY_WORDS:
                if re.search(rf"\b{word}", low):
                    fam = sorted((gid for gid, g in self.groups.items() if word in g["census_name"].lower()),
                                 key=lambda gid: -(self.groups[gid]["census_2020"].get("adherents") or 0))[:3]
                    for gid in fam:
                        scores[gid] = max(scores.get(gid, 0), 70.0)
        ranked = sorted(scores.items(), key=lambda kv: -(kv[1] + self._size_boost(kv[0])))
        return [{"id": gid, "name": self.groups[gid]["name"], "score": round(s, 1)} for gid, s in ranked[:k]]

    def get(self, denom_id: str) -> dict:
        g = self.groups[denom_id]
        known = {fid: {"value": v["value"], "status": v["status"],
                       "evidence": [{"quote": e.get("quote", ""), "url": e.get("url", "")} for e in v["evidence"][:2]]}
                 for fid, v in g["fields"].items() if v["value"] not in (None, "", "Unknown")}
        cur = self.curated.get(denom_id, {})
        return {"id": g["id"], "name": g["name"], "census_name": g["census_name"], "census_2020": g["census_2020"],
                "branch": cur.get("branch"), "tradition": cur.get("tradition"), "christian": self.is_christian(denom_id),
                "fields": known}

    # ---------------------------------------------------------------- priors
    def _sensitive_ok(self, entry: dict) -> bool:
        """Sensitive prior only for original workbook values or human-accepted fills (INTERFACES DenomKB.prior)."""
        for e in entry.get("evidence", []):
            if e.get("action") in ("fill", "conflict") and e.get("human_decision") != "accept":
                return False
        return True

    def prior(self, denom_id: str, feature_id: str) -> Evidence | None:
        meta = feature(feature_id)
        g = self.groups.get(denom_id)
        if not g:
            return None
        label = g["name"]
        if feature_id == "identity.denomination":
            value, src = denom_id, "denomination id"
        elif feature_id in ("identity.branch", "identity.tradition") and self.curated.get(denom_id, {}).get(feature_id.split(".")[1]):
            value, src = self.curated[denom_id][feature_id.split(".")[1]], "curated"
        else:
            df = meta.get("denom_field")
            entry = g["fields"].get(df) if df else None
            if not entry or not entry.get("value"):
                return None
            if meta.get("sensitive") and not self._sensitive_ok(entry):
                return None
            value = map_value(feature_id, entry["value"])
            if value is None:
                return None
            src = entry["value"]
        ev0 = (g["fields"].get(meta.get("denom_field") or "", {}) or {}).get("evidence") or [{}]
        return Evidence(feature=feature_id, value=value, tier="prior", how="prior", source_kind="denomination_kb",
                        quote=(ev0[0].get("quote") or "")[:300], url=ev0[0].get("url") or "",
                        note=f"typical for {label}: {src}"[:300])

    def variability(self, denom_id: str, feature_id: str) -> float:
        g = self.groups.get(denom_id)
        if not g:
            return 0.3
        kind = VARIABILITY_FIELD.get(feature_id.split(".")[0], "cultural")
        return variability_number((g["fields"].get(f"distinguishing_metadata.{kind}_variability") or {}).get("value"))

    def compare(self, a: str, b: str, features: list[str]) -> list[dict]:
        out = []
        for fid in features:
            pa, pb = self.prior(a, fid), self.prior(b, fid)
            out.append({"feature": fid, "label": feature(fid)["label"], a: pa.value if pa else None, b: pb.value if pb else None,
                        "same": (pa.value == pb.value) if pa and pb else None})
        return out

    def match_profile(self, profile: PreferenceProfile, k: int = 8) -> list[dict]:
        """Likely denominations for a profile: priors scored with the same matcher used for churches."""
        from ..match import score_evidence
        res = []
        for gid in self.groups:
            if not self.is_christian(gid):
                continue
            ev = {p.feature: self.prior(gid, p.feature) for p in profile.preferences if p.weight != "dont_care"}
            r = score_evidence(gid, profile, {f: e for f, e in ev.items() if e}, denom_conf=1.0,
                               variability=lambda f, gid=gid: self.variability(gid, f), denom_label=self.groups[gid]["name"])
            res.append({"id": gid, "name": self.groups[gid]["name"], "score": round(r.score + self._size_boost(gid) / 5, 1),
                        "excluded": r.excluded, "why": r.why})
        res.sort(key=lambda x: (x["excluded"], -x["score"]))
        return res[:k]


@lru_cache
def get_kb() -> DenomKB:
    return DenomKB(get_settings().denom_kb_path)


def priors_available() -> dict[str, int]:
    """Diagnostic: how many groups yield a prior per feature (for EVAL / report)."""
    kb = get_kb()
    return {fid: sum(1 for gid in kb.groups if kb.prior(gid, fid)) for fid in all_features()}
