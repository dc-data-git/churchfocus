"""Session/step log writer (INTERFACES §4)."""
from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.models import StepLog


def _log_path(session_id: str, church_id: str | None) -> Path:
    logs = get_settings().data_dir / "logs" / session_id
    logs.mkdir(parents=True, exist_ok=True)
    name = church_id if church_id else "session"
    name = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)   # "osm:123" is not a valid Windows filename
    return logs / f"{name}.jsonl"


def write_step(step: StepLog) -> None:
    path = _log_path(step.session_id, step.church_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(step.model_dump_json() + "\n")


def read_session(session_id: str) -> list[dict]:
    base = get_settings().data_dir / "logs" / session_id
    if not base.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(base.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows
