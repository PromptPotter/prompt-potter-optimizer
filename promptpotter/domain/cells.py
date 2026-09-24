"""A CELL — one candidate measured on one sample — as the measurement views serve it.

Every surface that shows a measured sample reads these shapes and no other: the Records →
Measurements log grouped by sample (the hard-sample leaderboard), by candidate, or not at all,
and the detail panel one click opens.

**A cell's address is `(run_id, sample_id)`**: the archive run its row was filed under
(`ScoredCandidate.run_id`) and its position in that run's dataset. Unique in the archive, which
folds last-wins on `m:{sample_id}`. `run_id` is `""` on a row whose report predates the stamp,
and such a cell lists but does not open."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from promptpotter.domain.dashboard_rows import SampleStatus
from promptpotter.domain.results import HardSampleOrder
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "Cell",
    "CellCandidate",
    "CellRow",
    "CellSpan",
    "CellsResponse",
    "DatasetItem",
    "HeatmapScope",
]

# `cycle` (one cycle's Rasch fit) / `campaign` (pooled) / `dataset` (cross-campaign archive).
# Workspace scope would be meaningless (samples differ per dataset), so the tier stops at dataset.
HeatmapScope = Literal["cycle", "campaign", "dataset"]


class CellCandidate(StrictModel):
    """Who measured a cell — a round's candidate in cycle and campaign scope, an archive run in
    dataset scope, where no campaign names one. Served in chronological order, which is the order
    a client lists them in."""

    key: str = Field(
        description="What `CellRow.candidate` joins on. Opaque — never parse it; every field it "
        "was built from is served beside it."
    )
    label: str = Field(description="`C{round}.{n}` in a campaign; the run's name in dataset scope.")
    candidate_id: str | None = Field(
        default=None, description="The individual's lineage id. Null in dataset scope."
    )
    run_id: str = Field(
        description='The archive run its cells were filed under. `""` on a report older than '
        "the stamp — its cells list but do not open."
    )
    round: int | None = Field(default=None, description="Null in dataset scope.")
    cycle_id: str | None = Field(default=None, description="Null in dataset scope.")
    live: bool = Field(
        default=False,
        description="Read off the round still being measured (`dashboard.json`), whose round file "
        "lands only at its close.",
    )
    created_at: str | None = Field(
        default=None, description="When the run was banked — dataset scope only."
    )


class CellRow(StrictModel):
    """One cell as a table row — enough to scan, sort by the served order and open."""

    sample_id: int
    run_id: str = Field(description="With `sample_id`, the cell's address. See `CellCandidate`.")
    candidate: str = Field(description="`CellCandidate.key` of the candidate that measured it.")
    status: SampleStatus
    fitness: float | None = Field(
        default=None, description="The graded per-cell score. Null on an ungraded row."
    )
    cached: bool = False
    predicted: str = Field(default="", description="Trimmed for display.")
    seconds: float | None = Field(
        default=None,
        description="What producing the cell cost (`recorded_cost_s`) — the half that survives "
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
        description="The prompt this node was sent, RENDERED at read time from the run's own node "
        "config and this sample — exactly the interpolation the measurement made, so nothing is "
        "stored twice. Null on a node configured with no prompt.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="The node's config for this run, prompt excepted."
    )
    outputs: dict[str, Any] = Field(
        default_factory=dict,
        description="The observation keys this node emits, as the row banked them.",
    )
    seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cost_usd: float | None = None
    estimated: bool = Field(
        default=False, description="Token counts from the chars/4 fallback, not the provider."
    )


class Cell(StrictModel):
    """One cell opened — the detail panel's whole read."""

    run_id: str
    sample_id: int
    dataset_name: str | None = None
    run_name: str = Field(default="", description="The run's own name (its measuring label).")
    created_at: str | None = None
    prompt_fields_id: str | None = Field(
        default=None, description="The configuration's address (`ScoredCandidate.sp_hash`)."
    )
    query: str
    ground_truth: str
    predicted: str
    status: SampleStatus
    fitness: float | None = None
    cached: bool = False
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
    n_obs: int | None = Field(
        default=None,
        description=(
            "Times this sample has been tried. ``null`` where the row is not in this scope's "
            "Rasch artifact at all — the same absence its `delta` / `delta_se` / `p_hat` "
            "neighbours already report, and not a fit that observed it zero times."
        ),
    )
    pick_score: float | None = Field(
        default=None,
        description=(
            "Queue-mechanism's blended objective on this sample for a brand-new candidate (prior "
            "N(0, sigma_theta**2)) vs the best fitted candidate. The live adaptive queue "
            "mechanism re-evaluates per step. None when unmeasured."
        ),
    )
    delta: float | None = Field(
        default=None,
        description="Rasch difficulty delta_s (higher = harder). None when unmeasured.",
    )
    delta_se: float | None = Field(
        default=None,
        description="SE of delta_s (large = barely measured). None when unmeasured.",
    )
    p_hat: float | None = Field(
        default=None,
        description=(
            "Marginal hit prob the seed-centred decision-IG reads — see "
            "``adaptive_queue_mechanism.marginal_hit_probability``. Near 0.5 = contested at seed; "
            "near 0/1 = predictable. None when unmeasured."
        ),
    )
    n_measured: int = Field(
        default=0,
        description="GRADED cells of this sample in scope (errored and unscored cells excluded, as "
        "the Rasch fit excludes them) — the denominator of the two below.",
    )
    n_hits: int = Field(
        default=0,
        description="Of those, how many maxed out the active scorer (`domain.scoring.is_hit`). "
        "Structurally 0 on a graded scorer; read `mean_fitness` there.",
    )
    mean_fitness: float | None = Field(
        default=None, description="Mean graded fitness over those cells; null when none."
    )


class CellsResponse(StrictModel):
    """The measurement log of one scope, in served order: ``samples`` ranked (their
    ``hard_sample_rank``), ``candidates`` chronological within a cycle, ``cells`` by candidate,
    then in each candidate's walk order, so the flat list is the run's time series.
    A client GROUPS these — by sample, by candidate or not at all — by bucketing the served list
    under a served key order, and never re-sorts: an ordering is a score."""

    name: str
    scope: HeatmapScope
    row_count: int
    split_test: int | None = Field(
        default=None,
        description="Declared held-out test fold size (not materialized). The training-bank "
        "size is `row_count` above.",
    )
    order: HardSampleOrder = Field(
        description="The key `samples` are ranked by — the request's `order` when it named one, "
        "else the dataset's `CampaignConfig.hard_sample_order`. Echoed so a client that sent "
        "no override can label what it is showing without guessing the default.",
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
