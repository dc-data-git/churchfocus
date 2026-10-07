"""Church Report builder and HTML renderer (INTERFACES §3–4)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import ROOT, get_settings
from app.denom.kb import get_kb
from app.features import all_features, feature, settled_open
from app.llm import complete_json, load_prompt
from app.match import score
from app.models import Church, ChurchReport, Evidence, MatchResult, PreferenceProfile

_REPORT_SCHEMA = {
    "type": "object",
    "required": ["at_a_glance", "how_it_fits", "stated_vs_observed", "still_unknown", "questions_for_visit"],
    "properties": {
        "at_a_glance": {"type": "string"},
        "how_it_fits": {"type": "string"},
        "stated_vs_observed": {"type": "string"},
        "still_unknown": {"type": "string"},
        "questions_for_visit": {"type": "array", "items": {"type": "string"}},
    },
}


def _known_feature(fid: str) -> bool:
    return fid in all_features()


def _filter_known_evidence(evidence: list[Evidence]) -> list[Evidence]:
    return [e for e in evidence if _known_feature(e.feature)]


def build_stated_vs_observed(evidence: list[Evidence]) -> list[dict]:
    """Rows where both stated and observed evidence exist for the same feature."""
    evidence = _filter_known_evidence(evidence)
    by_feature: dict[str, dict[str, Evidence | None]] = {}
    for ev in evidence:
        if ev.how not in ("stated", "observed"):
            continue
        row = by_feature.setdefault(ev.feature, {"stated": None, "observed": None})
        current = row[ev.how]
        if current is None or ev.checked_at > current.checked_at:
            row[ev.how] = ev

    rows: list[dict] = []
    for fid, pair in sorted(by_feature.items()):
        stated, observed = pair["stated"], pair["observed"]
        if stated is None or observed is None:
            continue
        agrees: bool | None
        if stated.value == observed.value:
            agrees = True
        else:
            agrees = False
        rows.append(
            {
                "feature": fid,
                "label": feature(fid)["label"],
                "stated": stated.model_dump(mode="json"),
                "observed": observed.model_dump(mode="json"),
                "agrees": agrees,
            }
        )
    return rows


def build_questions_for_visit(
    profile: PreferenceProfile,
    match: MatchResult,
    escalations: list,
    open_features: list[str],
) -> list[str]:
    questions: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            questions.append(q)

    for esc in escalations:
        for q in getattr(esc, "questions_to_ask", []) or []:
            add(q)

    for fid in match.unknown:
        if fid not in open_features or not _known_feature(fid):
            continue
        pref = next((p for p in profile.preferences if p.feature == fid), None)
        if pref and pref.weight in ("dealbreaker", "important"):
            add(f"What is your church's position on {feature(fid)['label']}?")

    for fid in open_features:
        if not _known_feature(fid):
            continue
        pref = next((p for p in profile.preferences if p.feature == fid), None)
        if pref and pref.weight == "dealbreaker":
            add(f"Could you clarify {feature(fid)['label']}? (this is a must-have for you)")

    return questions[:6]


def _narrative_sections(church: Church, profile: PreferenceProfile, report: ChurchReport) -> dict[str, Any]:
    prompt = load_prompt("report.v1")
    payload = {
        "church": church.model_dump(mode="json"),
        "match": report.match.model_dump(mode="json"),
        "settled": report.settled,
        "open": report.open,
        "stated_vs_observed": report.stated_vs_observed,
        "escalations": [e.model_dump(mode="json") for e in report.escalations],
        "questions_for_visit": report.questions_for_visit,
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    try:
        return complete_json("report", messages, _REPORT_SCHEMA, tier="strong")
    except Exception:
        return {
            "at_a_glance": f"{church.name} — score {report.match.score}.",
            "how_it_fits": "\n".join(report.match.why[:4]) or "See evidence below.",
            "stated_vs_observed": "",
            "still_unknown": ", ".join(report.open[:8]) or "None listed.",
            "questions_for_visit": report.questions_for_visit,
        }


def log_path_for(session_id: str, church_id: str) -> str:
    return str(get_settings().data_dir / "logs" / session_id / f"{church_id}.jsonl")


def build_report(church: Church, profile: PreferenceProfile, ctx: dict[str, Any]) -> tuple[ChurchReport, dict[str, Any]]:
    kb = get_kb()
    match = score(church, profile, kb)
    feature_ids = [p.feature for p in profile.preferences if _known_feature(p.feature)]
    evidence = _filter_known_evidence(church.evidence or db_get_evidence(church.church_id))
    settled, open_f = settled_open(evidence, feature_ids)
    open_f = [f for f in open_f if _known_feature(f)]
    escalations = list(ctx.get("escalations", []))
    svo = build_stated_vs_observed(evidence)
    questions = build_questions_for_visit(profile, match, escalations, open_f)

    report = ChurchReport(
        church=church.model_copy(update={"evidence": evidence}),
        profile_session=profile.session_id,
        match=match,
        settled=settled,
        open=open_f,
        stated_vs_observed=svo,
        sermons_analysed=int(ctx.get("sermons_analysed", 0)),
        escalations=escalations,
        questions_for_visit=questions,
        generated_at=datetime.now(timezone.utc),
        log_path=log_path_for(profile.session_id, church.church_id),
    )
    narrative = _narrative_sections(church, profile, report)
    if narrative.get("questions_for_visit"):
        merged = list(report.questions_for_visit)
        for q in narrative["questions_for_visit"]:
            if q not in merged:
                merged.append(q)
        report = report.model_copy(update={"questions_for_visit": merged[:6]})
    return report, narrative


def db_get_evidence(church_id: str) -> list[Evidence]:
    from app import db

    return db.get_evidence(church_id)


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(ROOT / "app" / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_html(report: ChurchReport, *, narrative: dict[str, Any] | None = None) -> str:
    narrative = narrative or {}
    env = _jinja_env()
    template = env.get_template("report.html")
    known_evidence = _filter_known_evidence(report.church.evidence)
    feature_labels = {fid: feature(fid)["label"] for fid in report.settled + report.open if _known_feature(fid)}
    return template.render(
        report=report,
        narrative=narrative,
        feature_labels=feature_labels,
        known_evidence=known_evidence,
    )


def save_report(report: ChurchReport, job_id: str, *, narrative: dict[str, Any] | None = None) -> Path:
    out_dir = get_settings().data_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{job_id}.html"
    path.write_text(render_html(report, narrative=narrative), encoding="utf-8")
    meta = out_dir / f"{job_id}.json"
    payload = report.model_dump(mode="json")
    if narrative:
        payload["narrative"] = narrative
    meta.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
