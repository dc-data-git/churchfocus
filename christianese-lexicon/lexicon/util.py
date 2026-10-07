"""Shared helpers: config, JSONL I/O, logging, timing."""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

log = logging.getLogger("lexicon")


@dataclass
class Ctx:
    """Run context passed to every stage."""
    cfg: dict
    root: Path          # directory containing config.yaml
    work: Path          # work_dir
    dry_run: bool
    llm: Any = None     # lexicon.llm.LLM

    def path(self, key: str) -> Path:
        return (self.root / self.cfg[key]).resolve()

    def stage_dir(self, name: str) -> Path:
        d = self.work / name
        d.mkdir(parents=True, exist_ok=True)
        return d


def load_config(path: str | Path) -> tuple[dict, Path]:
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg, path.parent


def read_jsonl(path: str | Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


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


def read_list(path: str | Path) -> list[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s.lower())
    return out


@contextmanager
def timed(label: str):
    t0 = time.time()
    log.info("start %s", label)
    yield
    log.info("done  %s in %.1fs", label, time.time() - t0)


def zscores(values: list[float]) -> list[float]:
    import numpy as np
    a = np.asarray(values, dtype=float)
    if len(a) == 0:
        return []
    sd = a.std()
    if sd == 0:
        return [0.0] * len(a)
    return list((a - a.mean()) / sd)
