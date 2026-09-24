"""A CELL — one candidate measured on one sample — as the measurement views serve it.

Every surface that shows a measured sample reads these shapes and no other: the Records →
Measurements log grouped by sample (the hard-sample leaderboard), by candidate, or not at all,
and the detail panel one click opens.

**A cell's address is `(run_id, sample_id)`**: the archive run its row was filed under
(`ScoredCandidate.run_id`) and its position in that run's dataset. Unique in the archive, which
folds last-wins on `m:{sample_id}`. `run_id` is `""` on a row whose report predates the stamp,
and such a cell lists but does not open."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from promptpotter.domain.dashboard_rows import SampleStatus
from promptpotter.domain.strict_model import StrictModel

__all__ = ["Cell", "CellCandidate", "CellRow", "CellSpan"]


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
