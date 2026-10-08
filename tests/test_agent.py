"""Stage 3 deep-search agent + tools + report (BUILD_PLAN T7)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.log import read_session
from app.match import score
from app.models import Church, DenomGuess, Evidence, Preference, PreferenceProfile
from app.stage3 import agent, report as report_module
from app.stage3 import tools
from tests.fakes import FakeLLM, FakeWeb

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def offline_report_model(monkeypatch):
    """Report rendering regressions never call a live model."""
    monkeypatch.setattr(report_module, "complete_json", lambda *a, **k: {
        "at_a_glance": "Offline research report", "how_it_fits": "",
        "stated_vs_observed": "", "still_unknown": "", "questions_for_visit": []})


MARRIAGE_QUOTE = (
    "We believe marriage is a covenant between one man and one woman, "
    "ordained by God for the union of husband and wife."
)


def _beliefs_page() -> dict[str, dict]:
    base = "https://grace-baptist.fixture"
    html = (FIX / "site_grace_baptist" / "beliefs.html").read_text(encoding="utf-8")
    return {f"{base}/beliefs": {"html": html}}


def _church(**kw) -> Church:
    defaults = dict(
        church_id="c-deep",
        name="Grace Baptist",
        address="100 Main, Wichita, KS",
        website="https://grace-baptist.fixture/",
        distance_miles=2.0,
        denomination=DenomGuess(denomination_id="sbc", label="Southern Baptist", confidence=0.9),
        evidence=[],
    )
    defaults.update(kw)
    return Church(**defaults)


def _profile(*prefs, session_id="sess-deep") -> PreferenceProfile:
    return PreferenceProfile(
        session_id=session_id,
        preferences=[Preference(feature=f, want=w, weight=wt) for f, w, wt in prefs],
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db import init

    init()
    return tmp_path


def _ctx(data_dir, profile=None, church=None):
    profile = profile or _profile(("lgbtq.marriage", ["traditional"], "important"))
    church = church or _church()
    return tools.make_ctx(
        session_id=profile.session_id,
        church_id=church.church_id,
        job_id="job-test",
        profile=profile,
        church=church,
    )


def _log_actions(data_dir, session_id="sess-deep", church_id="c-deep") -> list[str]:
    rows = read_session(session_id)
    return [r["action"] for r in rows if r.get("church_id") == church_id]


def _log_whys(data_dir, session_id="sess-deep", church_id="c-deep") -> list[str]:
    rows = read_session(session_id)
    return [r.get("why", "") for r in rows if r.get("church_id") == church_id]


class TestTools:
    def test_fetch_page_logs_step(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(
            monkeypatch
        )
        ctx = _ctx(data_dir)
        ctx["_tool_why"] = "Check beliefs page for marriage stance"
        out = tools.fetch_page(ctx, "https://grace-baptist.fixture/beliefs")
        assert out["status"] == 200
        assert "fetch_page" in _log_actions(data_dir)
        assert all(w for w in _log_whys(data_dir))

    def test_search_web_logs_step(self, data_dir, monkeypatch):
        monkeypatch.setattr("app.stage3.tools.llm_web_search", lambda q: [{"title": "Hit", "url": "https://x", "snippet": q}])
        ctx = _ctx(data_dir)
        ctx["_tool_why"] = "Look for locator listing"
        out = tools.search_web(ctx, "Grace Baptist Wichita")
        assert out["results"]
        assert "search_web" in _log_actions(data_dir)

    def test_denomination_lookup_logs_step(self, data_dir, kb):
        ctx = _ctx(data_dir)
        ctx["_tool_why"] = "Confirm denomination context"
        out = tools.denomination_lookup(ctx, "SBC")
        assert "matches" in out or "id" in out
        assert "denomination_lookup" in _log_actions(data_dir)

    def test_record_evidence_rejects_non_verbatim(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(
            monkeypatch
        )
        ctx = _ctx(data_dir)
        tools.fetch_page(ctx, "https://grace-baptist.fixture/beliefs")
        ctx["_tool_why"] = "Record marriage definition"
        bad = Evidence(
            feature="lgbtq.marriage",
            value="traditional",
            tier="A",
            quote="marriage is for everyone equally",
            url="https://grace-baptist.fixture/beliefs",
            source_kind="statement_of_faith",
            how="stated",
            checked_at=datetime.now(timezone.utc),
        )
        result = tools.record_evidence(ctx, [bad])
        assert result["ok"] is False
        assert result.get("correction") is True
        assert "correction" in _log_actions(data_dir)

    def test_record_evidence_accepts_verbatim(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(
            monkeypatch
        )
        ctx = _ctx(data_dir)
        tools.fetch_page(ctx, "https://grace-baptist.fixture/beliefs")
        ctx["_tool_why"] = "Record marriage definition"
        good = Evidence(
            feature="lgbtq.marriage",
            value="traditional",
            tier="A",
            quote=MARRIAGE_QUOTE,
            url="https://grace-baptist.fixture/beliefs",
            source_kind="statement_of_faith",
            how="stated",
            checked_at=datetime.now(timezone.utc),
        )
        result = tools.record_evidence(ctx, [good])
        assert result["ok"] is True
        assert "lgbtq.marriage" in result["settled"]


class TestDeepSearch:
    def _script_tools(self, steps):
        it = iter(steps)

        def next_turn(*_a, **_k):
            return next(it)

        return next_turn

    def test_continues_after_preferences_settled(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(
            monkeypatch
        )
        profile = _profile(("lgbtq.marriage", ["traditional"], "important"))
        church = _church()
        steps = [
            {
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {
                            "name": "fetch_page",
                            "arguments": json.dumps(
                                {
                                    "url": "https://grace-baptist.fixture/beliefs",
                                    "why": "Read beliefs page for marriage stance",
                                }
                            ),
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "2",
                        "function": {
                            "name": "record_evidence",
                            "arguments": json.dumps(
                                {
                                    "why": "Store tier-A marriage quote",
                                    "evidence": [
                                        {
                                            "feature": "lgbtq.marriage",
                                            "value": "traditional",
                                            "tier": "A",
                                            "quote": MARRIAGE_QUOTE,
                                            "url": "https://grace-baptist.fixture/beliefs",
                                            "source_kind": "statement_of_faith",
                                            "how": "stated",
                                            "checked_at": datetime.now(timezone.utc).isoformat(),
                                        }
                                    ],
                                }
                            ),
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "3",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps(
                                {"summary": "Marriage stance settled from beliefs page.", "why": "Important features done"}
                            ),
                        },
                    }
                ]
            },
        ]
        fake = FakeLLM({"deep_search:tools": self._script_tools(steps + [steps[-1]])})
        monkeypatch.setattr("app.stage3.agent.chat_tools", fake.chat_tools)
        monkeypatch.setattr("app.stage3.report.complete_json", lambda *a, **k: {"at_a_glance": "Done.", "how_it_fits": "", "stated_vs_observed": "", "still_unknown": "", "questions_for_visit": []})

        rep = agent.deep_search(church, profile, "job-settled")
        assert "lgbtq.marriage" in rep.settled
        assert len(fake.calls) >= 4  # preference coverage alone no longer ends research
        whys = _log_whys(data_dir)
        assert all(w for w in whys)

    def test_correction_then_retry_succeeds(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(
            monkeypatch
        )
        profile = _profile(("lgbtq.marriage", ["traditional"], "important"))
        church = _church()
        steps = [
            {
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {
                            "name": "fetch_page",
                            "arguments": json.dumps(
                                {"url": "https://grace-baptist.fixture/beliefs", "why": "Fetch beliefs"}
                            ),
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "2",
                        "function": {
                            "name": "record_evidence",
                            "arguments": json.dumps(
                                {
                                    "why": "Try bad quote",
                                    "evidence": [
                                        {
                                            "feature": "lgbtq.marriage",
                                            "value": "traditional",
                                            "tier": "A",
                                            "quote": "totally wrong quote text here",
                                            "url": "https://grace-baptist.fixture/beliefs",
                                            "source_kind": "statement_of_faith",
                                            "how": "stated",
                                            "checked_at": datetime.now(timezone.utc).isoformat(),
                                        }
                                    ],
                                }
                            ),
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "3",
                        "function": {
                            "name": "record_evidence",
                            "arguments": json.dumps(
                                {
                                    "why": "Retry with verbatim quote",
                                    "evidence": [
                                        {
                                            "feature": "lgbtq.marriage",
                                            "value": "traditional",
                                            "tier": "A",
                                            "quote": MARRIAGE_QUOTE,
                                            "url": "https://grace-baptist.fixture/beliefs",
                                            "source_kind": "statement_of_faith",
                                            "how": "stated",
                                            "checked_at": datetime.now(timezone.utc).isoformat(),
                                        }
                                    ],
                                }
                            ),
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "4",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps({"summary": "Settled after correction.", "why": "Done"}),
                        },
                    }
                ]
            },
        ]
        fake = FakeLLM({"deep_search:tools": self._script_tools(steps)})
        monkeypatch.setattr("app.stage3.agent.chat_tools", fake.chat_tools)
        monkeypatch.setattr("app.stage3.report.complete_json", lambda *a, **k: {"at_a_glance": "Done.", "how_it_fits": "", "stated_vs_observed": "", "still_unknown": "", "questions_for_visit": []})

        rep = agent.deep_search(church, profile, "job-correction")
        assert "correction" in _log_actions(data_dir)
        assert "lgbtq.marriage" in rep.settled

    def test_budget_exhausted_escalates_open_dealbreaker(self, data_dir, monkeypatch):
        monkeypatch.setenv("DEEP_MAX_TOOL_CALLS", "1")
        from app.config import get_settings

        get_settings.cache_clear()
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(
            monkeypatch
        )
        profile = _profile(("women.senior_pastor", ["yes"], "dealbreaker"))
        church = _church()
        steps = [
            {
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {
                            "name": "fetch_page",
                            "arguments": json.dumps(
                                {"url": "https://grace-baptist.fixture/beliefs", "why": "Look for staff info"}
                            ),
                        },
                    }
                ]
            },
        ]
        fake = FakeLLM({"deep_search:tools": self._script_tools(steps)})
        monkeypatch.setattr("app.stage3.agent.chat_tools", fake.chat_tools)
        monkeypatch.setattr("app.stage3.report.complete_json", lambda *a, **k: {"at_a_glance": "Budget hit.", "how_it_fits": "", "stated_vs_observed": "", "still_unknown": "", "questions_for_visit": []})

        rep = agent.deep_search(church, profile, "job-budget")
        reasons = [e.reason for e in rep.escalations]
        assert "budget_exhausted" in reasons

    def test_run_deep_search_job_sync(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(
            monkeypatch
        )
        profile = _profile(("lgbtq.marriage", ["traditional"], "important"))
        church = _church()

        def one_finish(*_a, **_k):
            return {
                "tool_calls": [
                    {
                        "id": "1",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps({"summary": "Nothing left.", "why": "Stop"}),
                        },
                    }
                ]
            }

        monkeypatch.setattr("app.stage3.agent.chat_tools", one_finish)
        monkeypatch.setattr("app.stage3.report.complete_json", lambda *a, **k: {"at_a_glance": "Sync.", "how_it_fits": "", "stated_vs_observed": "", "still_unknown": "", "questions_for_visit": []})

        rep = agent.run_deep_search_job(church, profile, "job-sync", background=False)
        assert rep is not None
        assert rep.church.church_id == "c-deep"


class TestReport:
    def test_stated_vs_observed_rows(self, data_dir):
        church = _church(
            evidence=[
                Evidence(
                    feature="women.preach",
                    value="never",
                    tier="A",
                    quote="Only men preach here.",
                    url="https://example/beliefs",
                    source_kind="statement_of_faith",
                    how="stated",
                ),
                Evidence(
                    feature="women.preach",
                    value="occasionally",
                    tier="D",
                    source_kind="sermon_transcript",
                    how="observed",
                    note="2 of 6 sermons",
                ),
            ]
        )
        profile = _profile(("women.preach", ["regularly"], "important"))
        ctx = tools.make_ctx(session_id="s1", church_id=church.church_id, job_id="j1", profile=profile, church=church)
        rep, _ = report_module.build_report(church, profile, ctx)
        assert len(rep.stated_vs_observed) == 1
        row = rep.stated_vs_observed[0]
        assert row["feature"] == "women.preach"
        assert row["agrees"] is False

    def test_refuses_unknown_features_in_render(self, data_dir):
        church = _church(
            evidence=[
                Evidence(
                    feature="not.in.yaml",
                    value="x",
                    tier="A",
                    quote="fake",
                    url="https://example",
                    source_kind="website",
                    how="stated",
                ),
                Evidence(
                    feature="lgbtq.marriage",
                    value="traditional",
                    tier="A",
                    quote="one man and one woman",
                    url="https://example/beliefs",
                    source_kind="statement_of_faith",
                    how="stated",
                ),
            ]
        )
        profile = _profile(("lgbtq.marriage", ["traditional"], "important"))
        ctx = tools.make_ctx(session_id="s2", church_id=church.church_id, job_id="j2", profile=profile, church=church)
        rep, narrative = report_module.build_report(church, profile, ctx)
        html = report_module.render_html(rep, narrative={"at_a_glance": "Test"})
        assert "not.in.yaml" not in html
        assert "lgbtq.marriage" not in html or "Marriage" in html or "traditional" in html

    def test_questions_for_visit(self, data_dir):
        profile = _profile(("women.senior_pastor", ["yes"], "dealbreaker"))
        church = _church()
        match = score(church, profile)
        ctx = tools.make_ctx(session_id="s3", church_id=church.church_id, job_id="j3", profile=profile, church=church)
        rep, _ = report_module.build_report(church, profile, ctx)
        assert rep.questions_for_visit
        assert any("senior" in q.lower() or "pastor" in q.lower() for q in rep.questions_for_visit)

    def test_print_css_in_template(self, data_dir):
        church = _church()
        profile = _profile(("lgbtq.marriage", ["traditional"], "important"))
        ctx = tools.make_ctx(session_id="s4", church_id=church.church_id, job_id="j4", profile=profile, church=church)
        rep, narrative = report_module.build_report(church, profile, ctx)
        html = report_module.render_html(rep, narrative=narrative)
        assert "@media print" in html
        assert "Save as PDF" in html or "Print" in html


# ---------------------------------------------------------------- code review regressions (R6, R10)
from app.stage3.agent import deep_search  # noqa: E402


class TestReviewRegressions:
    def _ev(self, **kw):
        base = dict(feature="lgbtq.marriage", value="traditional", tier="A", quote="q" * 20,
                    url="https://grace-baptist.fixture/beliefs", source_kind="statement_of_faith", how="stated")
        base.update(kw)
        return base

    def test_parallel_tool_calls_keep_tool_messages_contiguous(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        profile = _profile(("lgbtq.marriage", ["traditional"], "important"))
        seen = []

        def chat(task, messages, tools_, tier="strong", **kw):
            seen.append(messages)
            if len(seen) == 1:
                return {"content": "", "tool_calls": [
                    {"id": "a", "function": {"name": "fetch_page", "arguments": json.dumps({"url": "https://grace-baptist.fixture/beliefs", "why": "x"})}},
                    {"id": "b", "function": {"name": "record_evidence", "arguments": json.dumps({"why": "x", "evidence": []})}},
                    {"id": "c", "function": {"name": "find_sermon_feeds", "arguments": json.dumps({"why": "x"})}}]}
            return {"content": "", "tool_calls": [{"id": "z", "function": {"name": "finish", "arguments": json.dumps({"summary": "done", "why": "x"})}}]}

        monkeypatch.setattr("app.stage3.agent.chat_tools", chat)
        deep_search(_church(), profile, "jpar")
        second = seen[1]
        i = next(k for k, m in enumerate(second) if m.get("role") == "assistant" and m.get("tool_calls"))
        assert [m["role"] for m in second[i + 1:i + 4]] == ["tool", "tool", "tool"]

    def test_failing_tool_and_failing_model_never_leave_job_running(self, data_dir, monkeypatch):
        profile = _profile(("lgbtq.marriage", ["traditional"], "important"))
        monkeypatch.setattr("app.stage3.tools.fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        calls = {"n": 0}

        def chat(task, messages, tools_, tier="strong", **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"content": "", "tool_calls": [{"id": "a", "function": {"name": "fetch_page", "arguments": json.dumps({"url": "https://x.org", "why": "x"})}}]}
            raise RuntimeError("rate limited")

        monkeypatch.setattr("app.stage3.agent.chat_tools", chat)
        deep_search(_church(), profile, "jerr")
        from app import db as _db
        job = _db.get_job("jerr")
        assert job["status"] == "error" and job["report_path"]

    def test_tier_d_and_prior_cannot_be_recorded_by_agent(self, data_dir):
        ctx = _ctx(data_dir)
        r = tools.record_evidence(ctx, [self._ev(feature="women.preach", value="never", tier="D", quote="", note="0 of 9 sermons")])
        assert r["ok"] is False and "analyse_sermons" in r["errors"][0]

    def test_unstated_is_allowed(self, data_dir, monkeypatch):
        FakeWeb(pages=_beliefs_page(), robots={"grace-baptist.fixture": "User-agent: *\nAllow: /\n"}).install(monkeypatch)
        ctx = _ctx(data_dir)
        tools.fetch_page(ctx, "https://grace-baptist.fixture/beliefs")
        r = tools.record_evidence(ctx, [self._ev(feature="lgbtq.inclusion", value="unstated", quote="")])
        assert r["ok"] is True

    def test_marriage_definition_cannot_set_inclusion(self):
        from app.evidence_rules import marriage_rule_violation
        assert marriage_rule_violation("lgbtq.inclusion", "membership_not_leadership", "We believe marriage is between one man and one woman.")
        assert not marriage_rule_violation("lgbtq.inclusion", "full", "LGBTQ people are welcome as members and may serve as elders.")
        assert not marriage_rule_violation("lgbtq.marriage", "affirming", "We celebrate marriage between any two people.")   # affirming kept

    def test_compaction_keeps_one_state_and_trims_old_results(self):
        from app.stage3.agent import _compact
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "CURRENT STATE 1"}]
        for k in range(4):
            msgs += [{"role": "assistant", "content": "", "tool_calls": [{"id": str(k)}]},
                     {"role": "tool", "tool_call_id": str(k), "content": "x" * 5000},
                     {"role": "user", "content": f"CURRENT STATE {k + 2}"}]
        out = _compact(msgs)
        assert sum(1 for m in out if str(m.get("content", "")).startswith("CURRENT STATE")) == 1
        assert len(out[3]["content"]) < 1000 and len(out[-2]["content"]) == 5000
