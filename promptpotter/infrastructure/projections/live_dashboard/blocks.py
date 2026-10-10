from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.domain.dashboard_rows import (
    DashboardSample,
    LiveCandidate,
    sample_status,
)
from promptpotter.domain.paired_reading import ArmPointer
from promptpotter.domain.results import (
    ArmAbility,
    ArmElection,
    ArmReading,
    candidate_label,
    panel_cuts,
)
from promptpotter.domain.results_health import terminal_node
from promptpotter.domain.scoring import SampleStatus, ground_truth_text, is_verifier_graded
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.projections.live_dashboard.state import RacingBlock
from promptpotter.shared.composite import inline_short_formula_values

if TYPE_CHECKING:
    from promptpotter.infrastructure.projections.live_dashboard.round_buffer import (
        RoundBuffer,
        WalkedSample,
    )


def _trim(text: str, n: int) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def sample_row(s: WalkedSample) -> DashboardSample:
    facts, grade = s.facts, s.grade
    cost_s = facts.cost_s
    status: SampleStatus = sample_status(facts, grade)
    # A verifier-graded row's `predicted` is the `NO_RESULT` sentinel: served EMPTY, not as an answer.
    ground_truth = _trim(facts.ground_truth, 20)
    graded_by_verifier = is_verifier_graded(ground_truth)
    account = TokenAccount.from_step_tokens(facts.pipeline.step_tokens)
    return DashboardSample(
        qi=s.qi,
        sample_id=facts.sample_id,
        status=status,
        fitness=grade.fitness,
        terminal_node=terminal_node(facts),
        cached=facts.cached,
        cost_s=None if cost_s is None else round(cost_s, 2),
        predicted="" if graded_by_verifier else _trim(facts.predicted, 28),
        ground_truth=ground_truth,
        ground_truth_text=ground_truth_text(ground_truth),
        query=_trim(facts.query, 42),
        input_tokens=account.input if account else None,
        output_tokens=account.output if account else None,
        cache_read_tokens=account.cache_read if account else None,
    )


def build_candidate_rows(
    buffer: RoundBuffer, short_formula_template: str | None
) -> list[LiveCandidate]:
    slots = [buffer.candidates[idx] for idx in sorted(buffer.candidates)]
    cuts = panel_cuts(
        [
            (len(slot.samples), slot.expected_samples)
            if slot.scores is None
            else (slot.scores.scored_samples, slot.scores.expected_samples)
            for slot in slots
        ]
    )
    rows: list[LiveCandidate] = []
    for slot, cut in zip(slots, cuts, strict=True):
        report = slot.scores
        label = candidate_label(buffer.round_num, slot.idx)
        arm = ArmPointer(
            round=buffer.round_num,
            label=label,
            candidate_id=slot.candidate_id or ("" if report is None else report.candidate_id),
        )
        election = ArmElection.of(
            label,
            held=buffer.elected is not None,
            selected=buffer.elected or (),
            leading=None,
            electable=None,
        )
        if report is None:
            reading = ArmReading.walking(
                arm,
                fold=slot.running,
                scored=len(slot.samples),
                expected=slot.expected_samples,
                cached=sum(1 for s in slot.samples if s.facts.cached),
                cut=cut,
                election=election,
                changes_description=slot.changes_description,
            )
        else:
            reading = ArmReading.of(
                arm,
                report,
                cut=cut,
                election=election,
                changes_description=slot.changes_description or report.changes_description,
                ability=ArmAbility.of(report.theta, report.theta_se, report.theta_caveat),
                vs_reference=report.vs_reference,
            )
        served = report or slot.running
        rows.append(
            LiveCandidate(
                reading=reading,
                prompt_fields=slot.prompt_fields,
                resolved_pipeline_params=slot.resolved_pipeline_params,
                pipeline_overlay=slot.pipeline_overlay,
                samples=[sample_row(s) for s in slot.samples],
                validation_failures=[] if report is None else report.validation_failures,
                composite_fitness_formula_short=inline_short_formula_values(
                    short_formula_template, {} if served is None else dict(served.evaluators)
                ),
            )
        )
    return rows


def build_racing_block(buffer: RoundBuffer) -> RacingBlock | None:
    if not buffer.race_standings:
        return None
    ranked = sorted(buffer.race_standings.items(), key=lambda kv: -kv[1])
    return RacingBlock(
        member=buffer.race_member,
        current_id=buffer.race_current_id,
        n_samples=buffer.race_n_samples,
        top=[{"id": cid, "p_best": p} for cid, p in ranked[:5]],
    )


__all__ = [
    "build_candidate_rows",
    "build_racing_block",
    "sample_row",
]
