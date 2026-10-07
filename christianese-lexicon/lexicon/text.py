"""Tokenizing, scrubbing, sentence splitting, n-grams, term matching."""
from __future__ import annotations

import re
from typing import Iterable

STOPWORDS = set("""
a an the and or but if then so of to in on at by for with from as is are was were be been being am
i me my we our us you your he him his she her it its they them their this that these those there here
do does did doing have has had having not no nor can could will would should may might must shall
just very too also than more most such some any all each every both few many much own same other
what which who whom whose when where why how about into over under again further once up down out off
only s t d ll m re ve y o don doesn didn isn aren wasn weren won wouldn shouldn couldn i'm it's we're
you're they're that's there's let's get got go going like really one would've
""".split())

NEGATORS = {"not", "no", "never", "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't",
            "didn't", "without", "anti", "non", "nothing", "zero", "less", "hate", "avoid"}

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_HANDLE_RE = re.compile(r"(?<![\w@])@\w{2,}")
_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")


def scrub(text: str, cfg_privacy: dict) -> str:
    if cfg_privacy.get("scrub_urls", True):
        text = _URL_RE.sub(" [url] ", text)
    if cfg_privacy.get("scrub_emails", True):
        text = _EMAIL_RE.sub(" [email] ", text)
    if cfg_privacy.get("scrub_handles", True):
        text = _HANDLE_RE.sub(" [user] ", text)
    if cfg_privacy.get("scrub_phones", True):
        text = _PHONE_RE.sub(" [phone] ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("—", " - ").replace("–", "-")
    return text


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(text).lower())


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(normalize(text)) if s.strip()]


def ngrams(tokens: list[str], n: int) -> Iterable[str]:
    for i in range(len(tokens) - n + 1):
        yield " ".join(tokens[i:i + n])


def is_content_ngram(gram: str) -> bool:
    """Reject n-grams that start or end with a stopword, or are all digits."""
    parts = gram.split()
    if parts[0] in STOPWORDS or parts[-1] in STOPWORDS:
        return False
    if all(p.isdigit() for p in parts):
        return False
    if len(parts) == 1 and (len(parts[0]) < 3 or parts[0] in STOPWORDS):
        return False
    return True


def term_tokens(term: str) -> list[str]:
    return tokenize(term)


def find_term(tokens: list[str], term_toks: list[str]) -> list[int]:
    """Start indices where term_toks occurs in tokens."""
    n = len(term_toks)
    if n == 0:
        return []
    first = term_toks[0]
    return [i for i in range(len(tokens) - n + 1)
            if tokens[i] == first and tokens[i:i + n] == term_toks]


def negated_at(tokens: list[str], i: int, lookback: int = 3) -> bool:
    return any(t in NEGATORS or t.endswith("n't") for t in tokens[max(0, i - lookback):i])
