"""Build-state tracker for tasks.json (one task per line so parallel edits merge cleanly).

  python scripts/tasks.py                      # board: status by owner, blockers, milestones
  python scripts/tasks.py next Katie           # tasks Katie can start now (deps done)
  python scripts/tasks.py set T3.1 in_progress
  python scripts/tasks.py set T3.1 done "12 tests green"
  python scripts/tasks.py set T5.2 blocked "ffmpeg missing on Windows"
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "tasks.json"
STATUSES = ["todo", "in_progress", "blocked", "done", "cut"]
MARK = {"todo": "[ ]", "in_progress": "[~]", "blocked": "[!]", "done": "[x]", "cut": "[-]"}


def load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def save(d: dict) -> None:
    head = {k: v for k, v in d.items() if k not in ("milestones", "tasks")}
    lines = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    lines.append('  "milestones": [\n' + ",\n".join("    " + json.dumps(m, ensure_ascii=False) for m in d["milestones"]) + "\n  ],")
    lines.append('  "tasks": [\n' + ",\n".join("    " + json.dumps(t, ensure_ascii=False) for t in d["tasks"]) + "\n  ]")
    PATH.write_text("\n".join(lines) + "\n}\n", encoding="utf-8")


def ready(t: dict, by_id: dict) -> bool:
    return t["status"] == "todo" and all(by_id.get(x, {}).get("status") in ("done", "cut") for x in t["depends_on"])


def board(d: dict) -> None:
    by_id = {t["id"]: t for t in d["tasks"]}
    n = {s: sum(t["status"] == s for t in d["tasks"]) for s in STATUSES}
    print(f"{n['done']}/{len(d['tasks'])} done · {n['in_progress']} in progress · {n['blocked']} blocked · {n['cut']} cut\n")
    for owner in sorted({t["owner"] for t in d["tasks"]}):
        print(owner)
        for t in d["tasks"]:
            if t["owner"] == owner:
                flag = "  ← ready" if ready(t, by_id) else ""
                note = f"  ({t['note']})" if t["note"] and t["status"] in ("blocked", "done", "in_progress") else ""
                print(f"  {MARK[t['status']]} {t['id']:5} {t['title'][:70]}{note}{flag}")
        print()
    for m in d["milestones"]:
        left = [x for x in m["needs"] if by_id[x]["status"] != "done"]
        print(f"{m['id']} ({m['target']}): {'READY' if not left else 'waiting on ' + ', '.join(left)}")


def main(argv: list[str]) -> None:
    d = load()
    by_id = {t["id"]: t for t in d["tasks"]}
    if not argv:
        return board(d)
    if argv[0] == "register" and len(argv) == 2:
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        if "project" in spec:
            d["project"] = spec["project"]
        for task in spec.get("tasks", []):
            if task["id"] not in by_id:
                d["tasks"].append(task)
        known = {m["id"] for m in d["milestones"]}
        d["milestones"].extend(m for m in spec.get("milestones", []) if m["id"] not in known)
        save(d)
        print("Registered repair tasks")
        return
    if argv[0] == "annotate" and len(argv) == 3:
        tid = argv[1]
        if tid not in by_id:
            sys.exit(f"unknown task {tid!r}")
        metadata = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        allowed = {"files", "acceptance", "verification", "next_action", "review_roles"}
        by_id[tid].update({k:v for k,v in metadata.items() if k in allowed})
        save(d)
        print(f"Annotated {tid}")
        return
    if argv[0] == "next":
        who = argv[1].lower() if len(argv) > 1 else None
        for t in d["tasks"]:
            if ready(t, by_id) and (not who or t["owner"].lower() == who):
                print(f"{t['id']:5} {t['owner']:7} {t['title']}")
        return
    if argv[0] == "set" and len(argv) >= 3:
        tid, status = argv[1], argv[2]
        if tid not in by_id or status not in STATUSES:
            sys.exit(f"unknown task {tid!r} or status {status!r} (statuses: {', '.join(STATUSES)})")
        by_id[tid]["status"] = status
        if len(argv) > 3:
            by_id[tid]["note"] = " ".join(argv[3:])
        by_id[tid]["updated"] = dt.datetime.now().isoformat(timespec="minutes")
        save(d)
        print(f"{tid} -> {status}")
        return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
