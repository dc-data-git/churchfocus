from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path) -> tuple[dict, Path]:
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f), path.parent


def write_json(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
