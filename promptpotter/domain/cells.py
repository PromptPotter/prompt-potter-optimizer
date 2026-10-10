from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from promptpotter.domain.results import HardSampleOrder
from promptpotter.domain.ruler import DELTA_STATE_LABEL, DeltaState, RulerStanding
from promptpotter.domain.scoring import SampleStatus, is_hit
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "HARD_SAMPLES_VERSION",
    "Cell",
    "CellCandidate",
    "CellRow",
    "CellSpan",
    "CellsResponse",
    "DatasetItem",
    "HardSampleCell",
    "HardSamples",
    "HeatmapScope",
    "HitSpread",
    "SampleDifficulty",
    "hit_spread",
]

# The tier stops at dataset: samples differ per dataset, so a workspace scope would mean nothing.
HeatmapScope = Literal["cycle", "campaign", "dataset"]

# A reader takes a `hard_samples.json` of any other version as not written.
HARD_SAMPLES_VERSION = 7


class SampleDifficulty(StrictModel):
    """Every number is null outside ``linked``; two also where the scope holds no frontier ability."""

    state: DeltaState
    delta: float | None
    delta_se: float | None
    pick_score: float | None
    p_hat: float | None

    @classmethod
    def on(
        cls,
        ruler: RulerStanding,
        held: tuple[float, float] | None,
        *,
        pick_score: float | None,
        p_hat: float | None,
    ) -> SampleDifficulty:
        delta, delta_se = held if held is not None else (None, None)
        return cls(
            state=ruler.delta_state(held is not None),
            delta=delta,
            delta_se=delta_se,
            pick_score=pick_score,
            p_hat=p_hat,
        )

    @property
    def label(self) -> str | None:
        """What to print in place of the δ; ``None`` when the sample is ``linked``."""
        return DELTA_STATE_LABEL[self.state]


class HardSampleCell(StrictModel):
    candidate: str
    sample_id: int
    fitness: float


class HardSamples(StrictModel):
    """``hard_samples.json``, and the same view folded per request over an archive; it fits nothing."""

    schema_version: int
    cycle_id: str | None
    generated_at: str
    ruler: RulerStanding
    # Stamped θ descending; an individual no round stamped trails, by id.
    candidate_order: list[str]
    theta: dict[str, float]
    # The samples this scope measured: δ descending, then the ones off the ruler; ties by id.
    sample_order: list[int]
    samples: dict[int, SampleDifficulty]
    round_order: list[int]
    cells: list[HardSampleCell]

    def difficulty(self, sample_id: int) -> SampleDifficulty:
        held = self.samples.get(sample_id)
        if held is not None:
            return held
        return SampleDifficulty.on(self.ruler, None, pick_score=None, p_hat=None)


#: `partly` is both a sample some candidates hit and others missed, and one a graded scorer part-credited.
HitSpread = Literal["unmeasured", "never", "partly", "always"]


def hit_spread(graded: Sequence[float]) -> HitSpread:
    if not graded:
        return "unmeasured"
    if all(is_hit(fitness) for fitness in graded):
        return "always"
    return "never" if all(fitness <= 0.0 for fitness in graded) else "partly"


class CellCandidate(StrictModel):
    """Who measured a cell: a round's candidate or, in dataset scope, a configuration."""

    key: str = Field(
        description="What `CellRow.candidate` joins on. Opaque — never parse it; every field it "
        "was built from is served beside it."
    )
    label: str = Field(
        description="`C{round}.{n}` in a campaign; in dataset scope the role the configuration "
        "was first measured under, and the head of its key."
    )
    candidate_id: str | None = Field(
        default=None, description="The individual's lineage id. Null in dataset scope."
    )
    round: int | None = Field(default=None, description="Null in dataset scope.")
    cycle_id: str | None = Field(default=None, description="Null in dataset scope.")
    live: bool = Field(
        default=False,
        description="No score report has landed for it: its walk is still open, or ended with "
        "its producer, so its cells are a prefix of the walk.",
    )
    created_at: str | None = Field(
        default=None,
        description="When the configuration was first measured — dataset scope only.",
    )


class CellRow(StrictModel):
    """One cell as a table row — enough to scan, sort by the served order and open."""

    sample_id: int
    answer: str = Field(description="The address of the answer this row read — what opens it.")
    candidate: str = Field(description="`CellCandidate.key` of the candidate that measured it.")
    status: SampleStatus
    fitness: float | None = Field(
        default=None, description="The graded per-cell score. Null on an ungraded row."
    )
    cached: bool = False
    predicted: str = Field(default="", description="Trimmed for display.")
    seconds: float | None = Field(
        default=None,
        description="What producing the cell cost (`MeasuredCell.cost_s`) — the half that survives "
        "a cache replay. Null where no timing was recorded.",
    )
    input_tokens: int | None = None
    output_tokens: int | None = None


class CellSpan(StrictModel):
    """One pipeline node's part in a cell — the span of the trace."""

    node: str
    model: str | None = None
    provider: str | None = None
    input: str | None = Field(
        default=None,
        description="The prompt this node was sent, RENDERED at read time from the configuration's "
        "own node config and this sample — exactly the interpolation the measurement made, so nothing is "
        "stored twice. Null on a node configured with no prompt.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="The node's config, prompt excepted."
    )
    outputs: dict[str, Any] = Field(
        default_factory=dict,
        description="The observation keys this node emits, as the row banked them.",
    )
    seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cost_usd: float | None = Field(
        default=None, description="What the node's provider reported it billed. Null where none."
    )
    rate_priced_usd: float | None = Field(
        default=None,
        description="What our rate table prices the node's tokens where its provider reported no "
        "bill. Never spent.",
    )
    estimated: bool = Field(
        default=False, description="Token counts from the chars/4 fallback, not the provider."
    )


class Cell(StrictModel):
    """One answer opened — the detail panel's whole read."""

    answer: str
    sample_id: int = Field(description="Its slot in the dataset that measured it.")
    dataset_name: str | None = None
    role: str = Field(default="", description="Why the pass that measured it ran.")
    created_at: str | None = None
    prompt_fields_id: str | None = Field(
        default=None, description="The configuration's address (`ScoredCandidate.sp_hash`)."
    )
    query: str
    ground_truth: str | None = Field(
        description="The label the answer was graded against; null where the cell is "
        "verifier-graded, as `DatasetItem.ground_truth` declares it."
    )
    ground_truth_text: str = Field(
        description="That label as a surface shows it: itself, or the one sentence for a "
        "verifier-graded cell (`domain/scoring.py::ground_truth_text`)."
    )
    predicted: str
    status: SampleStatus
    fitness: float | None = None
    error: str | None = None
    terminal_node: str | None = None
    seconds: float | None = None
    spans: list[CellSpan] = Field(
        default_factory=list, description="One per pipeline node, in chain order."
    )
    other_outputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Everything else the row banked — rankings, diagnostics, turns, evaluator "
        "values — keyed as stored. Attributed to no node because no node declares it.",
    )


class DatasetItem(StrictModel):
    sample_id: int
    query: str
    ground_truth: str | None = Field(
        default=None,
        description="The row's label, or `null` where the cell is VERIFIER-GRADED — a harbor "
        "episode graded by its own task verifier, an L4 inner cycle graded by its proxies. Same "
        "declaration `Sample.ground_truth` makes; a placeholder string would read as a miss on "
        "every row of such a bank.",
    )
    task: str | None = None
    hard_sample_rank: int = Field(
        description="1-based position in the served hard-sample ranking under this response's "
        "`order`. THE ordering — a client renders rows in it and never re-derives one, since "
        "an ordering is a score and a locally-sorted one silently answers a different "
        "question in the same slot. Rows measured in this scope rank first; the rest trail.",
    )
    delta_label: str | None = Field(
        description="What to print in place of the δ where the scope's ruler "
        "(`CellsResponse.ruler`) holds none for this sample — the ruler is not fitted, or does "
        "not carry it. The four numbers below are then null, never 0; null where `delta` stands."
    )
    pick_score: float | None = Field(
        description="The acquisition score (`adaptive_queue_mechanism.pick_value`) of this sample "
        "for a new candidate centred on the scope's frontier ability, on the ruler's δ. Null "
        "off the ruler, and in a scope with no frontier ability (dataset scope)."
    )
    delta: float | None = Field(
        description="The ruler's difficulty δ for this sample (higher = harder)."
    )
    delta_se: float | None = Field(description="SE of that δ (large = barely measured).")
    p_hat: float | None = Field(
        description="Marginal hit probability of that same new candidate "
        "(`adaptive_queue_mechanism.marginal_hit_probability`). Near 0.5 = contested at the "
        "frontier; near 0/1 = predictable. Null wherever `pick_score` is."
    )
    n_measured: int = Field(
        default=0,
        description="GRADED cells of this sample in scope (errored and unscored cells excluded) "
        "— the denominator of `mean_fitness` and `hit_spread`.",
    )
    mean_fitness: float | None = Field(
        default=None, description="Mean graded fitness over those cells; null when none."
    )
    hit_spread: HitSpread = Field(
        description="How often those cells got the sample right: `never`, `partly` or `always` "
        "— `unmeasured` where the scope holds no graded cell of it. Served, because the two "
        "thresholds are the scorer's.",
    )


class CellsResponse(StrictModel):
    """The measurement log of one scope, in served order.

    Samples are ranked, candidates chronological, cells by candidate then walk order. A client
    groups the served lists and never re-sorts them: an ordering is a score."""

    name: str
    scope: HeatmapScope
    order: HardSampleOrder = Field(
        description="The key `samples` are ranked by — the request's `order` when it named one, "
        "else the dataset's `CampaignConfig.hard_sample_order`; `difficulty` wherever the scope "
        "holds no `pick_score` to rank on. Echoed so a client labels what it is showing.",
    )
    ruler: RulerStanding = Field(
        description="The ONE δ ruler every `delta`, `pick_score` and `p_hat` below is read on: "
        "the ruler of the cycle the scope reads, whose walks `cells` are — the requested cycle in "
        "cycle scope, the one campaign scope names "
        "(`application/scoring/measurement_log.py::campaign_scope_cycle`); in dataset scope one "
        "anchored per request on the dataset's archived cells, which no θ was read on."
    )
    samples: list[DatasetItem]
    candidates: list[CellCandidate]
    cells: list[CellRow]
    total_measurements: int = Field(
        description="Graded cells across `samples` — the headline's denominator, served so the "
        "reader adds nothing up."
    )
    total_hits: int
    mean_fitness: float | None = Field(
        description="Mean graded fitness across those cells; null when the scope holds none."
    )
    never_hit: int = Field(description="`samples` whose `hit_spread` is `never`.")
    partly_hit: int = Field(description="`samples` whose `hit_spread` is `partly`.")
    always_hit: int = Field(description="`samples` whose `hit_spread` is `always`.")
