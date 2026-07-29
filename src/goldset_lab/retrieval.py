"""Deterministic dependency-free BM25 retrieval over the local snapshot."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from .bg3 import Chunk


TOKEN_RE = re.compile(r"[\uAC00-\uD7A3]+|[A-Za-z]+(?:[._/-][A-Za-z0-9]+)*|\d+(?:\.\d+)?")


def tokenize(text: str) -> list[str]:
    split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(split_camel)]


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    rank: int
    score: float
    arm: str = "lexical"


class BM25:
    def __init__(self, chunks: Sequence[Chunk], *, k1: float = 1.5, b: float = 0.75):
        if not chunks:
            raise ValueError("at least one chunk is required")
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b
        self.counts = [Counter(tokenize(chunk.title + " " + chunk.text)) for chunk in chunks]
        self.lengths = [sum(count.values()) for count in self.counts]
        self.average_length = sum(self.lengths) / len(self.lengths)
        df: Counter[str] = Counter()
        for count in self.counts:
            df.update(count.keys())
        total = len(chunks)
        self.idf = {
            term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in df.items()
        }

    def search(self, query: str, limit: int = 5) -> list[Hit]:
        terms = tokenize(query)
        scored: list[tuple[int, float]] = []
        for index, (counts, length) in enumerate(zip(self.counts, self.lengths)):
            score = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(1.0, self.average_length)
                )
                score += self.idf.get(term, 0.0) * frequency * (self.k1 + 1) / denominator
            if score:
                scored.append((index, score))
        ordered = sorted(scored, key=lambda item: (-item[1], self.chunks[item[0]].chunk_id))[:limit]
        return [Hit(self.chunks[index], rank, score) for rank, (index, score) in enumerate(ordered, 1)]
