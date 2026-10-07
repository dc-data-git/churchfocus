"""EVAL_PLAN §5 guardrail / leakage matrix (automated)."""
from __future__ import annotations

import inspect

from app.models import Evidence, now
from app.stage3 import agent as agent_mod
from app.stage3 import report as report_mod
from app.web import scrub_pii


def test_g1_scrub_pii_redacts_email_and_phone():
    text = "Contact Jane at jane.doe@example.com or 316-555-0199 for prayer."
    out = scrub_pii(text)
    assert "jane.doe@example.com" not in out
    assert "316-555-0199" not in out
    assert "[email redacted]" in out
    assert "[phone redacted]" in out


def test_g1_scrub_keeps_main_contact_email():
    text = "Email office@grace.church or volunteer@grace.church"
    out = scrub_pii(text, keep_contact="office@grace.church")
    assert "office@grace.church" in out
    assert "volunteer@grace.church" not in out


def test_g4_agent_tools_are_read_only():
    write_names = {"post", "put", "patch", "delete", "send_email", "call", "write_file"}
    for name, fn in agent_mod._TOOL_DISPATCH.items():
        assert name not in write_names
        src = inspect.getsource(fn).lower()
        assert ".post(" not in src
        assert "smtp" not in src


def test_g2_report_refuses_unknown_features():
    from app.models import Church, ChurchReport, DenomGuess, MatchResult, PreferenceProfile

    church = Church(
        church_id="g2",
        name="Test",
        denomination=DenomGuess(label="Unknown"),
        evidence=[
            Evidence(
                feature="not.a.real.feature",
                value="x",
                tier="A",
                quote="made up",
                url="https://example.com",
                source_kind="website",
                how="stated",
                checked_at=now(),
            ),
            Evidence(
                feature="lgbtq.marriage",
                value="traditional",
                tier="A",
                quote="one man and one woman in covenant",
                url="https://example.com/beliefs",
                source_kind="statement_of_faith",
                how="stated",
                checked_at=now(),
            ),
        ],
    )
    filtered = report_mod._filter_known_evidence(church.evidence)
    assert all(e.feature != "not.a.real.feature" for e in filtered)
    church = church.model_copy(update={"evidence": filtered})
    rep = ChurchReport(
        church=church,
        profile_session="g2",
        match=MatchResult(church_id="g2", score=50, excluded=False),
        settled=["lgbtq.marriage"],
        open=[],
    )
    html = report_mod.render_html(rep, narrative={"at_a_glance": "ok", "how_it_fits": "", "stated_vs_observed": "", "still_unknown": "", "questions_for_visit": []})
    assert "not.a.real.feature" not in html


def test_g3_no_party_labels_in_tool_schemas():
    blob = " ".join(str(s) for s in agent_mod._TOOL_SCHEMAS).lower()
    for banned in ("democrat", "republican", "gop", "party affiliation"):
        assert banned not in blob


def test_marriage_rule_does_not_set_inclusion():
    from app.stage2.summary import _apply_marriage_rule

    items = [
        {
            "feature": "lgbtq.marriage",
            "status": "stated",
            "value": "traditional",
            "quote": "Marriage is between one man and one woman.",
            "url": "https://x/beliefs",
        },
        {
            "feature": "lgbtq.inclusion",
            "status": "stated",
            "value": "full",
            "quote": "Marriage is between one man and one woman.",
            "url": "https://x/beliefs",
        },
    ]
    out = _apply_marriage_rule(items)
    feats = {i["feature"] for i in out}
    assert "lgbtq.marriage" in feats
    assert "lgbtq.inclusion" not in feats
