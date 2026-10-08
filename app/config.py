"""Settings. The ONLY module that reads environment variables (CONVENTIONS rule 1)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:  # .env is optional
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def _path(v: str) -> Path:
    p = Path(v)
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Settings:
    google_places_api_key: str
    openai_api_key: str
    openai_model_fast: str
    openai_model_strong: str
    openai_transcribe_model: str
    llm_backend: str
    ollama_url: str
    ollama_model_fast: str
    ollama_model_strong: str
    denom_kb_path: Path
    features_path: Path
    data_dir: Path
    deep_max_minutes: int
    deep_max_tool_calls: int
    deep_max_sermons: int
    cache_max_age_days: int
    user_agent: str
    tools_reasoning_effort: str


@lru_cache
def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        google_places_api_key=e("GOOGLE_PLACES_API_KEY", ""),
        openai_api_key=e("OPENAI_API_KEY", ""),
        openai_model_fast=e("OPENAI_MODEL_FAST", ""),
        openai_model_strong=e("OPENAI_MODEL_STRONG", ""),
        openai_transcribe_model=e("OPENAI_TRANSCRIBE_MODEL", ""),
        llm_backend=e("LLM_BACKEND", "openai"),
        ollama_url=e("OLLAMA_URL", "http://localhost:11434"),
        ollama_model_fast=e("OLLAMA_MODEL_FAST", "qwen2.5:7b"),
        ollama_model_strong=e("OLLAMA_MODEL_STRONG", "qwen2.5:14b"),
        denom_kb_path=_path(e("DENOM_KB_PATH", "denom-kb/work/out/denominations_kb.json")),
        features_path=_path(e("FEATURES_PATH", "contracts/features.yaml")),
        data_dir=_path(e("DATA_DIR", "data")),
        deep_max_minutes=int(e("DEEP_MAX_MINUTES", "120")),
        deep_max_tool_calls=int(e("DEEP_MAX_TOOL_CALLS", "150")),
        deep_max_sermons=int(e("DEEP_MAX_SERMONS", "25")),
        cache_max_age_days=int(e("CACHE_MAX_AGE_DAYS", "30")),
        tools_reasoning_effort=e("TOOLS_REASONING_EFFORT", "none"),
        user_agent=e("USER_AGENT", "ChurchFocus/0.1 (hackathon research tool)").replace("ChurchSearch", "ChurchFocus"),
    )
