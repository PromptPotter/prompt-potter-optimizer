"""Structured domain context for optimizer LLM calls.

Replaces the untyped ``dict[str, Any]`` that was threaded through the
optimization pipeline.  Fields are populated by LLM-assisted decomposition
of user task descriptions and refined by L2 transitions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, ClassVar


@dataclass
class TaskDecomposition:
    """Typed domain context produced by task-description decomposition.

    All fields default to ``""`` so the object is always safe to read
    without ``.get()`` guards.
    """

    domain: str = ""
    pipeline_purpose: str = ""
    data_characteristics: str = ""
    optimization_goals: str = ""
    key_challenges: str = ""
    upstream_context: str = ""
    downstream_context: str = ""
    raw_description: str = ""

    # Display / iteration fields (excludes upstream/downstream/raw_description).
    FIELDS: ClassVar[tuple[str, ...]] = (
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
        "upstream_context",
        "downstream_context",
    )

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, str]:
        """Serialize to plain dict (JSON-safe)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> TaskDecomposition:
        """Construct from dict, ignoring unknown keys."""
        if not d:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    # ── Merge / copy ────────────────────────────────────────────────

    def merge(self, overrides: dict[str, Any]) -> TaskDecomposition:
        """Return a new TaskDecomposition with *overrides* applied on top."""
        base = self.to_dict()
        base.update(overrides)
        return self.from_dict(base)

    # ── Iteration helpers (support existing dict-like access patterns) ──

    def items(self) -> list[tuple[str, str]]:
        """Return (field, value) pairs — mirrors ``dict.items()``."""
        return [(f.name, getattr(self, f.name)) for f in fields(self)]

    def __len__(self) -> int:
        """Number of populated (non-empty) fields."""
        return sum(1 for f in fields(self) if getattr(self, f.name))

    def __bool__(self) -> bool:
        """True if any field is non-empty."""
        return any(getattr(self, f.name) for f in fields(self))
