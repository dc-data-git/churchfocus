"""Stage 0 conversation state machine (INTERFACES §4, ARCHITECTURE §4)."""
from __future__ import annotations

import re
from typing import Any, Callable, Literal

from app import db, llm
from app.denom.kb import get_kb
from app.features import feature, stage0_questions
from app.models import Escalation, Preference, PreferenceProfile, Weight

CompleteJson = Callable[..., dict]

# Women ladder rungs (a)–(g); (a) is baseline with no mapped feature.
LADDER_RUNGS: list[tuple[str, str | None]] = [
    ("a", None),
    ("b", "women.deacon"),
    ("c", "women.teach_mixed_adults"),
    ("d", "women.preach"),
    ("e", "women.pastor_other"),
    ("f", "women.elder"),
    ("g", "women.senior_pastor"),
]
LADDER_FEATURES = [fid for _, fid in LADDER_RUNGS if fid]

# Hard triggers: self-harm / immediate danger only. Grief, past abuse, "my mom died" etc. go to the LLM check
# (crisis_check.v2), because they are common, legitimate reasons people look for a church (R11).
CRISIS_KEYWORDS = re.compile(
    r"\b(suicide|suicidal|kill myself|killing myself|self[- ]harm|hurt myself|hurting myself|"
    r"don'?t want to live|end my life|cutting myself|overdos(e|ing)|in danger right now)\b",
    re.I,
)
CRISIS_MESSAGE = (
    "It sounds like you may be going through something very difficult right now. "
    "ChurchFocus can't provide crisis or pastoral care, but help is available. "
    "Please call or text 988 (Suicide & Crisis Lifeline) or reach out to someone you trust. "
    "If you are in immediate danger, call 911."
)

WEIGHT_OPTIONS = ["Must-have", "Important", "Nice to have", "Doesn't matter"]
WEIGHT_MAP: dict[str, Weight] = {
    "must-have": "dealbreaker",
    "must have": "dealbreaker",
    "dealbreaker": "dealbreaker",
    "important": "important",
    "nice to have": "nice_to_have",
    "nice-to-have": "nice_to_have",
    "doesn't matter": "dont_care",
    "doesnt matter": "dont_care",
    "don't care": "dont_care",
    "dont care": "dont_care",
    "prefer not to say": "dont_care",
}

CORE_STEPS = ["location", "for_whom", "identity.branch", "worship.style", "women_ladder"]
WOMEN_LADDER_IDS = {"women_ladder", "women_ladder_followup"}

INTERVIEWER_SCHEMA = {
    "type": "object",
    "required": ["say", "options", "updates", "raised", "skip_rest"],
    "properties": {
        "say": {"type": "string"},
        "options": {"type": "array"},
        "updates": {"type": "array"},
        "raised": {"type": "array"},
        "skip_rest": {"type": "boolean"},
    },
}
CRISIS_SCHEMA = {
    "type": "object",
    "required": ["escalate", "kind", "reason"],
    "properties": {
        "escalate": {"type": "boolean"},
        "kind": {"type": "string"},
        "reason": {"type": "string"},
    },
}
READBACK_SCHEMA = {
    "type": "object",
    "required": ["text"],
    "properties": {"text": {"type": "string"}},
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _parse_weight(text: str) -> Weight | None:
    t = _norm(text)
    for key, w in WEIGHT_MAP.items():
        if key in t:
            return w
    return None


def _parse_for_whom(text: str) -> Literal["self", "other"] | None:
    t = _norm(text)
    if any(x in t for x in ("someone else", "for another", "helping someone", "for a friend", "for my")):
        return "other"
    if any(x in t for x in ("myself", "for me", "for myself", "just me", "i am looking")):
        return "self"
    if t in ("self", "other"):
        return t  # type: ignore[return-value]
    return None


def _parse_ladder_rung(text: str) -> str | None:
    t = _norm(text)
    if "no preference" in t or "doesn't matter" in t or "doesnt matter" in t:
        return "none"
    m = re.search(r"\(([a-g])\)", t)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-g]\.?", t):          # bare letter only when it is the whole answer (R2: "a church..." != rung a)
        return t[0]
    labels = {
        "deacon": "b",
        "deacons": "b",
        "teach": "c",
        "mixed adult": "c",
        "preach": "d",
        "preaching": "d",
        "associate": "e",
        "other pastor": "e",
        "elder": "f",
        "elders": "f",
        "lead pastor": "g",
        "senior pastor": "g",
    }
    best = None
    for key, letter in labels.items():   # highest rung mentioned wins ("teach and preach" -> d)
        if key in t and (best is None or letter > best):
            best = letter
    if best:
        return best
    if "kids" in t or "children" in t or "women's ministr" in t:
        return "a"
    return None


def _parse_ladder_mode(text: str) -> Literal["minimum", "maximum", "both"] | None:
    t = _norm(text)
    if "both" in t:
        return "both"
    if "minimum" in t or "need to see" in t:
        return "minimum"
    if "maximum" in t or "as far as" in t or "comfortable going" in t:
        return "maximum"
    return None


def _apply_ladder(rung: str, mode: Literal["minimum", "maximum", "both"] | None) -> list[Preference]:
    """Map ladder rung + follow-up to women.* preferences (weights asked separately)."""
    if rung == "none" or mode is None:
        return [Preference(feature=fid, want=[], weight="dont_care", said="no preference") for fid in LADDER_FEATURES]

    idx = next(i for i, (letter, _) in enumerate(LADDER_RUNGS) if letter == rung)
    prefs: list[Preference] = []
    for i, (_, fid) in enumerate(LADDER_RUNGS):
        if not fid:
            continue
        rung_i = i
        if rung_i <= idx:
            if mode in ("minimum", "both"):
                want = ["regularly", "occasionally"] if fid == "women.preach" else ["yes"]
                prefs.append(Preference(feature=fid, want=want, weight="important", said=f"rung {rung} {mode}"))
            else:
                prefs.append(Preference(feature=fid, want=[], weight="dont_care", said=f"rung {rung} {mode}"))
        else:
            if mode in ("maximum", "both"):
                no = ["never"] if fid == "women.preach" else ["no"]   # women.preach values: regularly/occasionally/never
                prefs.append(Preference(feature=fid, want=no, weight="important", said=f"rung {rung} {mode}"))
            else:
                prefs.append(Preference(feature=fid, want=[], weight="dont_care", said=f"rung {rung} {mode}"))
    return prefs


_PLACEHOLDER_VALUES = {"miles", "schedule", "language_list", "ministry_list", "topic_list", "denomination_id", "network_name",
                       "names_roles_tenure", "change_list", "report_list", "stated_position_text"}


def _enumerable(meta: dict) -> bool:
    vals = meta.get("values") or []
    return bool(vals) and not (set(vals) & _PLACEHOLDER_VALUES) and len(vals) <= 12


def _skip_women_duplicates(questions: list[dict]) -> list[dict]:
    """Only women.senior_pastor carries the ladder question; other women.* are filled by mapping."""
    seen_ladder = False
    out: list[dict] = []
    for q in questions:
        fid = q["id"]
        if fid.startswith("women.") and fid != "women.senior_pastor":
            continue
        if fid == "women.senior_pastor":
            if seen_ladder:
                continue
            seen_ladder = True
        out.append(q)
    return out


class Interviewer:
    def __init__(self, session_id: str, *, complete_json_fn: CompleteJson | None = None):
        self.session_id = session_id
        self._llm = complete_json_fn or llm.complete_json
        self._phase: Literal["core", "standard", "if_raised", "advanced", "readback", "change", "done", "escalated"] = "core"
        self._core_idx = 0
        self._std_idx = 0
        self._adv_idx = 0
        self._if_raised: list[str] = []
        self._if_raised_idx = 0
        self._skip_rest = False
        self._advanced_opt_in: bool | None = None
        self._pending_weight: str | None = None
        self._pending_pref: Preference | None = None
        self._ladder_rung: str | None = None
        self._escalation: Escalation | None = None
        self._profile = PreferenceProfile(session_id=session_id)
        self._answered: set[str] = set()
        self._standard_qs = _skip_women_duplicates(stage0_questions("standard"))
        self._advanced_qs = stage0_questions("advanced")
        self._last_say = ""
        self._pending_ladder_weight = False

    def profile(self) -> PreferenceProfile:
        return self._profile.model_copy(deep=True)

    def readback(self) -> str:
        matches = get_kb().match_profile(self._profile, k=4)
        self._profile.likely_denominations = [m["id"] for m in matches]
        denom_lines = [f"{m['name']} ({m['score']}% fit)" for m in matches[:4]]
        prompt = llm.load_prompt("readback.v1")
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    f"Profile: {self._profile.model_dump_json()}\n"
                    f"Likely denominations: {denom_lines}\n"
                    "Write the read-back."
                ),
            },
        ]
        try:
            out = self._llm("readback", messages, READBACK_SCHEMA, tier="strong")
            return out["text"]
        except Exception:
            return self._fallback_readback(matches)

    def _fallback_readback(self, matches: list[dict]) -> str:
        lines = ["Here's what I heard:"]
        for p in self._profile.preferences:
            if p.weight == "dont_care" or not p.want:
                continue
            lines.append(f"- {feature(p.feature)['label']}: {', '.join(p.want)} ({p.weight.replace('_', ' ')})")
        if matches:
            lines.append("Likely traditions: " + ", ".join(m["name"] for m in matches[:4]))
        lines.append("Did I get that right? You can change anything.")
        return " ".join(lines)

    def next_turn(self, user_text: str | None) -> dict:
        if self._phase == "escalated":
            return {"say": self._escalation.message if self._escalation else CRISIS_MESSAGE, "options": None, "done": True, "escalation": self._escalation}
        if self._phase == "done":
            return {"say": "Your profile is saved. Ready to search when you are.", "options": None, "done": True, "escalation": None}

        if user_text is not None:
            if self._check_crisis(user_text):
                self._phase = "escalated"
                return {"say": self._escalation.message, "options": None, "done": True, "escalation": self._escalation}
            self._handle_input(user_text)

        # Confirm / crisis may finish the interview inside _handle_input.
        if self._phase == "escalated":
            return {"say": self._escalation.message if self._escalation else CRISIS_MESSAGE, "options": None, "done": True, "escalation": self._escalation}
        if self._phase == "done":
            return {"say": "Your profile is saved. Ready to search when you are.", "options": None, "done": True, "escalation": None}

        if self._pending_ladder_weight:
            return {"say": "How important are women's roles in ministry to you when choosing a church?",
                    "options": WEIGHT_OPTIONS, "done": False, "escalation": None}
        if self._pending_weight:
            return self._ask_weight()
        if self._phase == "change":
            return {"say": "What would you like to change? Tell me in your own words.", "options": None, "done": False, "escalation": None}

        step = self._current_step()
        if step is None:
            self._phase = "readback"
            text = self.readback()
            self._last_say = text
            return {"say": text, "options": ["Yes, that's right", "I need to change something"], "done": False, "escalation": None}

        if step == "readback_confirm":
            if not self._last_say:
                self._last_say = self.readback()
            return {"say": self._last_say, "options": ["Yes, that's right", "I need to change something"], "done": False, "escalation": None}

        question = self._question_for(step)
        return self._present(question)

    def _check_crisis(self, text: str) -> bool:
        escalate = bool(CRISIS_KEYWORDS.search(text))
        if not escalate:
            try:
                out = self._llm(
                    "crisis_check",
                    [
                        {"role": "system", "content": llm.load_prompt("crisis_check.v2")},
                        {"role": "user", "content": text},
                    ],
                    CRISIS_SCHEMA,
                    tier="fast",
                )
                escalate = bool(out.get("escalate"))
            except Exception:
                escalate = False
        if escalate:
            self._escalation = Escalation(reason="pastoral_or_crisis", message=CRISIS_MESSAGE, questions_to_ask=[])
        return escalate

    def _handle_input(self, text: str) -> None:
        if _norm(text) in ("skip the rest", "skip rest", "skip"):
            self._skip_rest = True
            self._pending_weight = None
            self._pending_pref = None
            return

        if self._phase == "readback":
            t = _norm(text)
            if t.startswith("yes") or t in ("confirm", "looks good", "correct"):
                self._profile.confirmed = True
                matches = get_kb().match_profile(self._profile, k=8)
                self._profile.likely_denominations = [m["id"] for m in matches]
                db.save_profile(self._profile)
                self._phase = "done"
            elif "change" in t or t.startswith("no"):
                self._phase = "change"          # R2: ask what to change instead of repeating the read-back
            else:
                self._apply_change(text)        # they typed the correction directly
                self._last_say = ""
            return

        if self._phase == "change":
            self._apply_change(text)
            self._phase = "readback"
            self._last_say = ""                 # regenerate the read-back with the change
            return

        if self._pending_ladder_weight:
            w = _parse_weight(text)
            if w:
                for p in list(self._profile.preferences):
                    if p.feature in LADDER_FEATURES and p.want:
                        self._upsert_pref(p.model_copy(update={"weight": w}))
                self._pending_ladder_weight = False
                self._advance()
            return

        if self._pending_weight and self._pending_pref:
            w = _parse_weight(text)
            if w:
                pref = self._pending_pref.model_copy(update={"weight": w})
                self._upsert_pref(pref)
                self._answered.add(pref.feature)    # R2: mark answered and move on (was an infinite loop)
                self._pending_weight = None
                self._pending_pref = None
                self._advance()
            return

        step = self._step_before_advance()
        if step == "location":
            self._profile.origin = {"text": text.strip()}
            m = re.search(r"(\d+)\s*miles?", text, re.I)
            if m:
                self._profile.max_miles = float(m.group(1))
            self._advance()
        elif step == "for_whom":
            whom = _parse_for_whom(text)
            if whom:
                self._profile.for_whom = whom
                self._advance()
        elif step == "women_ladder":
            rung = _parse_ladder_rung(text)
            if rung:
                self._ladder_rung = rung
                if rung == "none":
                    for pref in _apply_ladder("none", None):
                        self._upsert_pref(pref)
                    self._answered.update(LADDER_FEATURES)
                    self._advance()
        elif step == "women_ladder_followup":
            mode = _parse_ladder_mode(text)
            if mode and self._ladder_rung:
                for pref in _apply_ladder(self._ladder_rung, mode):
                    self._upsert_pref(pref)
                self._answered.update(LADDER_FEATURES)
                self._ladder_rung = None
                self._pending_ladder_weight = True   # weight asked once for the whole ladder (features.yaml note)
        elif step == "advanced_opt_in":
            t = _norm(text)
            if t.startswith("y") or "yes" in t:
                self._advanced_opt_in = True
                self._adv_idx = 0
            else:
                self._advanced_opt_in = False
            self._advance()
        elif step and step not in WOMEN_LADDER_IDS:
            self._apply_feature_answer(step, text)

    def _apply_feature_answer(self, fid: str, text: str) -> None:
        t = _norm(text)
        if "prefer not to say" in t:
            self._upsert_pref(Preference(feature=fid, want=[], weight="dont_care", said=text))
            self._answered.add(fid)
            self._advance()
            if fid == "lgbtq.marriage":
                self._ensure_lgbtq_inclusion_next()
            return

        meta = feature(fid)
        want: list[str] = []
        for val in meta.get("values", []):
            if isinstance(val, str) and val.replace("_", " ") in t.replace("_", " "):
                want.append(val)
        if not want:
            for val in meta.get("values", []):
                if isinstance(val, str) and val.replace("_", "-") in t:
                    want.append(val)
        if not want and fid == "lgbtq.marriage":
            if "affirm" in t:
                want = ["affirming"]
            elif "traditional" in t or "man and a woman" in t or "man and woman" in t:
                want = ["traditional"]
        if not want and fid == "lgbtq.inclusion":
            if "full" in t and "leadership" in t:
                want = ["full"]
            elif "membership" in t and "leadership not" in t:
                want = ["membership_not_leadership"]
            elif "welcome" in t:
                want = ["welcome_not_membership"]

        if not want and _parse_weight(text) != "dont_care":
            want = self._map_values(fid, text)   # free text ("a band with lights") -> allowed values via the model
        weight = _parse_weight(text) or "important"
        pref = Preference(feature=fid, want=want, weight=weight, said=text)
        if weight == "dealbreaker" and "must" not in t and "dealbreaker" not in t:
            self._pending_weight = fid
            self._pending_pref = pref.model_copy(update={"weight": "important"})
            return
        self._upsert_pref(pref)
        self._maybe_ask_weight(fid, pref)
        if not self._pending_weight:
            self._answered.add(fid)
            self._advance()

    def _maybe_ask_weight(self, fid: str, pref: Preference) -> None:
        if self._phase == "core":
            return
        if pref.weight != "important":
            return
        if any(k in _norm(pref.said) for k in ("must-have", "must have", "important", "nice to have", "doesn't matter")):
            return
        self._pending_weight = fid
        self._pending_pref = pref

    def _ask_weight(self) -> dict:
        label = feature(self._pending_weight)["label"] if self._pending_weight else "that"
        say = f"How important is {label} for you?"
        return {"say": say, "options": WEIGHT_OPTIONS, "done": False, "escalation": None}

    def _upsert_pref(self, pref: Preference) -> None:
        self._profile.preferences = [p for p in self._profile.preferences if p.feature != pref.feature]
        self._profile.preferences.append(pref)

    def _ensure_lgbtq_inclusion_next(self) -> None:
        """Marriage and inclusion are two separate questions — never skip inclusion after marriage."""
        if "lgbtq.inclusion" in self._answered:
            return
        for i, q in enumerate(self._standard_qs):
            if q["id"] == "lgbtq.inclusion":
                self._std_idx = i
                return

    def _step_before_advance(self) -> str | None:
        return self._current_step()

    def _current_step(self) -> str | None:
        if self._phase == "readback":
            return "readback_confirm"
        if self._skip_rest and self._phase in ("standard", "if_raised", "advanced"):
            return None
        if self._phase == "core":
            if self._core_idx >= len(CORE_STEPS):
                self._phase = "standard"
                return self._current_step()
            step = CORE_STEPS[self._core_idx]
            if step == "women_ladder" and self._ladder_rung and self._ladder_rung != "none":
                return "women_ladder_followup"
            return step
        if self._phase == "standard":
            if self._skip_rest:
                return self._finish_standard()
            while self._std_idx < len(self._standard_qs):
                fid = self._standard_qs[self._std_idx]["id"]
                if fid in self._answered or self._should_skip(fid):
                    self._std_idx += 1
                    continue
                return fid
            return self._finish_standard()
        if self._phase == "if_raised":
            while self._if_raised_idx < len(self._if_raised):
                fid = self._if_raised[self._if_raised_idx]
                if fid not in self._answered:
                    return fid
                self._if_raised_idx += 1
            self._phase = "advanced"
            return self._current_step()
        if self._phase == "advanced":
            if self._advanced_opt_in is None:
                return "advanced_opt_in"
            if not self._advanced_opt_in:
                return None
            while self._adv_idx < len(self._advanced_qs):
                fid = self._advanced_qs[self._adv_idx]["id"]
                if fid in self._answered:
                    self._adv_idx += 1
                    continue
                return fid
            return None
        return None

    def _finish_standard(self) -> str | None:
        if self._skip_rest:
            return None
        if self._if_raised:
            self._phase = "if_raised"
        else:
            self._phase = "advanced"
        return self._current_step()

    def _should_skip(self, fid: str) -> bool:
        # For-others mode: never ask orientation of the person being helped.
        # Marriage/inclusion are about the church's teaching, not the person's identity — still ask.
        if self._profile.for_whom == "other" and fid in ("lgbtq.orientation", "identity.orientation"):
            return True
        return False

    def _advance(self) -> None:
        step = self._step_before_advance()
        if step in WOMEN_LADDER_IDS:
            if step == "women_ladder" and self._ladder_rung and self._ladder_rung != "none":
                return
            if step == "women_ladder_followup":
                self._core_idx += 1
                return
        if self._phase == "core":
            self._core_idx += 1
        elif self._phase == "standard":
            self._std_idx += 1
        elif self._phase == "if_raised":
            self._if_raised_idx += 1
        elif self._phase == "advanced" and self._advanced_opt_in is not None and step != "advanced_opt_in":
            self._adv_idx += 1

    def _question_for(self, step: str) -> dict[str, Any]:
        if step == "location":
            return {"id": "location", "question": stage0_questions("core")[0]["question"], "options": None}
        if step == "for_whom":
            return {
                "id": "for_whom",
                "question": "Are you looking for a church for yourself, or for someone else you're helping?",
                "options": ["For myself", "For someone else"],
            }
        if step == "women_ladder":
            q = feature("women.senior_pastor")["question"]
            main = q.split("FOLLOW-UP:")[0].strip()
            return {
                "id": "women_ladder",
                "question": main,
                "options": ["(a) kids/women ministries", "(b) deacons", "(c) teach mixed adults", "(d) preach sometimes", "(e) associate pastors", "(f) elders", "(g) lead pastor", "No preference"],
            }
        if step == "women_ladder_followup":
            return {
                "id": "women_ladder_followup",
                "question": "Is that something you need to see (a minimum), as far as you're comfortable going (a maximum), or both?",
                "options": ["Minimum — need to see", "Maximum — as far as comfortable", "Both"],
            }
        if step == "advanced_opt_in":
            return {
                "id": "advanced_opt_in",
                "question": "Do you want to get specific about theology?",
                "options": ["Yes", "No thanks"],
            }
        meta = feature(step)
        opts = None
        if step == "lgbtq.marriage":
            opts = ["Affirm same-sex marriage", "Traditional marriage teaching", "Doesn't matter", "Prefer not to say"]
        elif step == "lgbtq.inclusion":
            opts = ["Full membership and leadership", "Membership open, leadership not", "Doesn't matter", "Prefer not to say"]
        elif _enumerable(meta):
            opts = [v.replace("_", " ") for v in meta["values"]] + ["Doesn't matter"]
        return {"id": step, "question": meta.get("question", meta["label"]), "options": opts}

    def _map_values(self, fid: str, text: str) -> list[str]:
        """Map a free-text answer to allowed values of one feature (fast model). Unknown -> []."""
        meta = feature(fid)
        if not _enumerable(meta):
            return []
        try:
            out = self._llm(
                "interviewer_map",
                [{"role": "system", "content": "Map the person's answer to the allowed values of ONE church feature. "
                                               "Return {\"want\": [allowed values the person would be happy with]}. "
                                               "Use only the allowed values; return [] if the answer does not say."},
                 {"role": "user", "content": f"Feature: {meta['label']}\nQuestion: {meta.get('question', '')}\n"
                                             f"Allowed values: {meta['values']}\nAnswer: {text}"}],
                {"type": "object", "required": ["want"], "properties": {"want": {"type": "array"}}},
                tier="fast",
            )
            return [v for v in out.get("want") or [] if v in meta["values"]]
        except Exception:
            return []

    def _apply_change(self, text: str) -> None:
        """Read-back correction: map free text onto any features (validated), then re-read."""
        from app.features import all_features

        feats = all_features()
        menu = {fid: m["values"] for fid, m in feats.items() if m.get("ask") != "never" and _enumerable(m)}
        try:
            out = self._llm(
                "interviewer_change",
                [{"role": "system", "content": "The person is correcting their church-search preferences. Return "
                                               "{\"updates\": [{\"feature\": id, \"want\": [allowed values], \"weight\": "
                                               "\"dealbreaker|important|nice_to_have|dont_care\"}]} using only the ids and values given."},
                 {"role": "user", "content": f"Current profile: {self._profile.model_dump_json()}\n"
                                             f"Features and allowed values: {menu}\nCorrection: {text}"}],
                {"type": "object", "required": ["updates"], "properties": {"updates": {"type": "array"}}},
                tier="strong",
            )
        except Exception:
            return
        for u in out.get("updates") or []:
            if not isinstance(u, dict) or u.get("feature") not in menu:
                continue
            want = [v for v in u.get("want") or [] if v in menu[u["feature"]]]
            weight = u.get("weight") if u.get("weight") in ("dealbreaker", "important", "nice_to_have", "dont_care") else "important"
            self._upsert_pref(Preference(feature=u["feature"], want=want, weight=weight, said=text))

    def _present(self, question: dict[str, Any]) -> dict:
        say = question["question"]
        options = question.get("options")
        try:
            bank = []
            for level in ("core", "standard", "if_raised", "advanced"):
                bank.extend({"id": q["id"], "question": q.get("question", ""), "ask": level} for q in stage0_questions(level))
            out = self._llm(
                "interviewer",
                [
                    {"role": "system", "content": llm.load_prompt("interviewer.v1")},
                    {
                        "role": "user",
                        "content": (
                            f"Question bank sample: {bank[:8]}\n"
                            f"Profile so far: {self._profile.model_dump()}\n"
                            f"Next question id: {question['id']}\n"
                            f"Scenario wording: {question['question']}\n"
                            f"Options: {options}\n"
                            "Return JSON for this turn only."
                        ),
                    },
                ],
                INTERVIEWER_SCHEMA,
                tier="strong",
            )
            # R2: the model only rephrases. Options stay ours (parsers depend on them); skip_rest only when the
            # user typed "skip"; raised ids must be real if_raised features.
            from app.features import all_features

            feats = all_features()
            for upd in out.get("raised") or []:
                if (isinstance(upd, str) and feats.get(upd, {}).get("ask") == "if_raised"
                        and upd not in self._if_raised and upd not in self._answered):
                    self._if_raised.append(upd)
            llm_say = (out.get("say") or "").strip()
            if llm_say and llm_say.lower() != "placeholder":
                say = llm_say
        except (KeyError, Exception):
            pass
        self._last_say = say
        return {"say": say, "options": options, "done": False, "escalation": None}
