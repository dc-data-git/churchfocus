"""Read the six-layer workbook into plain Python structures, and write an updated copy.

Never modifies the input file. The output workbook is a copy with:
- filled cells (previously Unknown) in green, with a cell comment holding the quote + URL
- conflicts (sources contradict an existing value) in red, with a comment; value NOT changed
- confirmations (sources support an existing value) left as-is, evidence row upgraded
- new sheets: Proposals (every proposed change), Run Info
- Evidence and Sources sheets get appended rows (status "model_extracted_needs_review")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill

LAYER_SHEETS = {
    "identity": "1 Identity",
    "history": "2 History",
    "theology": "3 Theology",
    "governance": "4 Governance",
    "practice_culture": "5 Practice and Culture",
    "distinguishing_metadata": "6 Distinguishing Metadata",
}
UNKNOWN = {"", "unknown", "none", "null", "n/a"}

FILL_NEW = PatternFill("solid", fgColor="C6EFCE")       # green: filled from sources
FILL_CONFLICT = PatternFill("solid", fgColor="FFC7CE")  # red: sources disagree with existing value
FILL_NA = PatternFill("solid", fgColor="E7E6E6")        # grey: not applicable


def is_unknown(v) -> bool:
    return v is None or str(v).strip().lower() in UNKNOWN


def _header_index(rows, first_cell: str) -> int:
    for i, r in enumerate(rows):
        if r and r[0] is not None and str(r[0]).strip() == first_cell:
            return i
    raise ValueError(f"header row starting with {first_cell!r} not found")


@dataclass
class KB:
    path: Path
    groups: list[dict] = field(default_factory=list)          # census order
    fields: dict[str, dict] = field(default_factory=dict)     # field_id -> {layer, subtopic, description, dtype, note}
    columns: dict[str, dict[str, str]] = field(default_factory=dict)  # layer -> header label -> field_id
    values: dict[tuple[str, str], str] = field(default_factory=dict)  # (gid, field_id) -> value
    evidence: dict[tuple[str, str], dict] = field(default_factory=dict)
    sources: dict[str, dict] = field(default_factory=dict)

    def group(self, gid: str) -> dict:
        return next(g for g in self.groups if g["id"] == gid)

    def group_values(self, gid: str) -> dict[str, str]:
        return {f: v for (g, f), v in self.values.items() if g == gid}

    def source_urls_for(self, gid: str) -> list[dict]:
        ids = set()
        for (g, _f), ev in self.evidence.items():
            if g == gid and ev.get("source_ids"):
                ids.update(s.strip() for s in str(ev["source_ids"]).replace(";", ",").split(",") if s.strip())
        return [self.sources[i] for i in sorted(ids) if i in self.sources and self.sources[i].get("url")]


def load(path: str | Path) -> KB:
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    kb = KB(path=path)

    rows = list(wb["Field Dictionary"].iter_rows(values_only=True))
    h = _header_index(rows, "Field ID")
    for r in rows[h + 1:]:
        if r[0]:
            kb.fields[r[0]] = {"layer": r[1], "subtopic": r[2], "description": r[3], "dtype": r[4], "note": r[5]}

    rows = list(wb["Groups"].iter_rows(values_only=True))
    h = _header_index(rows, "Census group name")
    for r in rows[h + 1:]:
        if r[0] and r[1]:
            kb.groups.append({"census_name": r[0], "id": r[1], "census_code": r[2], "name": r[3] or r[0],
                              "congregations": _num(r[4]), "adherents": _num(r[5]), "share": _num(r[6])})

    for layer, sheet in LAYER_SHEETS.items():
        rows = list(wb[sheet].iter_rows(values_only=True))
        h = _header_index(rows, "Census group name")
        header = rows[h]
        by_desc = {v["description"].strip().lower(): fid for fid, v in kb.fields.items() if v["layer"] == layer}
        colmap = {}
        for label in header[2:]:
            if label is None:
                continue
            fid = by_desc.get(str(label).strip().lower())
            if fid:
                colmap[str(label)] = fid
        kb.columns[layer] = colmap
        for r in rows[h + 1:]:
            if not r[1]:
                continue
            for label, val in zip(header[2:], r[2:]):
                fid = colmap.get(str(label)) if label is not None else None
                if fid:
                    kb.values[(r[1], fid)] = "" if val is None else str(val)

    rows = list(wb["Evidence"].iter_rows(values_only=True))
    h = _header_index(rows, "Group")
    for r in rows[h + 1:]:
        if r[1] and r[2]:
            kb.evidence[(r[1], r[2])] = {"status": r[3], "source_ids": r[4], "verified": r[5], "notes": r[6]}

    rows = list(wb["Sources"].iter_rows(values_only=True))
    h = _header_index(rows, "Source ID")
    for r in rows[h + 1:]:
        if r[0]:
            kb.sources[r[0]] = {"id": r[0], "title": r[1], "url": r[2], "type": r[3], "status": r[4], "date": r[5]}
    wb.close()
    return kb


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write_updated(kb: KB, proposals: list[dict], new_sources: list[dict], out_path: Path, run_info: dict) -> Path:
    """Copy the input workbook and apply proposals as described in the module docstring."""
    wb = openpyxl.load_workbook(kb.path)
    for layer, sheet in LAYER_SHEETS.items():
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        h = _header_index(rows, "Census group name")
        header = rows[h]
        col_of = {kb.columns[layer][str(lbl)]: j + 1 for j, lbl in enumerate(header)
                  if lbl is not None and str(lbl) in kb.columns[layer]}
        row_of = {r[1]: i + 1 for i, r in enumerate(rows) if i > h and r[1]}
        for p in proposals:
            if p["layer"] != layer or p["group_id"] not in row_of or p["field_id"] not in col_of:
                continue
            c = ws.cell(row=row_of[p["group_id"]], column=col_of[p["field_id"]])
            note = f"{p['action']} ({p['source_type']}, {p['model']}): \"{p['quote'][:300]}\" {p['url']}"
            if p["action"] == "fill":
                c.value = p["new_value"]
                c.fill = FILL_NEW
                c.comment = Comment(note, "denom-kb")
            elif p["action"] == "conflict":
                c.fill = FILL_CONFLICT
                c.comment = Comment(f"Existing value disputed. Proposed: {p['new_value']}\n{note}", "denom-kb")
            elif p["action"] == "not_applicable":
                c.value = "Not applicable"
                c.fill = FILL_NA
                c.comment = Comment(p["quote"] or "not applicable to this tradition", "denom-kb")

    ev = wb["Evidence"]
    for p in proposals:
        if p["action"] in ("fill", "confirm", "conflict"):
            status = {"fill": "model_extracted_needs_review", "confirm": "model_confirmed_needs_review",
                      "conflict": "model_conflict_needs_review"}[p["action"]]
            ev.append([kb.group(p["group_id"])["census_name"], p["group_id"], p["field_id"], status,
                       p["source_id"], p["checked_at"][:10], f"Quote: \"{p['quote'][:500]}\""])
    src = wb["Sources"]
    for s in new_sources:
        src.append([s["id"], s["title"], s["url"], s["type"], "retrieved", "Unknown", s.get("notes", "")])

    if "Proposals" in wb.sheetnames:
        del wb["Proposals"]
    ps = wb.create_sheet("Proposals")
    cols = ["group_id", "group", "field_id", "action", "old_value", "old_status", "new_value", "quote", "url",
            "source_type", "confidence", "model", "checked_at"]
    ps.append(cols)
    for p in proposals:
        ps.append([p["group_id"], kb.group(p["group_id"])["census_name"], p["field_id"], p["action"],
                   p["old_value"], p["old_status"], p["new_value"], p["quote"], p["url"], p["source_type"],
                   p["confidence"], p["model"], p["checked_at"]])
    if "Run Info" in wb.sheetnames:
        del wb["Run Info"]
    ri = wb.create_sheet("Run Info")
    for k, v in run_info.items():
        ri.append([k, str(v)])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
