from app.features import all_features, settled_open, stage0_questions
from app.models import Evidence


def e(**kw):
    base = dict(feature="theology.baptism", value="infant_and_adult", tier="A", how="stated", source_kind="website", quote="q", url="https://a.org/x")
    base.update(kw)
    return Evidence(**base)


def test_vocabulary_loads():
    f = all_features()
    assert len(f) == 71 and "community.young_adults" in f


def test_stage0_questions_only_with_question_text():
    core = [q["id"] for q in stage0_questions("core")]
    assert core[0] == "logistics.distance" and "women.senior_pastor" in core and "women.elder" not in core


def test_settled_rule():
    s, o = settled_open([e()], ["theology.baptism", "worship.style"])
    assert s == ["theology.baptism"] and o == ["worship.style"]
    two_b = [e(tier="B", url="https://umc.org/a"), e(tier="B", url="https://news.com/b")]
    assert settled_open(two_b, ["theology.baptism"])[0] == ["theology.baptism"]
    same_site = [e(tier="B", url="https://umc.org/a"), e(tier="B", url="https://www.umc.org/b")]
    assert settled_open(same_site, ["theology.baptism"])[0] == []
    obs = [e(feature="women.preach", value="never", tier="D", how="observed", quote="", note="0 of 6 sermons")]
    assert settled_open(obs, ["women.preach"])[0] == ["women.preach"]
