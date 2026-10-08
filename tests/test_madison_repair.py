import pytest
from app import db,memory
from app.models import MemoryOp,Church,DenomGuess,Preference,PreferenceProfile,Evidence
from app.stage0 import conversation
from app.match import score

@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setenv("DATA_DIR",str(tmp_path))
    from app.config import get_settings
    get_settings.cache_clear();db.init()
    yield
    get_settings.cache_clear()

def op(key,val,stance="want",**kwargs):
    return MemoryOp(t=1,key=key,val=val,stance=stance,strength=0.6,conf=1,src="stated",**kwargs)

def test_wanted_families_preserve_catholic_exclusion(store):
    memory.append("m",[op("denomination","catholic","avoid"),op("denomination","lcms")])
    p=memory.to_profile("m").preferences[0]
    assert p.want==["lcms"] and p.avoid==["catholic"]

def test_additions_and_explicit_corrections(store):
    memory.append("m",[op("identity.tradition","lutheran"),op("identity.tradition","anglican_episcopal")])
    assert memory.to_profile("m").preferences[0].want==["lutheran","anglican_episcopal"]
    memory.append("m",[op("identity.tradition","anabaptist_mennonite",op="revise")])
    assert memory.to_profile("m").preferences[0].want==["anabaptist_mennonite"]

def test_madison_interpretation_preserves_scope(store):
    conversation._apply_ops("m",1,[op("denomination",["lcms","wels","episcopal"],ev="Prioritize Lutheran and Anglican churches; others are okay").model_dump(),op("theology.scripture","inerrant",ev="Biblical authority").model_dump(),op("worship.style","traditional_hymns",ev="They want traditional hymns").model_dump(),op("worship.style","liturgical_traditional",ev="Prioritize liturgical churches").model_dump()])
    prefs={p.feature:p for p in memory.to_profile("m").preferences}
    assert prefs["identity.tradition"].want==["lutheran","anglican_episcopal"]
    assert prefs["theology.scripture"].want==["inerrant","infallible","inspired_authoritative"]
    assert prefs["worship.music_sources"].want==["hymns"]
    assert prefs["worship.style"].want==["liturgical_traditional"]

def test_family_priority_keeps_els_and_avoids_catholic():
    p=PreferenceProfile(session_id="m",preferences=[Preference(feature="identity.tradition",want=["lutheran","anglican_episcopal"],weight="important"),Preference(feature="identity.branch",avoid=["catholic"],weight="dealbreaker")])
    c=Church(church_id="els",name="Our Saviour",denomination=DenomGuess(denomination_id="els",confidence=.9,method="website"),evidence=[Evidence(feature="identity.tradition",value="lutheran",tier="A",source_kind="website",how="stated"),Evidence(feature="identity.branch",value="evangelical_protestant",tier="A",source_kind="website",how="stated")])
    assert not score(c,p).excluded
    before=p.model_copy(update={"preferences":p.preferences[:1]})
    assert score(c,p).score==score(c,before).score

def test_known_worship_mismatch_never_possible_fit():
    p=PreferenceProfile(session_id="m",preferences=[Preference(feature="worship.style",want=["liturgical_traditional"],weight="important"),Preference(feature="theology.scripture",want=["inerrant"],weight="dealbreaker"),Preference(feature="lgbtq.marriage",want=["traditional"],weight="dealbreaker")])
    c=Church(church_id="c",name="Church",evidence=[Evidence(feature=f,value=v,tier="A",source_kind="website",how="stated") for f,v in [("worship.style","charismatic_expressive"),("theology.scripture","inerrant"),("lgbtq.marriage","traditional")]])
    assert score(c,p).fit not in {"strong","possible"}


def test_cleanup_preserves_other_chats_shared_sources(store):
    from app.models import DenomGuess
    d=DenomGuess().model_dump(mode="json")
    for sid in ("m","other"):
        db.session_church_put(sid,"shared",{"church_id":"shared","name":"Shared church"},d)
    db.session_church_put("m","unique",{"church_id":"unique","name":"Test only church"},d)
    db.research_put("shared","https://shared.example/","Shared public source")
    db.research_put("unique","https://unique.example/","Test source")
    db.delete_test_sessions(["m"])
    assert not db.session_churches("m") and len(db.session_churches("other"))==1
    assert db.research_sources("shared") and not db.research_sources("unique")


def test_protestant_is_not_narrowed_to_mainline(store):
    conversation._apply_ops("m",1,[op("identity.branch","mainline_protestant",ev="Protestant").model_dump()],text="They are Protestant")
    assert "evangelical_protestant" in memory.to_profile("m").preferences[0].want



def test_head_pastor_negation_is_not_reversed(store):
    raw=op("women.senior_pastor","no","avoid",ev="not head pastors or priests").model_dump()
    conversation._apply_ops("m",1,[raw],text="Preachers okay. But not head pastors or priests.")
    p=memory.to_profile("m").preferences[0]
    assert p.want==["no"] and p.avoid==[]
    def church(value):
        return Church(church_id=value,name="Test",evidence=[Evidence(feature="women.senior_pastor",value=value,tier="A",source_kind="website",how="stated")])
    profile=memory.to_profile("m")
    assert score(church("no"),profile).score > score(church("yes"),profile).score


def test_explicit_protestant_scope_saved_without_model_ops(store):
    conversation._apply_ops("m",1,[],text="Definitely protestant. But try a liturgical church.")
    p=next(x for x in memory.to_profile("m").preferences if x.feature=="identity.branch")
    assert p.weight=="dealbreaker" and "evangelical_protestant" in p.want
    assert set(p.avoid)=={"catholic","eastern_orthodox","oriental_orthodox"}

def test_saturated_discovery_stays_bounded_and_publishes_progress(store,monkeypatch):
    from app.stage1 import search
    calls=[]
    def fake(lat,lng,radius,max_results):
        if calls:
            assert search.get_church("fast","0") is not None
            assert memory.table_version("fast") > 0
        calls.append((lat,lng,radius))
        return [{"church_id":str(i),"name":"Church", "lat":43,"lng":-89} for i in range(60)]
    monkeypatch.setattr(search.places,"search_churches",fake)
    monkeypatch.setattr(search,"_fast_denom",lambda c:DenomGuess())
    search._mem.pop("fast",None)
    assert search.ensure_coverage("fast",43,-89,15)==60
    assert len(calls)==7
    assert search.ensure_coverage("fast",43,-89,15)==0
    assert len(calls)==7
