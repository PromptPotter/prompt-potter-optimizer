"""An optimizer's own working state, which rides the round document and never the individual.

The bench persists it as ``RoundResult.optimizer_state``, restores it from there on resume and
fork, and reads nothing inside ``payload``."""

from __future__ import annotations

from typing import Any, Literal, Self, TypedDict

from pydantic import Field, model_validator

from promptpotter.domain.l1_layout import L1Layout, default_l1_layout
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import ValidatorOutcome
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure
from promptpotter.shared.hashing import shapes_optimizer_prompt

__all__ = [
    "CAPO_MANIFEST",
    "GEPA_MANIFEST",
    "L1_PARSE_FAILURE_CHARGED",
    "L1_PARSE_FAILURE_MALFORMED",
    "L1_PARSE_FAILURE_TOOLING",
    "L1_PARSE_FAILURE_WRONG_TYPE",
    "LEVI_MANIFEST",
    "POTTER_MANIFEST",
    "CapoRoundState",
    "CritiqueReadout",
    "DescriptorStats",
    "GepaCandidate",
    "GepaRoundState",
    "L2L3Memory",
    "LeviCalibration",
    "LeviElite",
    "LeviRoundState",
    "OptimizerState",
    "PotterRoundState",
    "WoundChannels",
    "potter_round_state",
]

PotterManifest = Literal["potter"]
POTTER_MANIFEST: PotterManifest = "potter"
CapoManifest = Literal["capo"]
CAPO_MANIFEST: CapoManifest = "capo"
LeviManifest = Literal["levi"]
LEVI_MANIFEST: LeviManifest = "levi"
GepaManifest = Literal["gepa"]
GEPA_MANIFEST: GepaManifest = "gepa"

# The reasons `PotterRoundState.l1_parse_failure` can carry. Opposite kinds of evidence, so no
# reader may treat the field as a bool:
#   MALFORMED  — schema-noncompliant output. The optimizer prompt's fault; charge it.
#   WRONG_TYPE — decoded cleanly but as another model, so the fault is the schema it asked
#                for, not the transport. Charged like MALFORMED.
#   TOOLING    — empty/truncated content. Missing data, not a verdict: charging it scores
#                provider flakiness as a bad mutation, so the round must be EXCLUDED.
L1_PARSE_FAILURE_MALFORMED = "optimizer_prompt_parse_failure"
L1_PARSE_FAILURE_WRONG_TYPE = "optimizer_prompt_unexpected_type"
L1_PARSE_FAILURE_TOOLING = "l1_provider_empty_response"
# The reasons a CHARGING reader may hold against the optimizer prompt. Asked as this predicate,
# never as `is not None` — that is the bool the block above forbids, and it reads TOOLING as a
# verdict the round never reached. A ROUTING reader is a different question and may ask either.
L1_PARSE_FAILURE_CHARGED: frozenset[str] = frozenset(
    {L1_PARSE_FAILURE_MALFORMED, L1_PARSE_FAILURE_WRONG_TYPE}
)


class CritiqueReadout(TypedDict, total=False):
    """A domain-local mirror, so a round file round-trips without the optimization layer's
    schema in scope; the optimizer node's full Pydantic shape stays in ``dispatch/schemas.py``."""

    priority_fix: str
    suggested_axes: list[str]
    failure_highlights: list[str]


class WoundChannels(StrictModel):
    """Four wound streams + sticky L3 note; rendered by dispatch-hub injections."""

    l3_note: str = ""
    validation_failures: list[ValidationFailure] = Field(default_factory=list)
    runtime_failures: list[RuntimeFailure] = Field(default_factory=list)
    l2_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)
    l3_guard_breaches: list[ValidatorOutcome] = Field(default_factory=list)


class L2L3Memory(StrictModel):
    """Potter's persistent frame, carried across every adoption.

    The surfaces its escalation layers author: L2 writes most; L3 writes ``plan``,
    ``wounds.l3_note`` and ``wounds.l3_guard_breaches``; the dispatch-hub injections read all of
    it."""

    wounds: WoundChannels = Field(
        default_factory=WoundChannels,
        description=(
            "Four wound streams (validation/runtime/l2-guard/l3-guard) + "
            "sticky L3 note. Rendered by dispatch-hub injections; absorbed "
            "by L2 next round."
        ),
    )
    l1_layout: L1Layout = Field(
        default_factory=default_l1_layout,
        description=(
            "L2-authored ordered list of injection slots that "
            "``DispatchHub.fill`` walks to compose the L1 optimizer prompt. "
            "L2's primary lever for changing what evidence L1 sees."
        ),
    )
    l1_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "L1 optimizer prompt overrides keyed by the surface field name "
            "(``persona``, ``instruction``, …). L2 writes here to nudge L1 "
            "without rewriting the shared optimizer prompt."
        ),
    )
    plan: str = Field(
        default="",
        description=(
            "Strategic frame written by ``l3_plan`` and read by every layer "
            "next round; persistent until the next L3 fire. Empty until L3 "
            "fires for the first time."
        ),
    )


class PotterRoundState(StrictModel):
    """Potter's payload: the memory the round ended on and the readouts only potter reads."""

    memory: L2L3Memory
    # Feedback FOR the next round's `l1_generate`, distilled after this round's scoring.
    critique: CritiqueReadout | None = None
    # STORED, not derived: a fact about GENERATION, fixed before any candidate has a score.
    l1_yield: float = 1.0
    # Why this round's L1 output was unparseable (zero candidates), or None. The round owns it:
    # a parse failure yields no candidate to charge. One of the three constants above.
    l1_parse_failure: str | None = None


class CapoRoundState(StrictModel):
    """CAPO's payload: the population its selector kept.

    The next round draws its parents from it and races it again beside their offspring."""

    # Empty on the origin's document: the initial population is generated by round 1.
    population: list[OptSearchPoint]
    rounds_without_advance: int
    # The longest initial prompt's scored length in characters, which CAPO's objective divides
    # the length term by (§4). ``None`` until round 1 generates that population.
    length_norm: int | None


class DescriptorStats(StrictModel):
    """Welford's running count, mean and squared-deviation sum per descriptor dimension."""

    count: int
    mean: list[float]
    m2: list[float]


class LeviCalibration(StrictModel):
    """What LEVI's calibration round fixes for the run: the proxy and the archive's Voronoi cells."""

    # Sample keys, in the order every later round walks them.
    proxy: list[str]
    centroids: list[list[float]]
    stats: DescriptorStats


class LeviElite(StrictModel):
    """One occupied cell of LEVI's archive: the best individual mapped to it."""

    cell: int
    # The mean per-cell objective over the proxy: LEVI's f, which the cell keeps the best of.
    score: float
    # The round whose rows measured it, which is where its feedback is read back from.
    round: int
    individual: OptSearchPoint


class LeviRoundState(StrictModel):
    """LEVI's payload: its CVT-MAP-Elites archive and what calibration fixed for it."""

    # ``None`` on the origin's document: round 1 is the calibration round that sets it.
    calibration: LeviCalibration | None
    elites: list[LeviElite]
    rounds_without_advance: int


class GepaCandidate(StrictModel):
    """One member of GEPA's candidate pool and its row of the score matrix: its campaign objective
    on each Pareto-set cell, by sample key."""

    individual: OptSearchPoint
    scores: dict[str, float]


class GepaRoundState(StrictModel):
    """GEPA's payload: its candidate pool scored on the Pareto set, and the parent the next round
    mutates."""

    # Sample keys, in the order every round walks them; empty until round 1 draws the split.
    pareto_set: list[str]
    # Empty on the origin's document: round 1 seats the origin with its Pareto-set scores.
    pool: list[GepaCandidate]
    # Drawn at each round's close; ``None`` until round 1 closes, the origin being the only parent.
    parent_id: str | None
    rounds_without_advance: int


_PAYLOADS: dict[str, type[StrictModel]] = {
    POTTER_MANIFEST: PotterRoundState,
    CAPO_MANIFEST: CapoRoundState,
    LEVI_MANIFEST: LeviRoundState,
    GEPA_MANIFEST: GepaRoundState,
}


class OptimizerState(StrictModel):
    """``{manifest, prompt_hashes, payload}`` — the one envelope every optimizer's state rides."""

    manifest: PotterManifest | CapoManifest | LeviManifest | GepaManifest
    # Which prompts of the manifest produced this round, per llm node — the only thing that can
    # answer "was this round produced by the optimizer I am holding now?" once the process exited.
    # Resume diverges at the FIRST round that disagrees. Empty on a generation-only round.
    # IDENTITY, NOT A FIRE RECORD — every node is named on every round, including ones that never
    # run. Which node RAN, and what each panel cost it, is the ledger's `llm_call`.
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    payload: PotterRoundState | CapoRoundState | LeviRoundState | GepaRoundState

    @model_validator(mode="after")
    def _payload_is_the_manifests(self) -> Self:
        if not isinstance(self.payload, _PAYLOADS[self.manifest]):
            raise ValueError(
                f"optimizer_state names {self.manifest!r} but carries a "
                f"{type(self.payload).__name__} payload"
            )
        return self


@shapes_optimizer_prompt
def potter_round_state(state: OptimizerState) -> PotterRoundState:
    """Potter's payload, raising on any other optimizer's — a potter reader handed a peer's round
    is a wiring fault, never a round to read as empty."""
    if not isinstance(state.payload, PotterRoundState):
        raise TypeError(f"a potter reader was handed {state.manifest!r}'s optimizer state")
    return state.payload
