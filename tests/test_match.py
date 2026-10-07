from app.match import score, score_evidence
from app.models import Church, DenomGuess, Evidence


def ev(feature, value, tier="A", how="stated", note="", kind="statement_of_faith"):
    return Evidence(feature=feature, value=value, tier=tier, how=how, note=note, source_kind=kind,
                    quote="quoted text" if tier in "ABC" else "", url="https://example.org/beliefs")


def church(evidence=(), denom=None, conf=0.9, dist=3.0):
    return Church(church_id="c1", name="Test Church", distance_miles=dist, evidence=list(evidence),
                  denomination=DenomGuess(denomination_id=denom, label=denom or "Unknown", confidence=conf if denom else 0))


def test_tier_a_conflict_on_dealbreaker_excludes(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "dealbreaker")])
    r = score(church([ev("women.senior_pastor", "no")]), prof, kb)
    assert r.excluded and "women.senior_pastor" in r.unmatched


def test_prior_conflict_lowers_but_never_excludes(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "dealbreaker")])
    r = score(church(denom="catholic"), prof, kb)
    assert not r.excluded and r.score < 50
    assert any("typical for" in w for w in r.why)


def test_church_evidence_beats_prior(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "important")])
    r = score(church([ev("women.senior_pastor", "yes")], denom="catholic"), prof, kb)   # outlier congregation
    assert r.score > 50 and r.matched == ["women.senior_pastor"]


def test_unknown_dealbreaker_small_penalty_and_listed(kb, profile_factory):
    prof = profile_factory([("theology.gifts", ["cessationist"], "dealbreaker")])
    r = score(church(), prof, kb)
    assert r.unknown == ["theology.gifts"] and 45 <= r.score < 50
    assert r.why and r.why[0].startswith("?")


def test_observed_d_tier_needs_five_points_to_exclude(kb, profile_factory):
    prof = profile_factory([("women.preach", ["regularly", "occasionally"], "dealbreaker")])
    weak = score(church([ev("women.preach", "never", tier="D", how="observed", note="0 of 3 sermons by women")]), prof, kb)
    strong = score(church([ev("women.preach", "never", tier="D", how="observed", note="0 of 12 sermons by women")]), prof, kb)
    assert not weak.excluded and strong.excluded


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
