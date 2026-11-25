from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from promptpotter.domain.arm_reading import fold_fitness_text
from promptpotter.domain.arms import Cut
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.l4.inner_facts import InnerCellFacts, series_reason_head
from promptpotter.domain.l4.proxies import floor_words
from promptpotter.domain.optimizer_state import CritiqueReadout
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.records.run_records import CycleFinalRecord, RoundStatement
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.ruler import LevelSeries
from promptpotter.infrastructure.projections.audit_trail import round_audits
from promptpotter.infrastructure.store.stores import Stores

# One line's clip: a round shows every arm and cluster, each in a line.
CLAIM_CAP = 200
MECHANISM_CAP = 160
ERROR_CAP = 120


def _clip(text: str, cap: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= cap else flat[: cap - 1] + "…"


def _fired(audit: RoundAudit | None) -> str:
    if audit is None or not audit.nodes:
        return "no optimizer call"
    calls = []
    for node, block in audit.nodes.items():
        repaired = len(block.schema_repair_errors)
        said = f" repaired {repaired}x" if repaired else ""
        failed = f" FAILED: {_clip(block.output.error, ERROR_CAP)}" if block.output.error else ""
        calls.append(f"{node}{said}{failed}")
    return "calls " + ", ".join(calls)


def _level(series: LevelSeries, rnd: int) -> str:
    read = series.reading(rnd)
    if read is None:
        return "θ: no scored cell"
    if read.caveat is not None:
        return f"θ withheld: {series_reason_head(read.caveat)}"
    origin = series.level(0)
    lift = None if origin is None else read.reading.lift_over(origin)
    over = "" if lift is None else f", {lift.point} over the origin"
    return f"θ {read.reading.theta:+.2f}{over}"


def _steer(critique: CritiqueReadout | None) -> list[str]:
    if not critique:
        return ["  acted on no critique"]
    lines = []
    if fix := critique.get("priority_fix"):
        lines.append(f"  critique's priority: {_clip(fix, CLAIM_CAP)}")
    for cluster in critique.get("failure_highlights") or []:
        lines.append(
            f"  cluster {_clip(cluster['mechanism'], MECHANISM_CAP)} "
            f"({len(cluster['sample_ids'])} rows): {_clip(cluster['claim'], CLAIM_CAP)}"
        )
    return lines


def _arm_line(close: RoundStatement, key: ArmKey) -> str:
    arm = close.proposed[key]
    mechanism = _clip(arm.changes_description, MECHANISM_CAP) or "(no mechanism stated)"
    reasons = ", ".join(sorted({v.reason.value for v in arm.validation_failures}))
    flagged = f" ({reasons})" if reasons else ""
    outcome = (arm.kind if isinstance(arm, Cut) else arm.state) + flagged
    if isinstance(arm, Cut) and arm.kind == "invalid":
        return f"  {arm.label} {outcome} — {mechanism}"
    fit = close.fit(key)
    # A lift read short of the parent's whole set stands on cells its stop selected.
    whole = None if fit is None or fit.vs_reference is None else fit.vs_reference.on_whole_set
    measured = f"fitness {fold_fitness_text(close.folds.get(key))}"
    if whole is not None:
        flips = whole.flips
        moved = "" if flips is None else f" (gained {flips.gained}, lost {flips.lost})"
        band = f" {whole.estimate.band}" if whole.estimate.band else ""
        measured += f" lift {whole.estimate.point}{band} {whole.side}{moved}"
    return f"  {arm.label} {outcome} {measured} — {mechanism}"


def _elected(close: RoundStatement) -> str:
    selected = None if close.election is None else close.election.selected
    if selected is None or selected not in close.arms:
        return "elected nothing"
    return f"elected {close.arms[selected].label}"


def _head(hop: CycleHop, final: CycleResult, facts: InnerCellFacts, *, floored: bool) -> str:
    """The stop and the floor's words are its run's, read off the run; the levels, the cell's."""
    head = f"inner run {hop.campaign_id}: stopped {final.stop_reason}"
    if floored:
        said = floor_words(final.stop_reason, final.n_rounds_after_origin)
        return f"{head}, scored at the floor: {said}"
    origin, lift = facts.bench_origin_level, facts.bench_final_lift
    if origin is not None and lift is not None:
        head += f"; its held-out rows from {origin:.3f} to {origin + lift:.3f}"
    return head


def inner_transcript(
    sandbox: Stores | None, hop: CycleHop | None, facts: InnerCellFacts, *, floored: bool
) -> str:
    """Every round the inner run closed, read where it ran (*hop* in *sandbox*, this cycle's):
    what it was told, what each arm tried and how it measured."""
    campaigns = None if sandbox is None else sandbox.campaigns
    campaign = (
        None if campaigns is None or hop is None else campaigns.load_campaign(hop.campaign_id)
    )
    live = campaign is not None and campaign.lifecycle_status != "deleted"
    ended = (
        None
        if not live or campaigns is None or hop is None
        else campaigns.last(hop, CycleFinalRecord)
    )
    if campaigns is None or hop is None or ended is None:
        return (
            "inner run: UNREADABLE — no campaign of this cycle's sandbox ran it; only this cell's "
            "numbers remain."
        )
    head = _head(hop, ended.final, facts, floored=floored)
    standing = campaigns.standing_rounds(hop, moment=None).rounds
    closes = [standing[n].close for n in sorted(standing)]
    if not closes:
        return f"{head}; it closed no round"
    audits = round_audits(Path(campaigns.cycle_dir(hop)), [c.round for c in closes])
    lines = [f"{head}; {len(closes) - 1} rounds closed past its origin"]
    series = closes[-1].level_series
    for (before, close), audit in zip(pairwise(closes), audits[1:], strict=True):
        lines.append(
            f"R{close.round} {_level(series, close.round)}; {_elected(close)}; {_fired(audit)}"
        )
        lines.extend(_steer(before.closed_state.feedback()))
        lines.extend(_arm_line(close, key) for key in close.proposed)
        if not close.proposed:
            lines.append("  no arm proposed")
    return "\n".join(lines)
