from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.domain.dashboard_rows import (
    DashboardSample,
    LiveCandidate,
    sample_status,
)
from promptpotter.domain.results import (
    ArmElection,
    ArmReading,
    Proposed,
    arm_name,
    panel_counts,
    panel_cuts,
)
from promptpotter.domain.results_health import terminal_node
from promptpotter.domain.scoring import SampleStatus, ground_truth_text, is_verifier_graded
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.projections.live_dashboard.state import RacingBlock
from promptpotter.shared.composite import inline_short_formula_values
from promptpotter.shared.measurement_context import MeasurementRole

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
        position=s.position,
        sample_key=facts.sample_key,
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
    arms = [
        (key, arm, buffer.fold(key))
        for key, arm in buffer.arms()
        if arm.role != MeasurementRole.PARENT
    ]
    cuts = panel_cuts([panel_counts(arm, fold) for _, arm, fold in arms])
    election = buffer.election
    rows: list[LiveCandidate] = []
    for (key, arm, fold), cut in zip(arms, cuts, strict=True):
        reading = ArmReading.of(
            arm_name(key, arm, buffer.round_num),
            arm,
            fold=fold,
            cut=cut,
            election=ArmElection.of(
                key,
                held=election is not None,
                selected=() if election is None else election.selected,
                leading=None if election is None else election.leading,
                electable=None if election is None else election.electable,
            ),
            fit=None if election is None else election.fit.get(key),
        )
        rows.append(
            LiveCandidate(
                reading=reading,
                prompts=arm.individual.prompts,
                resolved_params=None if isinstance(arm, Proposed) else arm.resolved_params,
                pipeline_overlay=arm.overlay,
                samples=[sample_row(s) for s in buffer.samples[key]],
                validation_failures=arm.validation_failures,
                composite_fitness_formula_short=inline_short_formula_values(
                    short_formula_template, {} if fold is None else dict(fold.evaluators)
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
