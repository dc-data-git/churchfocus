"""03 seeker mining: find "looking for a church that..." sentences and extract
what people ask for (want / avoid), with verbatim-span checking.

The same extractor is reused by stage 13 on the held-out seeker docs.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from .. import prompts
from ..text import NEGATORS, STOPWORDS, is_content_ngram, ngrams, sentences, tokenize
from ..util import Ctx, write_json, write_jsonl
from .s01_ingest import load_docs

log = logging.getLogger("lexicon.seeker")
NAME = "03_seeker"

REQUEST_PATTERNS = [
    r"\blooking for (?:a|an|the|my)?\s*(?:new\s+)?(?:church|congregation|parish|faith community)",
    r"\b(?:find|finding|found) (?:a|an)?\s*(?:new\s+)?(?:church|congregation|parish)",
    r"\b(?:recommend|recommendations?|suggest|suggestions?) (?:for )?(?:a )?(?:church|churches|parish)",
    r"\bchurch (?:that|which|where|with)\b",
    r"\b(?:want|need|prefer|wish|hoping for|miss) (?:a|an)?\s*(?:church|service|worship|pastor|preaching|community)",
    r"\b(?:deal ?breaker|must have|non-negotiable|red flag)",
    r"\bchurch (?:shopping|hunting|hopping)",
    r"\b(?:new (?:to|in) (?:town|the area|the city))",
]
_REQ_RE = re.compile("|".join(REQUEST_PATTERNS), re.I)


def request_sentences(text: str) -> list[str]:
    """Request sentence plus the following sentence (people often continue)."""
    sents = sentences(text)
    out = []
    for i, s in enumerate(sents):
        if _REQ_RE.search(s):
            chunk = s if i + 1 >= len(sents) else s + " " + sents[i + 1]
            out.append(chunk[:600])
    return out


def _fallback_extract(text: str) -> list[dict]:
    """No-LLM extraction: content bigrams/unigrams; negated ones become 'avoid'."""
    toks = tokenize(text)
    attrs = []
    for n in (2, 1):
        for i, g in enumerate(ngrams(toks, n)):
            if not is_content_ngram(g) or g in ("church", "looking", "find"):
                continue
            neg = any(t in NEGATORS or t.endswith("n't") for t in toks[max(0, i - 3):i])
            attrs.append({"phrase": g, "polarity": "avoid" if neg else "want", "category": "culture"})
    return attrs[:8]


def _fake(messages):
    text = messages[-1]["content"]
    return {"attributes": _fallback_extract(text)}


def extract(ctx: Ctx, text: str) -> list[dict]:
    use_llm = ctx.cfg["seeker"]["use_llm"]
    if not use_llm:
        return _fallback_extract(text)
    low = text.lower()

    def check(v):
        bad = [a["phrase"] for a in v["attributes"] if a["phrase"].lower() not in low]
        return f"these phrases are not verbatim in the text: {bad}" if bad else None

    msgs = [{"role": "system", "content": prompts.SEEKER_EXTRACT_SYSTEM},
            {"role": "user", "content": text}]
    v = ctx.llm.complete_json("seeker_extract", msgs, prompts.SEEKER_SCHEMA,
                              model=ctx.cfg["llm"]["small_chat_model"], fake=_fake, check=check)
    return v["attributes"] if v else []


def normalize_phrase(p: str) -> str:
    toks = [t for t in tokenize(p)]
    while toks and toks[0] in STOPWORDS:
        toks = toks[1:]
    while toks and toks[-1] in STOPWORDS:
        toks = toks[:-1]
    return " ".join(toks)


def mine(ctx: Ctx, docs, cap: int) -> tuple[list[dict], int]:
    rows, n_calls = [], 0
    for doc in docs:
        if doc["side"] != "seeker":
            continue
        for sent in request_sentences(doc["text"]):
            if n_calls >= cap:
                return rows, n_calls
            n_calls += 1
            for a in extract(ctx, sent):
                p = normalize_phrase(a["phrase"])
                if p:
                    rows.append({"doc": doc["id"], "phrase": p, "polarity": a["polarity"],
                                 "category": a["category"]})
    return rows, n_calls


def run(ctx: Ctx) -> dict:
    cap = ctx.cfg["seeker"]["max_requests"]
    rows, n_calls = mine(ctx, load_docs(ctx), cap)
    agg = defaultdict(lambda: {"count": 0, "want": 0, "avoid": 0, "categories": Counter()})
    for r in rows:
        a = agg[r["phrase"]]
        a["count"] += 1
        a[r["polarity"]] += 1
        a["categories"][r["category"]] += 1
    phrases = {p: {**{k: v for k, v in a.items() if k != "categories"},
                   "category": a["categories"].most_common(1)[0][0]} for p, a in agg.items()}
    d = ctx.stage_dir(NAME)
    write_jsonl(d / "attributes.jsonl", rows)
    write_json(d / "phrases.json", dict(sorted(phrases.items(), key=lambda kv: -kv[1]["count"])))
    summary = {"request_chunks": n_calls, "attributes": len(rows), "distinct_phrases": len(phrases)}
    log.info("seeker: %s", summary)
    return summary
