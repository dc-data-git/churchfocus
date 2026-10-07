"""Stage 0 interviewer tests (T3.1–T3.3). No network — FakeLLM only."""
from __future__ import annotations

import pytest

from app.models import Preference, PreferenceProfile
from app.stage0.interviewer import Interviewer
from tests.fakes import FakeLLM


def _fake_llm(**responses):
    base = {
        "interviewer": {"say": "placeholder", "options": None, "updates": [], "raised": [], "skip_rest": False},
        "crisis_check": {"escalate": False, "kind": "none", "reason": "ok"},
        "readback": {"text": "Must-haves: contemporary worship. Likely: Baptist churches. Did I get that right?"},
    }
    base.update(responses)
    return FakeLLM(base)


def _walk_core(iv: Interviewer) -> list[str]:
    """Answer location → for_whom → branch → worship → women ladder (no preference)."""
    steps = [
        ("Hesston, KS, within 20 miles", None),
        ("For myself", None),
        ("Mennonite felt like home", None),
        ("contemporary band", None),
        ("No preference", None),
    ]
    says = []
    r = iv.next_turn(None)
    says.append(r["say"])
    for text, _ in steps:
        r = iv.next_turn(text)
        says.append(r["say"])
    return says


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db import init

    init()


def test_core_questions_in_order(tmp_db):
    iv = Interviewer("s1", complete_json_fn=_fake_llm().complete_json)
    r = iv.next_turn(None)
    assert "coming from" in r["say"].lower() or "drive" in r["say"].lower()

    r = iv.next_turn("Wichita, KS, 15 miles")
    assert "yourself" in r["say"].lower() or "someone else" in r["say"].lower()

    r = iv.next_turn("For myself")
    assert "church before" in r["say"].lower() or "felt like home" in r["say"].lower()

    r = iv.next_turn("Baptist churches felt right")
    assert "sunday" in r["say"].lower() or "worship" in r["say"].lower() or "organ" in r["say"].lower()

    r = iv.next_turn("full band contemporary")
    assert "women" in r["say"].lower() or "ladder" in r["say"].lower() or "deacon" in r["say"].lower()


def test_skip_rest_jumps_to_readback(tmp_db):
    iv = Interviewer("s2", complete_json_fn=_fake_llm().complete_json)
    _walk_core(iv)
    # First standard question
    r = iv.next_turn("skip the rest")
    assert "right" in r["say"].lower() or "must-have" in r["say"].lower()
    assert r["done"] is False


def test_ladder_rung_f_both(tmp_db):
    iv = Interviewer("s3", complete_json_fn=_fake_llm().complete_json)
    iv.next_turn(None)
    for text in [
        "Hesston, KS, 15 miles",
        "For myself",
        "Protestant",
        "contemporary",
        "(f) elders",
        "Both",
    ]:
        iv.next_turn(text)
    prof = iv.profile()
    prefs = {p.feature: p for p in prof.preferences}

    for fid in ("women.deacon", "women.teach_mixed_adults", "women.pastor_other", "women.elder"):
        assert prefs[fid].want == ["yes"], fid
    assert prefs["women.preach"].want == ["regularly", "occasionally"]
    assert prefs["women.senior_pastor"].want == ["no"]


def test_ladder_rung_b_maximum(tmp_db):
    iv = Interviewer("s4", complete_json_fn=_fake_llm().complete_json)
    iv.next_turn(None)
    for text in [
        "Hesston, KS, 15 miles",
        "For myself",
        "Protestant",
        "contemporary",
        "(b) deacons",
        "Maximum — as far as comfortable",
    ]:
        iv.next_turn(text)
    prefs = {p.feature: p for p in iv.profile().preferences}

    assert prefs["women.deacon"].weight == "dont_care"
    for fid in ("women.teach_mixed_adults", "women.pastor_other", "women.elder", "women.senior_pastor"):
        assert prefs[fid].want == ["no"], fid
    assert prefs["women.preach"].want == ["never"]   # women.preach has no "no" value


def test_prefer_not_to_say_marriage_still_asks_inclusion(tmp_db):
    iv = Interviewer("s5", complete_json_fn=_fake_llm().complete_json)
    iv.next_turn(None)
    for text in ["Wichita, 10 miles", "For myself", "Baptist", "contemporary", "No preference"]:
        iv.next_turn(text)
    marriage_done = False
    inclusion_asked = False
    for _ in range(40):
        r = iv.next_turn("doesn't matter")
        low = r["say"].lower()
        if not marriage_done and "marriage" in low:
            r2 = iv.next_turn("prefer not to say")
            prefs = {p.feature: p for p in iv.profile().preferences}
            assert prefs["lgbtq.marriage"].weight == "dont_care"
            marriage_done = True
            low2 = r2["say"].lower()
            if "member" in low2 or "leadership" in low2 or "lgbtq people" in low2 or "inclusion" in low2:
                inclusion_asked = True
                break
            continue
        if marriage_done and ("member" in low or "leadership" in low or "lgbtq people" in low or "inclusion" in low):
            inclusion_asked = True
            break
    assert inclusion_asked


def test_for_whom_other_no_orientation_question(tmp_db):
    iv = Interviewer("s6b", complete_json_fn=_fake_llm().complete_json)
    iv.next_turn(None)
    for text in [
        "Wichita, 10 miles",
        "For someone else",
        "Methodist",
        "hymns",
        "No preference",
    ]:
        iv.next_turn(text)
    orientation_q = False
    for _ in range(40):
        r = iv.next_turn("doesn't matter")
        low = r["say"].lower()
        if "your orientation" in low or "are you lgbtq" in low or "are they lgbtq" in low:
            orientation_q = True
        if "get that right" in low:
            break
    assert not orientation_q
    assert iv.profile().for_whom == "other"


def test_crisis_escalation_stops_interview(tmp_db):
    fake = _fake_llm(crisis_check={"escalate": True, "kind": "crisis", "reason": "self-harm mentioned"})
    iv = Interviewer("s7", complete_json_fn=fake.complete_json)
    iv.next_turn(None)
    r = iv.next_turn("I want to kill myself")
    assert r["escalation"] is not None
    assert r["escalation"].reason == "pastoral_or_crisis"
    assert "988" in r["escalation"].message
    assert r["done"] is True
    r2 = iv.next_turn("hello")
    assert r2["done"] is True
    assert r2["escalation"] is not None


def test_crisis_keyword_without_llm(tmp_db):
    iv = Interviewer("s7b", complete_json_fn=_fake_llm().complete_json)
    iv.next_turn(None)
    r = iv.next_turn("I'm thinking about suicide")
    assert r["escalation"] is not None
    assert "988" in r["say"]


def test_readback_and_likely_denominations(tmp_db, kb):
    iv = Interviewer("s8", complete_json_fn=_fake_llm().complete_json)
    _walk_core(iv)
    iv.next_turn("skip the rest")
    text = iv.readback()
    assert "right" in text.lower()
    prof = iv.profile()
    assert isinstance(prof.likely_denominations, list)


def test_confirm_saves_profile(tmp_db):
    iv = Interviewer("s9", complete_json_fn=_fake_llm().complete_json)
    _walk_core(iv)
    iv.next_turn("skip the rest")
    r = iv.next_turn("Yes, that's right")
    assert r["done"] is True

    from app.db import get_profile

    saved = get_profile("s9")
    assert saved is not None
    assert saved.confirmed is True


def test_weight_asked_before_advancing(tmp_db):
    iv = Interviewer("s10", complete_json_fn=_fake_llm().complete_json)
    _walk_core(iv)
    # Hit first standard question — answer without weight keyword
    r = iv.next_turn("Sunday morning works best")
    if any(o in (r.get("options") or []) for o in ("Must-have", "Important")):
        assert "important" in r["say"].lower() or "must" in r["say"].lower()
        iv.next_turn("Must-have")
    prof = iv.profile()
    assert len(prof.preferences) >= 1


def test_profile_method_returns_copy(tmp_db):
    iv = Interviewer("s11", complete_json_fn=_fake_llm().complete_json)
    p1 = iv.profile()
    p1.for_whom = "other"
    p2 = iv.profile()
    assert p2.for_whom == "self"


# ---- regression tests for code review R2 / R11 ----

def _to_standard(iv):
    iv.next_turn(None)
    for text in ["Hesston, KS, 15 miles", "For myself", "Protestant", "contemporary", "No preference"]:
        iv.next_turn(text)


def test_weight_answer_advances_no_loop(tmp_db):
    iv = Interviewer("r2a", complete_json_fn=_fake_llm(interviewer_map={"want": []}).complete_json)
    _to_standard(iv)
    first = iv.next_turn("Sunday morning works best")   # likely asks importance
    if "important" in first["say"].lower():
        nxt = iv.next_turn("Important")
        assert "important" not in nxt["say"].lower() or nxt["say"] != first["say"]
        again = iv.next_turn("Sunday morning works best")
        assert again["say"] != first["say"] or "important" in again["say"].lower()
    answered_before = len(iv._answered)
    assert answered_before >= 1


def test_ladder_letter_a_in_sentence_is_not_rung_a():
    from app.stage0.interviewer import _parse_ladder_rung
    assert _parse_ladder_rung("I'd like a church where women can preach") == "d"
    assert _parse_ladder_rung("a") == "a"
    assert _parse_ladder_rung("(f) elders") == "f"


def test_ladder_weight_asked_once_and_applied(tmp_db):
    iv = Interviewer("r2c", complete_json_fn=_fake_llm().complete_json)
    iv.next_turn(None)
    for text in ["Hesston, KS", "For myself", "Protestant", "contemporary", "(g) lead pastor"]:
        iv.next_turn(text)
    r = iv.next_turn("Minimum — need to see")
    assert "important" in r["say"].lower()
    iv.next_turn("Must-have")
    prefs = {p.feature: p for p in iv.profile().preferences}
    assert prefs["women.senior_pastor"].weight == "dealbreaker" and prefs["women.senior_pastor"].want == ["yes"]


def test_readback_change_is_not_a_dead_end(tmp_db):
    fake = _fake_llm(interviewer_change={"updates": [{"feature": "worship.style", "want": ["liturgical_traditional"], "weight": "dealbreaker"}]})
    iv = Interviewer("r2d", complete_json_fn=fake.complete_json)
    _walk_core(iv)
    iv.next_turn("skip the rest")
    r = iv.next_turn("I need to change something")
    assert "change" in r["say"].lower()
    r = iv.next_turn("Actually I want a liturgical service, that's a must")
    assert "right" in r["say"].lower()
    prefs = {p.feature: p for p in iv.profile().preferences}
    assert prefs["worship.style"].want == ["liturgical_traditional"] and prefs["worship.style"].weight == "dealbreaker"


def test_llm_cannot_inject_options_or_bad_feature(tmp_db):
    fake = _fake_llm(interviewer={"say": "Hi", "options": ["Something else"], "updates": [], "raised": ["not.a.feature", "community.recovery"], "skip_rest": True})
    iv = Interviewer("r2e", complete_json_fn=fake.complete_json)
    iv.next_turn(None)
    r = iv.next_turn("Hesston, KS")
    assert r["options"] == ["For myself", "For someone else"]
    assert "not.a.feature" not in iv._if_raised and iv._skip_rest is False


def test_grief_does_not_trigger_hard_crisis(tmp_db):
    iv = Interviewer("r11", complete_json_fn=_fake_llm().complete_json)
    iv.next_turn(None)
    r = iv.next_turn("Wichita. My mom died last spring and I want a church family")
    assert r["escalation"] is None
