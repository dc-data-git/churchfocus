from app.match import score, score_evidence
from app.models import Church, DenomGuess, Evidence


def ev(feature, value, tier="A", how="stated", note="", kind="statement_of_faith"):
    return Evidence(feature=feature, value=value, tier=tier, how=how, note=note, source_kind=kind,
                    quote="quoted text" if tier in "ABC" else "", url="https://example.org/beliefs")


def church(evidence=(), denom=None, conf=0.9, dist=3.0):
    return Church(church_id="c1", name="Test Church", distance_miles=dist, evidence=list(evidence),
                  denomination=DenomGuess(denomination_id=denom, label=denom or "Unknown", confidence=conf if denom else 0))


def test_v2_practice_dealbreaker_never_excludes_only_lowers_fit(kb, profile_factory):
    """D28: 'must have charismatic worship' keeps a liturgical church, at low fit."""
    prof = profile_factory([("women.senior_pastor", ["yes"], "dealbreaker")])
    r = score(church([ev("women.senior_pastor", "no")]), prof, kb)
    assert not r.excluded and r.fit in ("poor", "unlikely") and "women.senior_pastor" in r.unmatched


def test_v2_denomination_avoid_is_the_only_hard_filter(kb):
    from app.models import Preference, PreferenceProfile
    prof = PreferenceProfile(session_id="t", preferences=[Preference(feature="identity.denomination", avoid=["umc"], weight="dealbreaker")])
    assert score(church(denom="umc"), prof, kb).excluded
    assert not score(church(denom="elca"), prof, kb).excluded


def test_prior_conflict_lowers_but_never_excludes(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "dealbreaker")])
    r = score(church(denom="catholic"), prof, kb)
    assert not r.excluded and r.score < 50
    assert any("typical for" in w for w in r.why)


def test_church_evidence_beats_prior(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "important")])
    r = score(church([ev("women.senior_pastor", "yes")], denom="catholic"), prof, kb)   # outlier congregation
    assert r.score > 50 and r.matched == ["women.senior_pastor"]


def test_unknown_dealbreaker_small_penalty_and_one_summary_line(kb, profile_factory):
    prof = profile_factory([("theology.gifts", ["cessationist"], "dealbreaker")])
    r = score(church(), prof, kb)
    assert r.unknown == ["theology.gifts"] and 45 <= r.score < 50 and r.fit == "unknown"
    assert r.why == ["1 thing to check later"]


def test_avoided_value_counts_against(kb):
    from app.models import Preference, PreferenceProfile
    prof = PreferenceProfile(session_id="t", preferences=[Preference(feature="worship.style", avoid=["charismatic_expressive"], weight="important")])
    assert score(church([ev("worship.style", "charismatic_expressive", kind="website")]), prof, kb).score < 50
    assert score(church([ev("worship.style", "liturgical_traditional", kind="website")]), prof, kb).score > 50


def test_why_at_most_four_lines_and_names_source(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "important"), ("theology.baptism", ["infant_and_adult"], "important"),
                            ("worship.style", ["contemporary"], "nice_to_have"), ("community.kids", ["present"], "important"),
                            ("polity.governance", ["congregational_vote"], "nice_to_have")])
    r = score(church([ev("women.senior_pastor", "yes"), ev("community.kids", "present", kind="website")], denom="elca"), prof, kb)
    assert len(r.why) <= 4
    assert any("beliefs page" in w for w in r.why) and any("typical for elca" in w.lower() for w in r.why)


def test_distance_penalty(profile_factory):
    prof = profile_factory([], max_miles=10)
    near = score_evidence("c", prof, {}, distance_miles=2)
    far = score_evidence("c", prof, {}, distance_miles=10)
    assert near.score == 50 and far.score == 40


def test_dont_care_ignored(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "dont_care")])
    assert score(church([ev("women.senior_pastor", "no")]), prof, kb).score == 50
