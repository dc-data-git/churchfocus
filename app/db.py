"""SQLite access — the only place that runs SQL (INTERFACES §4)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.models import Evidence, PreferenceProfile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    profile_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS churches (
    church_id TEXT PRIMARY KEY,
    website TEXT,
    last_checked TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    church_id TEXT NOT NULL,
    feature TEXT NOT NULL,
    value TEXT NOT NULL,
    tier TEXT NOT NULL,
    quote TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL,
    how TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    church_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    report_path TEXT
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    body TEXT NOT NULL
);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _db_path() -> Path:
    return get_settings().data_dir / "app.db"


def _connect() -> sqlite3.Connection:
    data_dir = get_settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def save_profile(p: PreferenceProfile) -> None:
    init()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (id, created_at, profile_json) VALUES (?, ?, ?)",
            (p.session_id, _iso(_utcnow()), p.model_dump_json()),
        )
        conn.commit()


def get_profile(session_id: str) -> PreferenceProfile | None:
    init()
    with _connect() as conn:
        row = conn.execute("SELECT profile_json FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return PreferenceProfile.model_validate_json(row["profile_json"])


def upsert_church(church_id: str, website: str | None) -> None:
    init()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO churches (church_id, website, last_checked) VALUES (?, ?, NULL) "
            "ON CONFLICT(church_id) DO UPDATE SET website = excluded.website",
            (church_id, website),
        )
        conn.commit()


def church_last_checked(church_id: str) -> datetime | None:
    init()
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_checked FROM churches WHERE church_id = ?", (church_id,)
        ).fetchone()
    if not row or not row["last_checked"]:
        return None
    return _parse_iso(row["last_checked"])


def _touch_church_checked(church_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE churches SET last_checked = ? WHERE church_id = ?",
            (_iso(_utcnow()), church_id),
        )
        conn.commit()


def add_evidence(church_id: str, ev: list[Evidence]) -> None:
    if not ev:
        return
    init()
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO evidence (church_id, feature, value, tier, quote, url, source_kind, how, checked_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    church_id,
                    e.feature,
                    e.value,
                    e.tier,
                    e.quote,
                    e.url,
                    e.source_kind,
                    e.how,
                    _iso(e.checked_at),
                    e.note,
                )
                for e in ev
            ],
        )
        conn.commit()
    _touch_church_checked(church_id)


def get_evidence(church_id: str) -> list[Evidence]:
    init()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT feature, value, tier, quote, url, source_kind, how, checked_at, note "
            "FROM evidence WHERE church_id = ? ORDER BY id",
            (church_id,),
        ).fetchall()
    return [
        Evidence(
            feature=r["feature"],
            value=r["value"],
            tier=r["tier"],
            quote=r["quote"],
            url=r["url"],
            source_kind=r["source_kind"],
            how=r["how"],
            checked_at=_parse_iso(r["checked_at"]),
            note=r["note"],
        )
        for r in rows
    ]


def create_job(job_id: str, session_id: str, church_id: str) -> None:
    init()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, session_id, church_id, status, progress, started_at) "
            "VALUES (?, ?, ?, 'pending', 0, ?)",
            (job_id, session_id, church_id, _iso(_utcnow())),
        )
        conn.commit()


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    init()
    allowed = {"status", "progress", "finished_at", "report_path"}
    cols = []
    vals: list[object] = []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"unknown job field {k!r}")
        cols.append(f"{k} = ?")
        vals.append(v if k != "finished_at" or v is None or isinstance(v, str) else _iso(v))
    vals.append(job_id)
    with _connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE job_id = ?", vals)
        conn.commit()


def get_job(job_id: str) -> dict:
    init()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    return dict(row)


def cache_get(key: str) -> tuple[datetime, str] | None:
    init()
    with _connect() as conn:
        row = conn.execute("SELECT fetched_at, body FROM cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return _parse_iso(row["fetched_at"]), row["body"]


def cache_put(key: str, body: str) -> None:
    init()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, fetched_at, body) VALUES (?, ?, ?)",
            (key, _iso(_utcnow()), body),
        )
        conn.commit()
