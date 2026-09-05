"""What a judge IS, and what an operator declares to use one.

The type boundary is what keeps a judge out of the optimizer loop: a :class:`JudgeSpec` names its
own models and inherits none, so nothing that sets the loop's LLMs can reach a grader.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.domain.pipeline_schema import stable_hash
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = [
    "GradeFn",
    "Judge",
    "JudgeSpec",
    "JudgeStage",
    "JudgeVerdict",
]


class JudgeStage(StrictModel):
    """One model call in a judge's composition. ``model`` and ``provider`` are required and
    declared HERE, nowhere else — a judge that borrowed the loop's model would silently start
    grading with whatever the operator last steered the search to."""

    role: str = "grade"
    """Names this stage within the composition, so a two-stage judge's ledger rows are tellable
    apart. Free-form; it is the ``node`` a token-usage record is filed under."""
    model: str
    provider: str
    temperature: float = 0.0
    """0.0 by default and that is the point: a judge is a RULER. Every re-read of the same
    comparison should land on the same verdict, or the ruler adds variance to a measurement
    rather than resolving it."""
    max_tokens: int | None = None


class JudgeSpec(StrictModel):
    """What a campaign declares to put a judge in its scoring: which judge, on which models.

    Deliberately carries NO version — identity comes from the registered judge itself, so an
    operator cannot forget to bump one."""

    name: str
    """A key in the judge registry (``judges/__init__.py``)."""
    stages: list[JudgeStage]
    """At least one. Order is the composition order and is part of the fingerprint."""

    def model_post_init(self, _ctx: object) -> None:
        if not self.stages:
            raise ValueError(
                f"judge {self.name!r}: declares no stages. A judge needs at least one model to "
                f"ask, and it is never inherited from the loop's configuration."
            )


class JudgeVerdict(StrictModel):
    """One graded cell. Field names follow OpenTelemetry's ``gen_ai.evaluation.result`` event, so
    the observability sinks this repo carries an extra for read a verdict with no translation."""

    name: str
    """Which judge produced this — ``Judge.name``."""
    score: float | None
    """The number the scoring formula reads, in [0, 1]. ``None`` is NOT a zero — it means the judge
    could not grade this cell."""
    label: str = ""
    """The categorical verdict, where the judge has a taxonomy (``CORRECT`` / ``INCORRECT`` /
    ``NOT_ATTEMPTED``)."""
    explanation: str = ""
    """Why. Rendered into the L1 transcript panel, which is the whole reason it is carried:
    "wrong entity" and "right but hedged" are different repairs, and a bare 0.0 says neither."""
    provenance: str = "llm_judge"
    """Who graded. ``llm_judge`` today; the slot exists from day one so a HUMAN rating can occupy
    the same column later without a schema change — which is what ``screen-taste-v0`` needs."""
    error: str = ""
    """Set when the judge failed rather than graded. Distinct from a score of 0, and the
    distinction is load-bearing: a provider hiccup must never be bankable as a wrong answer."""


GradeFn = Callable[["JudgeSpec", "QueryMeasurement"], Awaitable[JudgeVerdict]]
"""How a judge grades one measured cell. It may make one call or several — the arity is the
judge's own business, bounded only by :attr:`Judge.max_stages`."""


@dataclass(frozen=True)
class Judge:
    """A registered judge. Mirrors ``connectors/protocol.py::Connector`` on purpose."""

    name: str
    version: str
    """The judge author's own revision tag, bumped when behaviour changes in a way that invalidates
    prior verdicts. Belt-and-braces — :meth:`fingerprint` hashes the rubric text too."""
    description: str
    rubric: str
    """The grading prompt, verbatim. Hashed into :meth:`fingerprint`, which is why a judge must
    keep its rubric here rather than building it inside ``grade``."""
    grade: GradeFn
    labels: tuple[str, ...] = ()
    """The categorical taxonomy this judge emits, empty for a judge that returns only a score."""
    to_score: Mapping[str, float] = field(default_factory=dict)
    """label → score in [0, 1], declared rather than assumed — a three-way taxonomy has no
    self-evident numeric reading."""
    needs_gold: bool = True
    """True ⇒ this judge compares against a ground truth, so it is undefined on a verifier-graded
    bank. Rides straight through to ``Evaluator.needs_labels``."""
    max_stages: int | None = 1
    """How many of ``JudgeSpec.stages`` this judge actually asks; ``None`` for unbounded.
    ``build_evaluators`` rejects a longer spec at init, because :meth:`fingerprint` hashes the whole
    chain — so a stage nothing reads still re-cuts every archive key and re-pays for every row."""

    def fingerprint(self, spec: JudgeSpec) -> str:
        """What this judge, on these models, IS — folded into measurement identity so swapping any
        part of it re-cuts the archive key instead of replaying old verdicts under a new grader."""
        return stable_hash(
            [
                self.name,
                self.version,
                self.rubric,
                [[s.role, s.provider, s.model, s.temperature, s.max_tokens] for s in spec.stages],
            ]
        )
