"""Local-model client: Ollama or any OpenAI-compatible server (shared design with christianese-lexicon).

Features
- Structured JSON output constrained by a JSON schema.
- Validation + self-correction: invalid output is sent back with the error.
- Disk cache (sqlite) so an interrupted overnight run resumes without re-paying.
- Call log (JSONL) with task, model, latency, token counts, retries, outcome.
- Dry-run mode: stages pass a `fake` function; no network calls are made.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

import jsonschema
import requests

log = logging.getLogger("denomkb.llm")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict, work: Path, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.provider = cfg.get("provider", "ollama")
        self.base = cfg.get("base_url", "http://localhost:11434").rstrip("/")
        key_env = cfg.get("api_key_env") or ""
        self.api_key = os.environ.get(key_env, "") if key_env else ""
        self.timeout = cfg.get("timeout_s", 300)
        self.max_retries = cfg.get("max_retries", 3)
        self.repair_attempts = cfg.get("json_repair_attempts", 2)
        work.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(work / "llm_cache.sqlite", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT)")
        self._lock = threading.Lock()
        self._log_path = work / "calls.jsonl"

    # ---------------------------------------------------------------- cache
    def _key(self, *parts: Any) -> str:
        h = hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return h

    def _get(self, k: str):
        row = self._db.execute("SELECT v FROM cache WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, k: str, v: Any):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)", (k, json.dumps(v)))
            self._db.commit()

    def _log(self, rec: dict):
        with self._lock, open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    # ---------------------------------------------------------------- http
    def _post(self, path: str, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last = None
        for attempt in range(self.max_retries):
            try:
                r = requests.post(self.base + path, json=payload, headers=headers, timeout=self.timeout)
                if r.status_code >= 500:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, LLMError) as e:
                last = e
                wait = 2 ** attempt * 3
                log.warning("request failed (%s), retry in %ss", e, wait)
                time.sleep(wait)
        raise LLMError(f"request failed after {self.max_retries} attempts: {last}")

    def _chat_raw(self, model: str, messages: list[dict], schema: dict, think=None) -> tuple[str, int, int]:
        if self.provider == "ollama":
            payload = {
                "model": model, "messages": messages, "stream": False, "format": schema,
                "options": {"temperature": self.cfg.get("temperature", 0), "num_ctx": self.cfg.get("num_ctx", 8192),
                            "num_predict": self.cfg.get("num_predict", 1500)},   # stops runaway generations
            }
            if think is not None:
                payload["think"] = think
            d = self._post("/api/chat", payload)
            return d["message"]["content"], d.get("prompt_eval_count", 0), d.get("eval_count", 0)
        payload = {
            "model": model, "messages": messages, "temperature": self.cfg.get("temperature", 0),
            "max_tokens": self.cfg.get("num_predict", 1500),
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}},
        }
        d = self._post("/v1/chat/completions", payload)
        u = d.get("usage", {})
        return d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

    # ---------------------------------------------------------------- public
    def complete_json(self, task: str, messages: list[dict], schema: dict, *,
                      model: str | None = None,
                      fake: Callable[[list[dict]], Any] | None = None,
                      check: Callable[[Any], str | None] | None = None,
                      partial: bool = False, think=None,
                      normalize: Callable[[Any], Any] | None = None) -> Any:
        """Return a JSON object matching `schema`.

        `check` is an optional semantic validator returning an error string or None
        (e.g. "phrase not found verbatim in the input"). Schema and semantic errors
        are fed back to the model for up to `json_repair_attempts` repairs.
        Returns None if the model never produced valid output (logged as failure).
        partial=True: if only the semantic check still fails after repairs, return the last
        schema-valid value anyway (caller filters entries individually).
        """
        model = model or self.cfg["chat_model"]
        if self.dry_run:
            if fake is None:
                raise LLMError(f"dry-run: no fake provided for task {task}")
            out = fake(messages)
            self._log({"task": task, "model": "fake", "ok": True, "ms": 0, "repairs": 0})
            return out

        k = self._key("chat", model, messages, schema)
        cached = self._get(k)
        if cached is not None and cached["value"] is not None:   # failed calls are retried, not replayed
            return cached["value"]

        msgs = list(messages)
        t0 = time.time()
        p_tok = c_tok = 0
        err = None
        last_valid = None
        for repair in range(self.repair_attempts + 1):
            content, pt, ct = self._chat_raw(model, msgs, schema, think)
            p_tok += pt
            c_tok += ct
            try:
                value = json.loads(_strip_fences(content))
                if normalize:
                    value = normalize(value)      # models that ignore the format: repair the shape first
                jsonschema.validate(value, schema)
                last_valid = value
                err = check(value) if check else None
            except (json.JSONDecodeError, jsonschema.ValidationError) as e:
                err = f"{type(e).__name__}: {str(e)[:400]}"
            if err is None:
                ms = int((time.time() - t0) * 1000)
                self._log({"task": task, "model": model, "ok": True, "ms": ms, "prompt_tokens": p_tok,
                           "completion_tokens": c_tok, "repairs": repair})
                self._put(k, {"value": value})
                return value
            msgs = msgs + [{"role": "assistant", "content": content},
                           {"role": "user", "content": f"That output was invalid: {err}\nReturn corrected JSON only."}]
        ms = int((time.time() - t0) * 1000)
        self._log({"task": task, "model": model, "ok": False, "ms": ms, "prompt_tokens": p_tok,
                   "completion_tokens": c_tok, "repairs": self.repair_attempts, "error": err})
        result = last_valid if partial else None
        self._put(k, {"value": result})
        return result

    def ping(self) -> str:
        """Quick connectivity check used by `lexicon doctor`."""
        if self.provider == "ollama":
            r = requests.get(self.base + "/api/tags", timeout=10)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            return "models available: " + ", ".join(names)
        r = requests.get(self.base + "/v1/models", timeout=10,
                         headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})
        r.raise_for_status()
        return "models available: " + ", ".join(m["id"] for m in r.json().get("data", []))


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()
