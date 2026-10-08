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
CREATE TABLE IF NOT EXISTS memory_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    op_json TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL,
    PRIMARY KEY (session_id, n)
);
CREATE TABLE IF NOT EXISTS coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    lat REAL NOT NULL, lng REAL NOT NULL,
    radius_mi REAL NOT NULL,
    queries INTEGER NOT NULL DEFAULT 0,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_churches (
    session_id TEXT NOT NULL,
    church_id TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    denom_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (session_id, church_id)
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    church_id TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    answer TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_state (
    session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_sources (
    church_id TEXT NOT NULL, url TEXT NOT NULL, scope TEXT NOT NULL,
    text TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    speaker TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL, text_hash TEXT NOT NULL,
    PRIMARY KEY(church_id,url,scope)
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


_JOB_V2_COLUMNS = {"kind": "TEXT NOT NULL DEFAULT 'deep'", "cancel": "INTEGER NOT NULL DEFAULT 0",
                   "done": "INTEGER NOT NULL DEFAULT 0", "total": "INTEGER NOT NULL DEFAULT 0",
                   "label": "TEXT NOT NULL DEFAULT ''", "result_json": "TEXT NOT NULL DEFAULT ''",
                   "generation": "INTEGER NOT NULL DEFAULT 0", "visible": "INTEGER NOT NULL DEFAULT 1",
                   "announced": "INTEGER NOT NULL DEFAULT 0", "verified_at": "TEXT"}
_initialised: set[str] = set()


def init() -> None:
    path = str(_db_path())
    if path in _initialised and Path(path).exists():
        return
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for col, decl in _JOB_V2_COLUMNS.items():   # v2 job fields (REDESIGN §6); additive migration
            if col not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
        if "visible" not in have:
            # Legacy automatic medium results had no user authorization record.
            conn.execute("UPDATE jobs SET visible=0 WHERE kind='medium'")
        conn.commit()
    _initialised.add(path)


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
    allowed = {"status", "progress", "finished_at", "report_path", "kind", "cancel", "done", "total", "label", "result_json", "generation", "visible", "announced", "verified_at"}
    cols = []
    vals: list[object] = []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"unknown job field {k!r}")
        cols.append(f"{k} = ?")
        vals.append(v if k != "finished_at" or v is None or isinstance(v, str) else _iso(v))
    vals.append(job_id)
    with _connect() as conn:
        # A stopped worker cannot overwrite cancellation or resurrect a terminal result.
        guard = " AND cancel = 0 AND status != 'cancelled'" if fields.get("status") in ("pending", "running", "complete", "error") else ""
        conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE job_id = ?" + guard, vals)
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


# ---------------------------------------------------------------- v2 (REDESIGN.md)
def memory_append(session_id: str, op_jsons: list[str]) -> None:
    init()
    with _connect() as conn:
        conn.executemany("INSERT INTO memory_log (session_id, op_json, ts) VALUES (?, ?, ?)",
                         [(session_id, j, _iso(_utcnow())) for j in op_jsons])
        conn.commit()


def memory_rows(session_id: str) -> list[str]:
    init()
    with _connect() as conn:
        return [r["op_json"] for r in conn.execute("SELECT op_json FROM memory_log WHERE session_id = ? ORDER BY id", (session_id,))]


def message_add(session_id: str, role: str, text: str, meta: dict | None = None) -> int:
    init()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # Allocate and insert atomically across chat and research workers.
        n = (conn.execute("SELECT COALESCE(MAX(n), 0) FROM messages WHERE session_id = ?", (session_id,)).fetchone()[0] or 0) + 1
        conn.execute("INSERT INTO messages (session_id, n, role, text, meta_json, ts) VALUES (?, ?, ?, ?, ?, ?)",
                     (session_id, n, role, text, json.dumps(meta or {}), _iso(_utcnow())))
        conn.commit()
    return n


def messages_since(session_id: str, n: int = 0) -> list[dict]:
    init()
    with _connect() as conn:
        rows = conn.execute("SELECT n, role, text, meta_json, ts FROM messages WHERE session_id = ? AND n > ? ORDER BY n",
                            (session_id, n)).fetchall()
    return [{"n": r["n"], "role": r["role"], "text": r["text"], "meta": json.loads(r["meta_json"]), "ts": r["ts"]} for r in rows]


def coverage_add(session_id: str, lat: float, lng: float, radius_mi: float, queries: int) -> None:
    init()
    with _connect() as conn:
        conn.execute("INSERT INTO coverage (session_id, lat, lng, radius_mi, queries, ts) VALUES (?, ?, ?, ?, ?, ?)",
                     (session_id, lat, lng, radius_mi, queries, _iso(_utcnow())))
        conn.commit()


def coverage_get(session_id: str) -> list[dict]:
    init()
    with _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT lat, lng, radius_mi, queries, ts FROM coverage WHERE session_id = ? ORDER BY id", (session_id,))]


def session_church_reset(session_id: str) -> None:
    init()
    with _connect() as conn:
        conn.execute("DELETE FROM session_churches WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM coverage WHERE session_id = ?", (session_id,))
        conn.commit()


def session_church_put(session_id: str, church_id: str, candidate: dict, denom: dict | None = None) -> None:
    init()
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO session_churches (session_id, church_id, candidate_json, denom_json) VALUES (?, ?, ?, ?)",
                     (session_id, church_id, json.dumps(candidate), json.dumps(denom or {})))
        conn.commit()


def session_churches(session_id: str) -> list[dict]:
    init()
    with _connect() as conn:
        rows = conn.execute("SELECT church_id, candidate_json, denom_json FROM session_churches WHERE session_id = ?", (session_id,)).fetchall()
    return [{"church_id": r["church_id"], "candidate": json.loads(r["candidate_json"]), "denom": json.loads(r["denom_json"])} for r in rows]


def question_add(session_id: str, church_id: str, text: str) -> int:
    init()
    with _connect() as conn:
        import re
        normalize = lambda value: re.sub(r"[^\w]+", " ", value.casefold()).strip()
        conn.execute("BEGIN IMMEDIATE")
        for row in conn.execute("SELECT id,text FROM questions WHERE session_id=? AND church_id=?", (session_id,church_id)):
            if normalize(row["text"]) == normalize(text):
                return int(row["id"])
        cur = conn.execute("INSERT INTO questions (session_id, church_id, text, ts) VALUES (?, ?, ?, ?)",
                           (session_id, church_id, text, _iso(_utcnow())))
        conn.commit()
        return int(cur.lastrowid)


def question_update(qid: int, **fields) -> None:
    allowed = {"text", "status", "answer"}
    cols = [f"{k} = ?" for k in fields if k in allowed]
    if not cols:
        return
    init()
    with _connect() as conn:
        conn.execute(f"UPDATE questions SET {', '.join(cols)} WHERE id = ?", [v for k, v in fields.items() if k in allowed] + [qid])
        conn.commit()


def questions_list(session_id: str, church_id: str | None = None) -> list[dict]:
    init()
    q = "SELECT id, church_id, text, status, answer, ts FROM questions WHERE session_id = ?"
    args: list = [session_id]
    if church_id:
        q += " AND church_id = ?"
        args.append(church_id)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(q + " ORDER BY id", args)]


def jobs_for_session(session_id: str) -> list[dict]:
    init()
    with _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE session_id = ? ORDER BY started_at", (session_id,))]


def latest_job(church_id: str, kind: str, status: str = "complete") -> dict | None:
    init()
    with _connect() as conn:
        r = conn.execute("SELECT * FROM jobs WHERE church_id = ? AND kind = ? AND status = ? ORDER BY finished_at DESC LIMIT 1",
                         (church_id, kind, status)).fetchone()
    return dict(r) if r else None


def jobs_purge(kind: str, cutoff_iso: str) -> int:
    """Retention (D43): forget saved results of `kind` finished before cutoff (row kept for the audit trail)."""
    init()
    with _connect() as conn:
        cur = conn.execute("UPDATE jobs SET result_json = NULL, status = 'expired' WHERE kind = ? AND status = 'complete' "
                           "AND COALESCE(verified_at, finished_at) < ?", (kind, cutoff_iso))
        conn.commit()
        return cur.rowcount


def jobs_orphan_cleanup() -> int:
    """At startup: any job still pending/running belonged to a dead process."""
    init()
    with _connect() as conn:
        cur = conn.execute("UPDATE jobs SET status = 'error', label = 'Stopped when the app restarted' "
                           "WHERE status IN ('pending', 'running')")
        conn.commit()
        return cur.rowcount


# Repair: durable session authorization and reusable public research corpus.
def _state_default() -> dict:
    return {"generation": 0, "pins": [], "selection_mode": False, "selected": [],
            "active_church": None, "restart_pending": False, "pending_location": None,
            "discovery": {"status": "idle", "label": ""}, "table_version": 0, "memory_version": 0}


def session_state(session_id: str) -> dict:
    init()
    with _connect() as conn:
        row = conn.execute("SELECT state_json FROM session_state WHERE session_id=?", (session_id,)).fetchone()
    return {**_state_default(), **(json.loads(row[0]) if row else {})}


def state_update(session_id: str, **fields) -> dict:
    init()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state_json FROM session_state WHERE session_id=?", (session_id,)).fetchone()
        state = {**_state_default(), **(json.loads(row[0]) if row else {}), **fields}
        conn.execute("INSERT OR REPLACE INTO session_state VALUES (?,?)", (session_id,json.dumps(state)))
        conn.commit()
    return state


def state_bump(session_id: str, key: str) -> int:
    init()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state_json FROM session_state WHERE session_id=?", (session_id,)).fetchone()
        state = {**_state_default(), **(json.loads(row[0]) if row else {})}
        state[key] = int(state.get(key,0)) + 1
        conn.execute("INSERT OR REPLACE INTO session_state VALUES (?,?)", (session_id,json.dumps(state)))
        conn.commit()
    return state[key]


def state_reset(session_id: str) -> dict:
    state_bump(session_id,"generation")
    return state_update(session_id,pins=[],selected=[],selection_mode=False,active_church=None,
                        restart_pending=False,pending_location=None,discovery={"status":"idle","label":""})


def research_put(church_id: str, url: str, text: str, *, kind: str = "website", title: str = "",
                 speaker: str = "", published_at: str = "", scope: str = "medium", checked_at: str | None = None) -> None:
    import hashlib
    if scope not in ("medium","deep") or not url or not text.strip():
        return
    init()
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO research_sources VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (church_id,url,scope,text,kind,title,speaker,str(published_at or ""),checked_at or _iso(_utcnow()),
                      hashlib.sha256(text.encode()).hexdigest()))
        conn.commit()


def research_sources(church_id: str, scope: str | None = None) -> list[dict]:
    from datetime import timedelta
    init()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM research_sources WHERE church_id=?", (church_id,)).fetchall()
    return [dict(r) for r in rows if (scope is None or r["scope"] == scope)
            and _parse_iso(r["checked_at"]) >= _utcnow()-timedelta(days=90 if r["scope"] == "medium" else 365)]


def research_purge() -> int:
    from datetime import timedelta
    init()
    with _connect() as conn:
        count = 0
        for scope,days in (("medium",90),("deep",365)):
            count += conn.execute("DELETE FROM research_sources WHERE scope=? AND checked_at<?",
                                  (scope,_iso(_utcnow()-timedelta(days=days)))).rowcount
        conn.commit()
    return count


def delete_test_sessions(session_ids: list[str]) -> dict:
    """Explicit maintenance: delete selected test chats and unshared church research."""
    ids = list(dict.fromkeys(session_ids))
    if not ids:
        return {"sessions": 0, "churches": 0, "reports": []}
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        cids = {r[0] for r in conn.execute(f"SELECT church_id FROM session_churches WHERE session_id IN ({placeholders})", ids)}
        cids.update(r[0] for r in conn.execute(f"SELECT church_id FROM jobs WHERE session_id IN ({placeholders})", ids))
        reports = [r[0] for r in conn.execute(f"SELECT report_path FROM jobs WHERE session_id IN ({placeholders})", ids) if r[0]]
        for table in ("messages", "memory_log", "coverage", "questions", "session_state", "session_churches", "jobs"):
            conn.execute(f"DELETE FROM {table} WHERE session_id IN ({placeholders})", ids)
        count = conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", ids).rowcount
        unique = {cid for cid in cids if not conn.execute("SELECT 1 FROM session_churches WHERE church_id=?", (cid,)).fetchone()
                  and not conn.execute("SELECT 1 FROM jobs WHERE church_id=?", (cid,)).fetchone()}
        for cid in unique:
            urls = [r[0] for r in conn.execute("SELECT url FROM research_sources WHERE church_id=?", (cid,))]
            for table in ("evidence", "research_sources", "churches"):
                conn.execute(f"DELETE FROM {table} WHERE church_id=?", (cid,))
            for url in urls:
                if not conn.execute("SELECT 1 FROM research_sources WHERE url=?", (url,)).fetchone():
                    conn.execute("DELETE FROM cache WHERE key=?", (url,))
            conn.execute("DELETE FROM cache WHERE instr(key,?)>0", (cid,))
    return {"sessions": count, "churches": len(unique), "reports": reports}
