"""
Keyword (BM25) search over the chunks of the active document versions.

Vector search is good at meaning and weak at exact tokens such as error codes, identifiers, names
and numbers. BM25 is the opposite, so the two are fused (see ``store_retriever``).

The index is held in memory and is cheap to build (tokenising the chunk texts). It is rebuilt
whenever the set of active versions changes. Also reports *coverage*: the share of the query's
keyword weight (IDF) that a passage contains, which the context builder uses as keyword evidence.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_COMPOUND = re.compile(r"[^\W_]+(?:[-_./][^\W_]+)+", re.UNICODE)   # E-4021, v1.2.3, user_id

STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from had has have how i if in into is it its
    of on or our should so than that the their then there these they this those to was we were what when
    where which who whom why will with would you your about after all also any been being both each
    more most other over some such their them than very""".split()
)


def tokenize(text: str, *, drop_stopwords: bool = False) -> list[str]:
    lowered = text.lower()
    tokens = _WORD.findall(lowered) + _COMPOUND.findall(lowered)   # compounds are indexed in addition to parts
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [t for t in tokens if len(t) > 1 or t.isdigit()]


@dataclass(frozen=True)
class LexicalHit:
    index: int       # position in the list the index was built from
    score: float     # raw BM25
    coverage: float  # 0-1 weight of query terms present in the passage


class BM25Index:
    def __init__(self, texts: Sequence[str], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._tf: list[Counter[str]] = [Counter(tokenize(t)) for t in texts]
        self._len = [sum(tf.values()) for tf in self._tf]
        self.size = len(texts)
        self._avg_len = (sum(self._len) / self.size) if self.size else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._df = df
        self._postings: dict[str, list[int]] = {}
        for i, tf in enumerate(self._tf):
            for term in tf:
                self._postings.setdefault(term, []).append(i)

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1.0 + (self.size - df + 0.5) / (df + 0.5))

    def _query_terms(self, query: str) -> list[str]:
        return list(dict.fromkeys(tokenize(query, drop_stopwords=True)))

    def _score(self, doc: int, terms: Sequence[str]) -> tuple[float, float]:
        tf = self._tf[doc]
        norm = self._k1 * (1 - self._b + self._b * (self._len[doc] / self._avg_len if self._avg_len else 1.0))
        score = matched = total = 0.0
        for term in terms:
            idf = self._idf(term)
            total += idf
            freq = tf.get(term, 0)
            if freq:
                score += idf * (freq * (self._k1 + 1)) / (freq + norm)
                matched += idf
        return score, (matched / total if total else 0.0)

    def search(self, query: str, limit: int) -> list[LexicalHit]:
        terms = self._query_terms(query)
        if not terms or self.size == 0 or limit < 1:
            return []
        candidates = {doc for term in terms for doc in self._postings.get(term, ())}
        hits = []
        for doc in candidates:
            score, coverage = self._score(doc, terms)
            hits.append(LexicalHit(doc, score, coverage))
        hits.sort(key=lambda h: (-h.score, h.index))
        return hits[:limit]

    def coverage(self, doc: int, query: str) -> float:
        terms = self._query_terms(query)
        return self._score(doc, terms)[1] if terms else 0.0
