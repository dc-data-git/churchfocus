"""Exercise the real (non-dry-run) client against a mock Ollama HTTP server:
request shape, schema-constrained output, repair loop, cache, call log."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from denomkb.llm import LLM

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["n"],
          "properties": {"n": {"type": "integer"}}}


class Mock(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": "m:7b"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.calls.append({"path": self.path, **req})
        assert req["format"] == SCHEMA and req["stream"] is False
        n_user = sum(1 for m in req["messages"] if m["role"] == "user")
        content = '{"n": "oops"}' if n_user == 1 else '{"n": 3}'   # first answer invalid -> repair
        self._send({"message": {"content": content}, "prompt_eval_count": 10, "eval_count": 5})


def test_real_client_repair_cache_log(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {"provider": "ollama", "base_url": f"http://127.0.0.1:{srv.server_port}", "chat_model": "m:7b",
           "embed_model": "e", "json_repair_attempts": 2, "max_retries": 1}
    llm = LLM(cfg, tmp_path)
    assert "m:7b" in llm.ping()
    msgs = [{"role": "user", "content": "give n"}]
    assert llm.complete_json("t", msgs, SCHEMA) == {"n": 3}
    n_http = len(Mock.calls)
    assert n_http == 2                                   # invalid, then repaired
    assert llm.complete_json("t", msgs, SCHEMA) == {"n": 3}
    assert len(Mock.calls) == n_http                     # served from cache
    even = lambda v: "n must be even" if v["n"] % 2 else None
    sem = llm.complete_json("t2", [{"role": "user", "content": "x"}], SCHEMA, check=even)
    assert sem is None                                   # semantic check never satisfied -> None, logged
    part = llm.complete_json("t3", [{"role": "user", "content": "y"}], SCHEMA, check=even, partial=True)
    assert part == {"n": 3}                              # partial: last schema-valid answer, caller filters
    log = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert log[0]["ok"] and log[0]["repairs"] == 1 and log[0]["prompt_tokens"] == 20
    assert any(not r["ok"] for r in log if r["task"] == "t2")
    srv.shutdown()
