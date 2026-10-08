"""Conversation message store (REDESIGN §8). Bot/user/system messages per session, in order."""
from __future__ import annotations

from app import db


def post(session_id: str, text: str, meta: dict | None = None, role: str = "bot") -> int:
    """Add a message; meta may carry {"options": [...], "kind": "church_summary"|"search_started"|..., "church_id": ...}."""
    return db.message_add(session_id, role, text, meta)


def since(session_id: str, n: int = 0) -> list[dict]:
    return db.messages_since(session_id, n)
