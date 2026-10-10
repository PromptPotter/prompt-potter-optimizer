from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from promptpotter.domain.scoring import MeasuredCell

__all__ = [
    "GradeFn",
    "Judge",
    "JudgeSpec",
    "JudgeStage",
    "JudgeVerdict",
]


class JudgeStage(StrictModel):
    """One model call in a judge's composition, on a model declared here and never inherited."""

    role: str = "grade"
    """The ``node`` a token-usage record is filed under."""
    model: str
    provider: str
    temperature: float = 0.0
    """0.0: a judge is a RULER, and a re-read landing elsewhere adds variance to the measurement."""
    max_tokens: int | None = None


class JudgeSpec(StrictModel):
    """What a campaign declares to put a judge in its scoring: which judge, on which models."""

    name: str
    stages: list[JudgeStage]
    """Order is the composition order and is part of the fingerprint."""

    def model_post_init(self, _ctx: object) -> None:
        if not self.stages:
            raise ValueError(
                f"judge {self.name!r}: declares no stages. A judge needs at least one model to "
                f"ask, and it is never inherited from the loop's configuration."
            )


class JudgeVerdict(StrictModel):
    name: str
    score: float | None
    """``None`` is NOT a zero: the judge could not grade this cell."""
    label: str = ""
    explanation: str = ""
    """Rendered into the L1 transcript panel: a bare 0.0 cannot tell two repairs apart."""
    provenance: str = "llm_judge"
    """The slot a HUMAN rating takes later with no schema change."""
    error: str = ""
    """Set when the judge FAILED: a provider hiccup must never bank as a wrong answer."""


GradeFn = Callable[["JudgeSpec", "MeasuredCell"], Awaitable[JudgeVerdict]]
"""One call or several: the arity is the judge's own, bounded by ``Judge.max_stages``."""


@dataclass(frozen=True)
class Judge:
    name: str
    version: str
    """Bumped when behaviour changes; :meth:`fingerprint` hashes the rubric text too."""
    description: str
    rubric: str
    """Hashed into :meth:`fingerprint`, so a judge never builds its rubric inside ``grade``."""
    grade: GradeFn
    labels: tuple[str, ...] = ()
    to_score: Mapping[str, float] = field(default_factory=dict)
    """Declared, never assumed: a three-way taxonomy has no self-evident numeric reading."""
    needs_gold: bool = True
    """Compares against a ground truth, so it is undefined on a verifier-graded bank."""
    max_stages: int | None = 1
    """``None``: unbounded. A longer spec is refused at init: an unread stage still re-cuts every key."""

    def fingerprint(self, spec: JudgeSpec) -> str:
        """What this judge, on these models, IS — folded into measurement identity."""
        return stable_hash(
            [
                self.name,
                self.version,
                self.rubric,
                [[s.role, s.provider, s.model, s.temperature, s.max_tokens] for s in spec.stages],
            ]
        )
