"""Chunk source documents and retrieve excerpts per field with BM25 (no embedding model,
so the GPU stays free for the chat model on an 8 GB card)."""
from __future__ import annotations

import math
import re
from collections import Counter

_TOK = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
STOP = set("the a an and or of to in on for with by as is are was were be been that this it its at from which "
           "who we our us they their his her he she not but all any can may will shall".split())

# lower = more trusted
SOURCE_RANK = {"official": 0, "register": 0, "wikipedia": 1, "wikidata": 1}


def toks(s: str) -> list[str]:
    return [t for t in _TOK.findall(s.lower()) if t not in STOP]


def chunk_docs(docs: list[dict], words: int = 220, overlap: int = 40) -> list[dict]:
    """docs: {url, title, text, source_type}. Returns chunks with stable ids."""
    out = []
    for d in docs:
        w = d["text"].split()
        if not w:
            continue
        step = words - overlap if 0 <= overlap < words else words
        for i in range(0, len(w), step):
            piece = " ".join(w[i:i + words])
            if len(piece) < 80:
                continue
            out.append({"id": len(out), "url": d["url"], "title": d.get("title", ""), "source_type": d["source_type"],
                        "text": piece})
            if i + words >= len(w):
                break
    return out


class BM25:
    def __init__(self, chunks: list[dict], k1: float = 1.4, b: float = 0.75):
        self.chunks = chunks
        self.docs = [Counter(toks(c["text"] + " " + c["title"])) for c in chunks]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1
        df = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int = 4) -> list[dict]:
        q = toks(query)
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = d.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                # gentle preference for official sources
                s *= 1.0 - 0.1 * SOURCE_RANK.get(self.chunks[i]["source_type"], 1)
                scores.append((s, i))
        scores.sort(reverse=True)
        return [self.chunks[i] for _, i in scores[:k]]


def norm_for_match(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", s.lower())).strip()


def quote_found(quote: str, text: str, threshold: int = 90) -> bool:
    """Verbatim check, tolerant of punctuation/whitespace/small OCR-like differences."""
    q, t = norm_for_match(quote), norm_for_match(text)
    if len(q) < 12:
        return False
    if q in t:
        return True
    from rapidfuzz import fuzz
    return fuzz.partial_ratio(q, t) >= threshold
