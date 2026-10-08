"""One cycle as a store lists it — the decoded ``index.json`` every survey of a store reads.
``CampaignStore.enumerate_cycles`` is its one producer; ``GET /cycles`` serves it as is."""

from __future__ import annotations

from pydantic import Field

from promptpotter.domain.phases import RunPhase, StopReason
from promptpotter.domain.run_records import MintKind
from promptpotter.domain.strict_model import StrictModel

__all__ = ["CycleListEntry", "SpawnedBy"]


class SpawnedBy(StrictModel):
    """The outer work-item an L4 inner cycle was spawned to measure.

    Stamped at inner-cycle mint (``runner/inner/spawn.py``) — an inner campaign's own
    ids carry no outer provenance (random ``campaign_id``, ``cycle_id`` hashed from its
    OWN origin), so without this the fan-out can only be numbered by launch order.
    """

    outer_cycle_id: str = Field(description="The outer cycle that owns this inner sandbox")
    outer_campaign_id: str = Field(
        description="The outer CAMPAIGN that owns this inner sandbox. Required alongside the cycle because a `cycle_id` is content-addressed on its origin and so is shared by every campaign minted from that origin — the pair is the identity, either half alone is not, and a null here is why two pooled sandboxes on disk could not be attributed after the fact.",
    )
    round: int | None = Field(
        default=None,
        description="Outer round; 0 is the origin (C0). Null when the spawn came from outside any round (the noise-floor diagnostic).",
    )
    candidate_idx: int | None = Field(
        default=None, description="Position in the outer round's population; null for the origin."
    )
    candidate_id: str | None = Field(
        default=None,
        description="The outer candidate's `OptSearchPoint.lineage.id` — stable across rounds; null for the origin.",
    )
    candidate_label: str | None = Field(
        default=None,
        description="Canonical label (`C0` for the origin, else `C{round}.{idx+1}`) — the same string the round file and console use.",
    )
    task: str = Field(
        description="The panel cell this run measured — the outer query, e.g. `justlogic-d234/seed-0` (`inner_tasks.yaml::tasks[].id`). The candidate fields do NOT identify a run: every task runs for every candidate, so one candidate's spawns are as many as the panel has cells and are told apart only by this.",
    )


class CycleListEntry(StrictModel):
    campaign_id: str = Field(description="Campaign the cycle belongs to")
    cycle_id: str
    parent_session_id: str = ""
    parent_cycle_id: str | None = Field(
        default=None,
        description="Immediate parent for siblings (forks/diag); null for roots. Sidebar uses this to nest siblings.",
    )
    dataset_name: str = ""
    backend_id: str = ""
    # What minted this cycle (`MINT_KIND_FOR_TRIGGER`, via `campaign_store/store.py::_mint_kind`).
    # The raw separator is NOT served beside it: the browser parses the id itself (`lib/ids.ts`).
    mint_kind: MintKind
    is_root: bool
    stop_reason: StopReason | None = Field(
        default=None,
        description="Why the cycle ended; null while it has not. Label, outcome and next step "
        "derive from the one STOP_REASON_INFO table — never re-mapped per surface.",
    )
    superseded_by: str | None = Field(
        default=None,
        description="The cycle_id that took this cycle's line, set on the LEFT-BEHIND side of a supersede cut. This is the successor pointer — follow it to find which cycle answers for the campaign; it is a fact of its own precisely so it survives on a parent that had already stopped for its own reason, which `stop_reason` cannot express. Null on a root, an offshoot, and any cycle still holding the line.",
    )
    run_phase: RunPhase = Field(
        default=RunPhase.DETACHED,
        description="The single run-state value (RunPhase). Computed once by derive_run_phase from lifecycle + control flags + freshness; every picker dot and badge reads this, none re-derive it. 'checkin' wins first (the campaign hasn't run); 'terminal' pairs with `stop_reason` for the reason label.",
    )
    best_accuracy: float | None = Field(
        default=None,
        description="The optimizer's own selection score — what it KEPT, read on the rows that "
        "chose it. Never the headline; the campaign's `CampaignSummary.bench` is.",
    )
    origin_accuracy: float | None = Field(
        default=None,
        description="Round 0's accuracy — the origin's measurement, derived from rounds[] (no stored copy). Null until round 0 lands.",
    )
    rounds_closed: int = Field(
        default=0,
        description="Rounds this cycle has closed AFTER the origin — the unit a rounds cap counts.",
    )
    created_at: str = ""
    updated_at: str = ""
    human_intervened: bool = Field(
        default=False,
        description="True once an operator manually intervened (e.g. skip-searchpoint); the cycle is babysat and no longer purely reproducible. Drives the 'babysat' badge; orthogonal to run_phase.",
    )
    spawned_by: SpawnedBy | None = Field(
        default=None,
        description=(
            "Which outer work-item asked for this cycle, when it is an L4 inner "
            "measurement; null for an ordinary campaign, which is the only reason it is "
            "null. Lets the sidebar name an inner run by the candidate that produced it "
            "instead of by launch order."
        ),
    )
