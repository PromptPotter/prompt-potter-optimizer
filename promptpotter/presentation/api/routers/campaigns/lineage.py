"""Campaign lineage — every cycle in a campaign + each cycle's rounds with
candidates + the parent-round where each fork was cut.

One round-trip from the webapp; per-cycle index.json reads are batched
server-side. Backs the cross-cycle search-point cladogram in the dashboard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from promptpotter.application.mask import (
    Verdict,
    find_divergences,
    make_abort_verdict,
    make_scoring_verdict,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import (
    UNATTRIBUTED_OPERATOR,
    ForkTrigger,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.base import read_json_optional
from promptpotter.infrastructure.store.paths import sibling_kind
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError

# An operator-steered fork is a clean offshoot: numbering restarts at 1, so all
# its rounds are post-divergence by definition and the lane sits one column past
# the parent's fork point.
_OPERATOR_RESTART_TRIGGER = ForkTrigger.OPERATOR_STEERED.value


class CampaignLineageCandidate(BaseModel):
    candidate_id: str = Field(description="Stable id assigned at L1-score time")
    label: str = Field(default="", description="Short L1-generated description")
    accuracy: float | None = Field(default=None, description="Per-candidate accuracy")
    rank: int | None = Field(default=None, description="Final rank within the round")
    is_winner: bool = Field(default=False, description="True for the round's elected winner")


class CampaignLineageRound(BaseModel):
    round: int = Field(description="Round number within the cycle (1-indexed)")
    label: str = Field(default="", description="Round label — winner's L1 description")
    accuracy: float | None = Field(default=None, description="Round-level accuracy (winner)")
    candidates: list[CampaignLineageCandidate] = Field(
        description="All candidates scored this round, sorted by rank"
    )


class CampaignLineageCycle(BaseModel):
    cycle_id: str
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    # Immediate parent, read from index.json so sub-forks (forks of forks)
    # attach to their actual parent in the visual tree.
    immediate_parent_cycle_id: str | None
    # Round of the immediate parent at which this cycle's first round was
    # cut. None for roots; may be None for forks whose index didn't record it.
    fork_from_round: int | None
    # Candidate id at the parent's fork_from_round that this fork descends
    # from. Only set when index.json::fork carries from_candidate_id (operator
    # endorse/steered forks); divergence/sweep forks attach at round-level only.
    fork_from_candidate_id: str | None
    # Fork creation trigger — drives the round-numbering convention.
    trigger: str
    # Operator who steered the fork (ForkSpec.issued_by), when attributed.
    # None for non-operator forks and for the unattributed "operator" default.
    # Surfaced as the lineage "edited by {name}" badge on operator_steered forks.
    steered_by: str | None = None
    # X-axis offset for this cycle's rounds in the campaign cladogram —
    # add to each round's ``round`` number to get its absolute column.
    round_column_offset: int
    status: str
    dataset_name: str
    best_accuracy: float | None
    # Origin is round 0 — it rides ``rounds[]`` like any round (no separate
    # trunk anchor). The cladogram renders it through the same round path.
    rounds: list[CampaignLineageRound]


class LineageDivergence(BaseModel):
    """A mask divergence point — the first node on a branch an alternative scoring
    criterion would have forked. Rendered as a marker on that node (not dimmed);
    nodes in ``divergent`` are its counterfactual descendant subtree (dimmed).
    Empty unless the request carried a ``mask`` criterion."""

    node_key: str = Field(description="Lineage node id, formatted `{cycle_id}::r{round}`")
    cycle_id: str
    round: int
    alternative_candidate_id: str | None = Field(
        default=None,
        description="The candidate the masked criterion would have elected instead "
        "(measured, so nameable); null when the round would simply have held on origin.",
    )


class CampaignLineageResponse(BaseModel):
    campaign_id: str
    cycles: list[CampaignLineageCycle] = Field(
        description="Every cycle in the campaign (root + forks + sweeps + diag). "
        "Sorted by cycle id; lay out via immediate_parent_cycle_id."
    )
    # Mask overlay — computed only when the request carries a ``mask`` scoring
    # criterion; both empty otherwise (the unmasked lineage is byte-identical to
    # before). Match a divergence / dimmed node to a tree node by `{cycle_id}::r{round}`.
    divergences: list[LineageDivergence] = Field(default_factory=list)
    divergent: list[str] = Field(
        default_factory=list,
        description="Node keys of the counterfactual subtree to render dimmed.",
    )


def _extract_candidates(scoreboard: list[Any]) -> list[CampaignLineageCandidate]:
    out: list[CampaignLineageCandidate] = []
    for c in scoreboard:
        if not isinstance(c, dict):
            continue
        out.append(
            CampaignLineageCandidate(
                candidate_id=str(c.get("candidate_id") or ""),
                label=str(c.get("label") or ""),
                accuracy=(
                    float(c["accuracy"]) if isinstance(c.get("accuracy"), int | float) else None
                ),
                rank=(int(c["rank"]) if isinstance(c.get("rank"), int) else None),
                is_winner=bool(c.get("is_winner", False)),
            )
        )
    return out


def _round_scoreboard(cycle_dir: Path, round_n: int) -> list[Any]:
    """Per-round candidate scoreboard.

    ``index.json::rounds[]`` carries only the round-level summary (accuracy,
    hits, total) — the per-candidate scoreboard lives in the audit file
    ``rounds/round_NNNN.json::scoreboard`` (``AuditTrailView`` output). The
    cladogram's expanded candidate fan reads from there, so a non-live cycle
    (one not overridden by ``dashboard.json``) still shows its candidates.
    """
    rf = read_json_optional(cycle_dir / "rounds" / f"round_{round_n:04d}.json")
    if isinstance(rf, dict):
        sb = rf.get("scoreboard")
        if isinstance(sb, list):
            return sb
    return []


def _fork_from_round_from_ledger(parent_dir: Path, child_cycle_id: str) -> int | None:
    """Find the FORK_CUT record in *parent_dir* whose outcome is *child_cycle_id*.

    Final fallback when index.json::fork::from_round doesn't carry the
    value. Returns None if the parent's ledger is missing or the record
    isn't there.
    """
    if not (parent_dir / "events.jsonl").is_file():
        return None
    try:
        ledger = CycleEventLog.open(CycleDir(parent_dir))
    except Exception:
        return None
    for rec in ledger.iter():
        if (
            isinstance(rec, ResumeCheckpointRecord)
            and rec.kind is ResumeCheckpointKind.FORK_CUT
            and str(rec.outcome) == child_cycle_id
        ):
            v = rec.inputs_ref.get("from_round")
            if isinstance(v, int):
                return v
    return None


def _filter_post_divergence_rounds(
    rounds: list[CampaignLineageRound], trigger: str, fork_from_round: int | None
) -> list[CampaignLineageRound]:
    """For divergence / sweep / diag forks, drop rounds inherited from the
    parent (round <= fork_from_round). Those rounds belong to the parent's
    lane and would visually overlap if rendered in the fork's lane.

    An operator-steered fork restarts numbering at 1 so all its rounds are
    post-divergence by definition — return as-is.
    """
    if trigger == _OPERATOR_RESTART_TRIGGER:
        return rounds
    if fork_from_round is None:
        return rounds
    return [r for r in rounds if r.round > fork_from_round]


# Abort-lens variants → the PoBB contributor(s) to switch off (the thin API-edge
# selector for the abort verdict; see docs/specs/mask-projection.md).
_ABORT_SUPPRESS: dict[str, frozenset[str]] = {
    "epsilon_off": frozenset({"epsilon"}),
    "lock_in_off": frozenset({"lock_in"}),
    "all_off": frozenset({"epsilon", "lock_in"}),
}


def _resolve_verdict(mask: str | None, abort: str | None) -> Verdict:
    """The thin API-edge selector: a chosen lens → its verdict strategy. ``abort``
    wins when both are sent (one active lens). A bad value is a clean 400."""
    if abort is not None:
        suppress = _ABORT_SUPPRESS.get(abort)
        if suppress is None:
            raise BadRequestError(
                f"Unknown abort lens: {abort!r} (expected one of {sorted(_ABORT_SUPPRESS)})"
            )
        return make_abort_verdict(suppress)
    try:
        return make_scoring_verdict(compile_round_scorer(mask))
    except (ValueError, SyntaxError) as exc:
        raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc


def _parse_samples(samples: str | None) -> frozenset[int] | None:
    """Parse the ``samples`` lens param — a comma-separated sample-id list — into a
    frozenset. Empty / unset ⇒ None (full-set mask). A non-integer token is a 400."""
    if not samples or not samples.strip():
        return None
    try:
        ids = frozenset(int(tok) for tok in samples.split(",") if tok.strip())
    except ValueError as exc:
        raise BadRequestError(f"Invalid samples list: {samples!r} ({exc})") from exc
    return ids or None


def _mask_overlay(
    store: StoreDep,
    campaign_id: str,
    mask: str | None,
    abort: str | None,
    samples: frozenset[int] | None,
) -> tuple[list[LineageDivergence], list[str]]:
    """Run the chosen lens over the realized record → served divergence overlay.

    Read-only: loads the record, folds the selected verdict, returns the markers +
    the dimmed subtree. *samples* (the sample-set mask) re-scores accuracy over the
    subset at load time; it composes with a scoring ``mask`` (a What-If formula
    reweighting the subset accuracy) and is ignored for the ``abort`` lens (which
    reads the recorded firing log, not evaluators). A *known* evaluator simply absent
    from an older round's stored namespace is handled inside the scoring verdict —
    that candidate is skipped, the tree unaffected. The ``NameError`` catch here is
    the backstop for any *other* lazy-eval name failure (clean 400, not a 500).
    """
    verdict = _resolve_verdict(mask, abort)
    record_samples = samples if abort is None else None
    try:
        result = find_divergences(load_mask_record(store, campaign_id, record_samples), verdict)
    except NameError as exc:
        raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc
    divergences = [
        LineageDivergence(
            node_key=d.node_key,
            cycle_id=d.cycle_id,
            round=d.round,
            alternative_candidate_id=d.alternative_candidate_id,
        )
        for d in result.divergences
    ]
    return divergences, result.divergent


@campaigns_router.get(
    "/campaigns/{campaign_id}/lineage",
    response_model=CampaignLineageResponse,
)
async def get_campaign_lineage(
    store: StoreDep,
    campaign_id: str,
    mask: str | None = None,
    abort: str | None = None,
    samples: str | None = None,
) -> CampaignLineageResponse:
    """Aggregated lineage for the whole campaign.

    One pass over every cycle in the campaign — reads each index.json
    (which already carries the per-round scoreboard) and supplements with
    a ledger scan for fork-cut rounds when the index doesn't have them.
    The tree is built from each cycle's ``parent_cycle_id``.

    Optional **lenses** select a divergence overlay. ``mask`` = an alternative
    scoring formula (where that criterion would have forked the record). ``abort`` ∈
    {``epsilon_off``, ``lock_in_off``, ``all_off``} = switch off a PoBB abort
    contributor (``abort`` wins over ``mask`` if both sent). ``samples`` = a
    comma-separated sample-id list (the **sample-set mask**): re-score accuracy over
    only those samples and mark where the subset-best diverges from the recorded
    winner — it composes with ``mask`` (reweight the subset accuracy) and is ignored
    for ``abort``. No lens ⇒ overlay empty, lineage byte-identical to the raw read.
    """
    if store.campaigns.load_campaign(campaign_id) is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    enum_entries = [
        e for e in store.campaigns.enumerate_cycles() if e["campaign_id"] == campaign_id
    ]

    out_cycles: list[CampaignLineageCycle] = []
    for entry in sorted(enum_entries, key=lambda e: e["cycle_id"]):
        cid = entry["cycle_id"]
        cdir = cycle_dir_for(store.base_dir, campaign_id, cid)
        index = read_json_optional(cdir / "index.json")
        if not isinstance(index, dict):
            out_cycles.append(
                CampaignLineageCycle(
                    cycle_id=cid,
                    sibling_kind=sibling_kind(cid),
                    immediate_parent_cycle_id=entry["parent_cycle_id"],
                    fork_from_round=None,
                    fork_from_candidate_id=None,
                    trigger="",
                    round_column_offset=0,
                    status="missing",
                    dataset_name=entry["dataset_name"],
                    best_accuracy=None,
                    rounds=[],
                )
            )
            continue

        immediate_parent = index.get("parent_cycle_id") or None
        _fork = index.get("fork")
        fork_block: dict[str, Any] = _fork if isinstance(_fork, dict) else {}
        trigger = str(fork_block.get("trigger") or "")

        # Two sources for fork_from_round, tried in this order:
        #   1. index.json::fork::from_round
        #   2. parent ledger's FORK_CUT record (last-resort scan)
        from_round: int | None = None
        block_fr = fork_block.get("from_round")
        if isinstance(block_fr, int):
            from_round = block_fr
        elif immediate_parent:
            from_round = _fork_from_round_from_ledger(
                cycle_dir_for(store.base_dir, campaign_id, immediate_parent), cid
            )

        from_candidate = fork_block.get("from_candidate_id")
        from_candidate_str = (
            str(from_candidate) if isinstance(from_candidate, str) and from_candidate else None
        )

        # The operator who steered this fork (ForkSpec.issued_by). "operator"
        # is the unattributed default the dispatcher stamps when the client
        # sends no identity — surface only a real attribution, so the badge
        # reads "edited by {name}" or nothing.
        _issued_by = fork_block.get("issued_by")
        steered_by = (
            str(_issued_by)
            if isinstance(_issued_by, str) and _issued_by and _issued_by != UNATTRIBUTED_OPERATOR
            else None
        )

        rounds_raw = index.get("rounds")
        rounds_out: list[CampaignLineageRound] = []
        if isinstance(rounds_raw, list):
            for r in rounds_raw:
                if not isinstance(r, dict):
                    continue
                rn = r.get("round")
                if not isinstance(rn, int):
                    continue
                # Candidates come from the round audit file; fall back to any
                # index-side scoreboard (none today, but future-proof).
                scoreboard = _round_scoreboard(cdir, rn)
                if not scoreboard and isinstance(r.get("scoreboard"), list):
                    scoreboard = r["scoreboard"]
                rounds_out.append(
                    CampaignLineageRound(
                        round=rn,
                        label=str(r.get("label") or ""),
                        accuracy=(
                            float(r["accuracy"])
                            if isinstance(r.get("accuracy"), int | float)
                            else None
                        ),
                        candidates=_extract_candidates(scoreboard),
                    )
                )

        rounds_out = _filter_post_divergence_rounds(rounds_out, trigger, from_round)
        col_offset = (
            from_round
            if trigger == _OPERATOR_RESTART_TRIGGER and isinstance(from_round, int)
            else 0
        )

        header_raw = index.get("header")
        header = header_raw if isinstance(header_raw, dict) else {}

        out_cycles.append(
            CampaignLineageCycle(
                cycle_id=cid,
                sibling_kind=str(index.get("sibling_kind") or sibling_kind(cid)),
                immediate_parent_cycle_id=immediate_parent,
                fork_from_round=from_round,
                fork_from_candidate_id=from_candidate_str,
                trigger=trigger,
                steered_by=steered_by,
                round_column_offset=col_offset,
                status=str(index.get("status") or ""),
                dataset_name=str(header.get("dataset_name") or entry["dataset_name"]),
                best_accuracy=(
                    float(index["best_accuracy"])
                    if isinstance(index.get("best_accuracy"), int | float)
                    else None
                ),
                rounds=rounds_out,
            )
        )

    sample_ids = _parse_samples(samples)
    divergences, divergent = (
        _mask_overlay(store, campaign_id, mask, abort, sample_ids)
        if (mask or abort or sample_ids)
        else ([], [])
    )
    return CampaignLineageResponse(
        campaign_id=campaign_id,
        cycles=out_cycles,
        divergences=divergences,
        divergent=divergent,
    )
