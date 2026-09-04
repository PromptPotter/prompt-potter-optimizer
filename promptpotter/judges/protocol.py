"""What a judge IS, and what an operator declares to use one.

A judge grades one cell by asking a model, where no deterministic matcher can. It is **scoring
infrastructure** — not part of the optimizer loop, not the backend under test — and the type
boundary here is what keeps it that way: a :class:`JudgeSpec` names its own models and inherits
none, so nothing that sets the loop's LLMs can reach a judge.

The judge itself is never a formula term. It produces an OBSERVATION at measure time, banked into
the row, which the pure scoring formula then reads by name — see ``judges/CLAUDE.md``.
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
    """One model call in a judge's composition.

    A judge is a LIST of these, not a single call: a grader followed by a tie-breaker, or a panel
    voted across. One stage is the common case and never the assumed one.

    **``model`` and ``provider`` are required and are declared HERE, nowhere else.** They are not
    defaulted from ``campaign.allowed_models``, from ``nodes.{node}.config.model``, or from the
    optimizer's own pipeline: a judge that borrowed the loop's model would silently start grading
    with whatever the operator last steered the search to.
    """

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

    Deliberately NOT carrying a version — the judge's identity comes from the registered judge
    itself (its declared ``version`` plus a hash of its actual rubric text), so an operator cannot
    forget to bump it and a rubric edit moves the fingerprint whether or not anyone remembered.
    That is stronger than every published judge abstraction surveyed, and it is not gold-plating:
    an archive row is keyed on config, so a judge that changed without saying so would have its
    old verdicts replayed under the new grader.
    """

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
    """One graded cell.

    Field names follow OpenTelemetry's ``gen_ai.evaluation.result`` event — the only shape the
    ecosystem is converging on (MLflow, Phoenix, LangSmith and pydantic-evals all land on
    ``name`` / ``score`` / ``label`` / ``explanation``). Adopting the names costs nothing and is
    what lets the observability sinks this repo already carries an extra for read a verdict
    without a translation table.
    """

    name: str
    """Which judge produced this — ``Judge.name``."""
    score: float | None
    """The number the scoring formula reads, in [0, 1]. ``None`` is NOT a zero: it means the judge
    could not grade this cell, which the materializer turns into an ABSENT term so the formula
    halts loud rather than scoring a miss nobody measured."""
    label: str = ""
    """The categorical verdict, where the judge has a taxonomy (``CORRECT`` / ``INCORRECT`` /
    ``NOT_ATTEMPTED``). A label is not a score — :attr:`Judge.to_score` is the declared mapping."""
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
"""How a judge grades one measured cell. Async because it reaches a model; it may make one call,
several in sequence, or several in parallel — the arity is the judge's own business and no part
of the scoring seam knows about it."""


@dataclass(frozen=True)
class Judge:
    """A registered judge. Mirrors ``connectors/protocol.py::Connector`` on purpose: same frozen
    bundle, same registry, same "a capability is DECLARED, never sniffed" discipline."""

    name: str
    version: str
    """The judge author's own revision tag. Bumped when behaviour changes in a way that
    invalidates prior verdicts. Belt-and-braces only — :meth:`fingerprint` also hashes the rubric,
    so a text edit moves identity even when this is left alone."""
    description: str
    rubric: str
    """The grading prompt, verbatim. Hashed into :meth:`fingerprint`, which is why a judge must
    keep its rubric here rather than building it inside ``grade``."""
    grade: GradeFn
    labels: tuple[str, ...] = ()
    """The categorical taxonomy this judge emits, empty for a judge that returns only a score."""
    to_score: Mapping[str, float] = field(default_factory=dict)
    """label → score in [0, 1]. Declared rather than assumed: a three-way taxonomy has no
    self-evident numeric reading, and picking one silently is how ``NOT_ATTEMPTED`` becomes a
    zero that flatters an evasive model."""
    needs_gold: bool = True
    """True ⇒ this judge compares against a ground truth, so it is undefined on a verifier-graded
    bank. Rides straight through to ``Evaluator.needs_labels``."""

    def fingerprint(self, spec: JudgeSpec) -> str:
        """What this judge, on these models, IS — folded into measurement identity so swapping any
        part of it re-cuts the archive key instead of replaying old verdicts under a new grader.

        Covers the whole composition, not one model pin: two judges differing only in their second
        stage must not collide.
        """
        return stable_hash(
            [
                self.name,
                self.version,
                self.rubric,
                [[s.role, s.provider, s.model, s.temperature, s.max_tokens] for s in spec.stages],
            ]
        )
