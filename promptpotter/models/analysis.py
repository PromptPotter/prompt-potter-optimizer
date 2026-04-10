"""Data models for M8 Wave 1b evaluation analysis.

Pure data containers — no I/O, no service dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class FailurePattern:
    """A cluster of query failures sharing the same diagnostic signature."""

    name: str
    query_count: int
    fraction: float
    diagnostic_key: tuple[str, ...]
    example_queries: list[str] = field(default_factory=list)
    signals: dict[str, object] = field(default_factory=dict)


@dataclass
class FailureAnalysis:
    """Structured failure analysis over a set of evaluation results."""

    patterns: list[FailurePattern] = field(default_factory=list)
    total_failures: int = 0
    total_results: int = 0


DifficultyClass = Literal["easy", "discriminating", "hard", "dead"]


@dataclass
class QueryProfile:
    """Per-query difficulty profile across evaluations."""

    query: str
    hit_rate: float
    n_measurements: int
    classification: DifficultyClass


@dataclass
class QueryDifficulty:
    """Aggregate query difficulty classification."""

    profiles: list[QueryProfile] = field(default_factory=list)

    @property
    def easy(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "easy"]

    @property
    def discriminating(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "discriminating"]

    @property
    def hard(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "hard"]

    @property
    def dead(self) -> list[QueryProfile]:
        return [p for p in self.profiles if p.classification == "dead"]
