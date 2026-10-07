"""Test doubles — no network (CONVENTIONS rule 8)."""
from __future__ import annotations

import json
from typing import Any


class FakeLLM:
    """Canned JSON outputs keyed by task name (INTERFACES §4)."""

    def __init__(self, responses: dict[str, Any] | None = None, *, fail_once: set[str] | None = None):
        self.responses = responses or {}
        self.fail_once = fail_once or set()
        self._failed: set[str] = set()
        self.calls: list[dict] = []

    def complete_json(
        self,
        task: str,
        messages: list[dict],
        schema: dict,
        tier: str = "fast",
        **kw: Any,
    ) -> dict:
        self.calls.append({"task": task, "messages": messages, "schema": schema, "tier": tier, **kw})
        if task in self.fail_once and task not in self._failed:
            self._failed.add(task)
            return {"not_valid": True}
        if task not in self.responses:
            raise KeyError(f"no FakeLLM response for task {task!r}")
        out = self.responses[task]
        return out() if callable(out) else out

    def chat_tools(self, task: str, messages: list[dict], tools: list[dict], tier: str = "strong") -> dict:
        self.calls.append({"task": task, "messages": messages, "tools": tools, "tier": tier})
        key = f"{task}:tools"
        if key in self.responses:
            out = self.responses[key]
            return out() if callable(out) else out
        return {"role": "assistant", "content": "", "tool_calls": []}

    def transcribe(self, audio_path: str) -> str:
        self.calls.append({"task": "transcribe", "audio_path": audio_path})
        return self.responses.get("transcribe", "sermon transcript text")

    def web_search(self, query: str, max_results: int = 8) -> list[dict]:
        self.calls.append({"task": "web_search", "query": query, "max_results": max_results})
        return self.responses.get("web_search", [{"title": "Example", "url": "https://example.com", "snippet": query}])


class FakeWeb:
    """Fixture pages by URL for fetch() tests."""

    def __init__(self, pages: dict[str, dict] | None = None, robots: dict[str, str] | None = None):
        self.pages = pages or {}
        self.robots = robots or {}

    def install(self, monkeypatch) -> None:
        import httpx

        from app import web

        class Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                url = str(request.url)
                if url.endswith("/robots.txt"):
                    domain = request.url.host or ""
                    body = self_outer.robots.get(domain, "User-agent: *\nAllow: /\n")
                    return httpx.Response(200, text=body, request=request)
                page = self_outer.pages.get(url)
                if page is None:
                    return httpx.Response(404, text="not found", request=request)
                return httpx.Response(
                    page.get("status", 200),
                    text=page.get("html", page.get("text", "")),
                    request=request,
                )

        self_outer = self
        web.set_client(httpx.Client(transport=Transport(), follow_redirects=True))
        monkeypatch.setattr(web, "RATE_LIMIT_SEC", 0.0)
        monkeypatch.setattr(web, "_robots", {})
        monkeypatch.setattr(web, "_last_fetch", {})
