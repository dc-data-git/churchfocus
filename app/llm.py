"""Model client + routing + call log — only place that calls a model (INTERFACES §4)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import logging

import httpx

from app.config import ROOT, get_settings

log = logging.getLogger("app.llm")

# USD per 1M tokens (input, output) for the cost column in calls.jsonl. Unknown models fall back by tier.
# Note: with OpenAI data sharing on, usage inside the daily complimentary pool is actually free.
_MODEL_COST = {"gpt-5.6-luna": (0.20, 1.20), "gpt-5.6-terra": (2.00, 12.00), "gpt-5.6-sol": (5.00, 30.00),
               "gpt-6-sol": (2.00, 10.00), "gpt-6-luna": (0.10, 0.50), "gpt-5.4-mini": (0.75, 4.50), "gpt-5.4-nano": (0.20, 1.25)}
_COST = {"fast": (0.20, 1.20), "strong": (2.00, 12.00)}
_JSON_SYSTEM = ("Respond only with a single JSON object (no prose, no code fences) that matches this JSON schema:\n{schema}")


def load_prompt(name: str) -> str:
    """Load app/prompts/<name>.md (e.g. deep_search.v1)."""
    path = ROOT / "app" / "prompts" / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def _log_path() -> Path:
    p = get_settings().data_dir / "logs" / "calls.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _log_call(
    *,
    task: str,
    model: str,
    ms: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    ok: bool,
) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "model": model,
        "ms": ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(cost_usd, 6),
        "ok": ok,
    }
    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _model_for(tier: Literal["fast", "strong"]) -> str:
    s = get_settings()
    if s.llm_backend == "ollama":
        return s.ollama_model_fast if tier == "fast" else s.ollama_model_strong
    return s.openai_model_fast if tier == "fast" else s.openai_model_strong


def _estimate_cost(tier: str, prompt_tokens: int, completion_tokens: int, model: str = "") -> float:
    inp, out = _MODEL_COST.get(model) or _COST.get(tier, (0.0, 0.0))
    return (prompt_tokens * inp + completion_tokens * out) / 1_000_000


def _validate_schema(obj: dict, schema: dict) -> None:
    required = schema.get("required", [])
    props = schema.get("properties", {})
    for key in required:
        if key not in obj:
            raise ValueError(f"missing required field {key!r}")
    for key, spec in props.items():
        if key not in obj:
            continue
        expected = spec.get("type")
        if expected == "string" and not isinstance(obj[key], str):
            raise ValueError(f"{key!r} must be string")
        if expected == "boolean" and not isinstance(obj[key], bool):
            raise ValueError(f"{key!r} must be boolean")
        if expected == "array" and not isinstance(obj[key], list):
            raise ValueError(f"{key!r} must be array")
        if expected == "object" and not isinstance(obj[key], dict):
            raise ValueError(f"{key!r} must be object")


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _openai_chat(model: str, messages: list[dict], **kw: Any) -> tuple[str, int, int]:
    from openai import OpenAI

    client = OpenAI(api_key=get_settings().openai_api_key)
    resp = client.chat.completions.create(model=model, messages=messages, **kw)
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    pt = usage.prompt_tokens if usage else 0
    ct = usage.completion_tokens if usage else 0
    return text, pt, ct


def _ollama_chat(model: str, messages: list[dict], **kw: Any) -> tuple[str, int, int]:
    url = get_settings().ollama_url.rstrip("/") + "/api/chat"
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if "format" in kw:
        payload["format"] = kw.pop("format")
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = data.get("message", {}).get("content", "")
    pt = data.get("prompt_eval_count", 0)
    ct = data.get("eval_count", 0)
    return text, pt, ct


def _chat(model: str, messages: list[dict], **kw: Any) -> tuple[str, int, int]:
    if get_settings().llm_backend == "ollama":
        return _ollama_chat(model, messages, **kw)
    return _openai_chat(model, messages, **kw)


def complete_json(
    task: str,
    messages: list[dict],
    schema: dict,
    tier: Literal["fast", "strong"] = "fast",
    **kw: Any,
) -> dict:
    model = _model_for(tier)
    t0 = time.perf_counter()
    ok = True
    pt = ct = 0
    raw = ""
    fmt_kw: dict[str, Any] = {}
    if get_settings().llm_backend == "openai":
        # json_object mode REQUIRES the word "JSON" in the messages (else HTTP 400); always add the schema note.
        fmt_kw["response_format"] = {"type": "json_object"}
        messages = [{"role": "system", "content": _JSON_SYSTEM.format(schema=json.dumps(schema))}] + list(messages)
    else:
        fmt_kw["format"] = schema
    try:
        try:
            raw, pt, ct = _chat(model, messages, **fmt_kw)
            obj = json.loads(_strip_fences(raw))
            _validate_schema(obj, schema)
            return obj
        except (json.JSONDecodeError, ValueError, KeyError) as first_err:
            log.warning("complete_json %s: invalid JSON (%s); repairing once", task, first_err)
            repair_msgs = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"That was not valid ({first_err}). Return ONLY a JSON object matching the schema."},
            ]
            raw2, pt2, ct2 = _chat(model, repair_msgs, **fmt_kw)
            pt += pt2
            ct += ct2
            obj = json.loads(_strip_fences(raw2))
            _validate_schema(obj, schema)
            return obj
    except Exception as e:
        ok = False
        log.warning("complete_json %s failed: %s: %s", task, type(e).__name__, e)
        raise
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_call(
            task=task,
            model=model,
            ms=ms,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=_estimate_cost(tier, pt, ct, model),
            ok=ok,
        )


def chat_tools(
    task: str,
    messages: list[dict],
    tools: list[dict],
    tier: Literal["fast", "strong"] = "strong",
    tool_choice: str | None = None,
) -> dict:
    model = _model_for(tier)
    t0 = time.perf_counter()
    ok = True
    pt = ct = 0
    try:
        if get_settings().llm_backend == "ollama":
            raw, pt, ct = _ollama_chat(
                model,
                messages + [{"role": "user", "content": f"Available tools: {json.dumps(tools)}"}],
            )
            log.warning("chat_tools: Ollama backend does not support tool calling here; deep search needs LLM_BACKEND=openai")
            return {"role": "assistant", "content": raw, "tool_calls": []}

        from openai import OpenAI

        client = OpenAI(api_key=get_settings().openai_api_key)
        extra: dict[str, Any] = {"tool_choice": tool_choice} if tool_choice else {}
        # U15/D26: gpt-5.x on /v1/chat/completions rejects function tools unless reasoning_effort is "none".
        effort = get_settings().tools_reasoning_effort
        if effort:
            extra["reasoning_effort"] = effort
        for attempt in range(2):  # one retry on rate limit / server error
            try:
                resp = client.chat.completions.create(model=model, messages=messages, tools=tools, **extra)
                break
            except Exception as e:  # openai.RateLimitError / APIStatusError 5xx / connection errors
                status = getattr(e, "status_code", None)
                if attempt == 0 and (status is None or status == 429 or status >= 500):
                    log.warning("chat_tools %s: %s; retrying in 5 s", task, e)
                    time.sleep(5)
                    continue
                raise
        msg = resp.choices[0].message
        usage = resp.usage
        pt = usage.prompt_tokens if usage else 0
        ct = usage.completion_tokens if usage else 0
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
        return {"role": msg.role, "content": msg.content or "", "tool_calls": tool_calls}
    except Exception:
        ok = False
        raise
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_call(
            task=task,
            model=model,
            ms=ms,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=_estimate_cost(tier, pt, ct, model),
            ok=ok,
        )


def transcribe(audio_path: str) -> str:
    model = get_settings().openai_transcribe_model
    t0 = time.perf_counter()
    ok = True
    try:
        from openai import OpenAI

        client = OpenAI(api_key=get_settings().openai_api_key)
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.create(model=model, file=f)
        return resp.text
    except Exception:
        ok = False
        raise
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_call(task="transcribe", model=model, ms=ms, prompt_tokens=0, completion_tokens=0, cost_usd=0.0, ok=ok)


def web_search(query: str, max_results: int = 8) -> list[dict]:
    """OpenAI Responses API web search. Returns [{title, url, snippet}] from url_citation annotations.
    Billed per call (~$0.01), not covered by complimentary tokens."""
    model = get_settings().openai_model_fast
    t0 = time.perf_counter()
    ok = True
    pt = ct = 0
    try:
        from openai import OpenAI

        client = OpenAI(api_key=get_settings().openai_api_key)
        resp = None
        for tool_type in ("web_search", "web_search_preview"):
            try:
                resp = client.responses.create(model=model, tools=[{"type": tool_type}], input=query)
                break
            except Exception as e:  # older accounts/models only know the preview tool name
                last = e
                continue
        if resp is None:
            raise last  # noqa: F821
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "input_tokens", 0) or 0
        ct = getattr(usage, "output_tokens", 0) or 0
        results: list[dict] = []
        seen: set[str] = set()
        for item in getattr(resp, "output", []) or []:
            for part in getattr(item, "content", []) or []:
                if getattr(part, "type", "") != "output_text":
                    continue
                text = getattr(part, "text", "") or ""
                for a in getattr(part, "annotations", []) or []:
                    if getattr(a, "type", "") != "url_citation":
                        continue
                    url = getattr(a, "url", "") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    start = max(0, (getattr(a, "start_index", 0) or 0) - 200)
                    end = getattr(a, "end_index", None) or (start + 200)
                    results.append({"title": getattr(a, "title", "") or url, "url": url,
                                    "snippet": text[start:end].strip()[:300]})
        return results[:max_results]
    except Exception as e:
        ok = False
        log.warning("web_search failed for %r: %s: %s", query, type(e).__name__, e)
        return []
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        _log_call(task="web_search", model=model, ms=ms, prompt_tokens=pt, completion_tokens=ct,
                  cost_usd=_estimate_cost("fast", pt, ct, model) + 0.01, ok=ok)
