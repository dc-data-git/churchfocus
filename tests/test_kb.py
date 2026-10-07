import asyncio

from app.denom.kb import map_value


def test_find_abbreviations_and_names(kb):
    assert kb.find("SBC")[0]["id"] == "sbc"
    assert kb.find("ELCA")[0]["id"] == "elca"
    assert kb.find("Southern Baptist Convention")[0]["id"] == "sbc"
    assert kb.find("First Mennonite Church")[0]["id"] == "mcusa"
    assert kb.find("Hillsboro Mennonite Brethren Church")[0]["id"] == "usmb"
    assert kb.find("Assembly of God Wichita")[0]["id"] == "ag"


def test_find_short_abbreviation_needs_exact_token(kb):
    assert all(r["id"] != "ag" for r in kb.find("Agape Fellowship"))


def test_find_unknown_name_returns_nothing_confident(kb):
    assert all(r["score"] < 85 for r in kb.find("Grace Community Church"))


def test_family_word_fallback_is_low_confidence(kb):
    res = kb.find("Trinity Lutheran Church")
    assert res and res[0]["score"] == 70 and res[0]["id"] == "elca"


def test_non_christian_groups_flagged(kb):
    assert kb.is_christian("sbc") and not kb.is_christian("usrc2020_267")


def test_prior_maps_text_to_allowed_values(kb):
    assert kb.prior("catholic", "women.senior_pastor").value == "no"
    assert kb.prior("elca", "women.senior_pastor").value == "yes"
    assert kb.prior("sbc", "women.pastor_other").value == "no"
    assert kb.prior("sbc", "theology.baptism").value == "believers_immersion"
    assert kb.prior("catholic", "theology.baptism").value == "infant_and_adult"
    assert kb.prior("ag", "worship.style").value == "charismatic_expressive"
    p = kb.prior("catholic", "identity.branch")
    assert p.value == "catholic" and p.tier == "prior" and p.how == "prior" and "typical for" in p.note


def test_prior_none_when_unmappable_or_not_applicable(kb):
    assert kb.prior("usmb", "women.senior_pastor") is None          # "Requires ... policy review"
    assert kb.prior("usrc2020_267", "theology.baptism") is None     # "Not applicable"
    assert map_value("theology.baptism", "Not applicable") is None


def test_sensitive_prior_needs_original_or_human_accept(kb):
    assert kb.prior("elca", "lgbtq.marriage") is None                # model fill, not reviewed
    assert kb.prior("sbc", "lgbtq.marriage").value == "traditional"  # human-accepted fill


def test_identity_denomination_prior_is_group_id(kb):
    assert kb.prior("elca", "identity.denomination").value == "elca"


def test_compare(kb):
    rows = kb.compare("elca", "sbc", ["women.senior_pastor", "theology.baptism"])
    assert rows[0]["elca"] == "yes" and rows[0]["sbc"] in ("no", None) and rows[1]["same"] is False


def test_match_profile_prefers_fitting_denominations(kb, profile_factory):
    prof = profile_factory([("women.senior_pastor", ["yes"], "dealbreaker"),
                            ("theology.baptism", ["infant_and_adult"], "important")])
    top = kb.match_profile(prof, k=3)
    assert top[0]["id"] == "elca"
    assert next(r for r in kb.match_profile(prof, k=10) if r["id"] == "catholic")["score"] < top[0]["score"]


def test_mcp_server_lists_five_tools():
    from app.denom import mcp_server
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert names == {"find_denomination", "get_denomination", "compare_denominations", "match_profile", "denomination_prior"}
