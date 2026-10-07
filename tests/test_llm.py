import json
from pathlib import Path

import pytest

from app.llm import complete_json, load_prompt
from tests.fakes import FakeLLM


SCHEMA = {
    "type": "object",
    "required": ["say", "done"],
    "properties": {"say": {"type": "string"}, "done": {"type": "boolean"}},
}


def test_load_prompt():
    text = load_prompt("interviewer.v1")
    assert "Stage 0" in text or "conversation" in text.lower()


def test_fake_llm_by_task_name():
    fake = FakeLLM({"interviewer": {"say": "Hello", "done": False}})
    out = fake.complete_json("interviewer", [], SCHEMA)
    assert out["say"] == "Hello"
    assert out["done"] is False


def test_complete_json_validates_and_repairs(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()

    calls: list[list[dict]] = []

    def fake_chat(model, messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            return "not json", 10, 5
        return json.dumps({"say": "fixed", "done": True}), 12, 6

    monkeypatch.setattr("app.llm._chat", fake_chat)
    monkeypatch.setenv("LLM_BACKEND", "openai")

    out = complete_json("interviewer", [{"role": "user", "content": "hi"}], SCHEMA)
    assert out == {"say": "fixed", "done": True}
    assert len(calls) == 2

    log_path = tmp_path / "logs" / "calls.jsonl"
    assert log_path.is_file()
    row = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["task"] == "interviewer"
    assert "prompt_tokens" in row
    assert "cost_usd" in row


def test_fake_web_fixture(monkeypatch, tmp_path):
    from tests.fakes import FakeWeb

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db import init
    from app.web import fetch

    init()
    FakeWeb(
        pages={
            "https://fixture.church/about": {
                "html": "<html><body><article><p>About our church.</p></article></body></html>"
            }
        },
        robots={"fixture.church": "User-agent: *\nAllow: /\n"},
    ).install(monkeypatch)

    result = fetch("https://fixture.church/about")
    assert result["status"] == 200
    assert "about our church" in result["text"].lower()


def test_complete_json_adds_json_instruction_for_openai(monkeypatch):
    """R1: OpenAI json_object mode 400s unless the word JSON appears in the messages."""
    from app import llm
    seen = {}

    def fake_chat(model, messages, **kw):
        seen["messages"], seen["kw"] = messages, kw
        return '{"ok": true}', 1, 1

    monkeypatch.setattr(llm, "_chat", fake_chat)
    monkeypatch.setattr(llm, "get_settings", lambda: type("S", (), {"llm_backend": "openai", "openai_model_fast": "m", "openai_model_strong": "m",
                                                                    "data_dir": __import__("pathlib").Path(__import__("tempfile").mkdtemp())})())
    llm.complete_json("crisis_check", [{"role": "user", "content": "hello"}], {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}})
    assert "json" in seen["messages"][0]["content"].lower()
    assert seen["kw"]["response_format"] == {"type": "json_object"}


def test_web_search_reads_url_citations(monkeypatch):
    """R9: results come from url_citation annotations, not lines of prose."""
    from types import SimpleNamespace as NS

    from app import llm
    ann = NS(type="url_citation", url="https://umc.org/church/123", title="First UMC", start_index=0, end_index=20)
    resp = NS(output=[NS(content=[NS(type="output_text", text="First UMC in Hesston is a member.", annotations=[ann])])],
              usage=NS(input_tokens=5, output_tokens=5))

    class FakeClient:
        def __init__(self, **kw):
            self.responses = NS(create=lambda **k: resp)

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    out = llm.web_search("First UMC Hesston")
    assert out and out[0]["url"] == "https://umc.org/church/123" and out[0]["title"] == "First UMC"
