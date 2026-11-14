from __future__ import annotations

from promptpotter.application.commands.dispatcher import Applier, DispatchedToCycle
from promptpotter.application.jobs.launcher.admission import campaign_sends, paid_pass
from promptpotter.application.scoring import passes
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.command_kinds import (
    GradeBenchPayload,
    VerifyCandidatePayload,
)
from promptpotter.domain.phase_views import VerificationGradedView
from promptpotter.domain.records.run_records import VerifySaturated
from promptpotter.domain.spend import Budget
from promptpotter.shared.measurement_context import MeasurementRole


async def verify_candidate(
    dispatched: DispatchedToCycle[VerifyCandidatePayload],
) -> Applier[VerificationGradedView | VerifySaturated]:
    """Answers the graded pass, or what was already measured where that is every sample; its end
    keeps that answer, so a retry and its task's result read this pass, never a later one."""
    stores, hop, payload = dispatched.dispatcher.stores, dispatched.hop, dispatched.payload

    async def _apply_verify() -> VerificationGradedView | VerifySaturated:
        # Taken HERE, not inside `verify_candidate`: the loop's saturation check calls that under its book.
        async with paid_pass(
            stores=stores,
            pass_kind=MeasurementRole.VERIFY,
            hop=hop,
            declared=Budget(),
            sends=campaign_sends(stores, hop),
            acting=dispatched.command_id,
        ):
            return await passes.verify_candidate(
                stores=stores,
                hop=hop,
                candidate_id=payload.candidate_id,
                samples=payload.samples,
                strategy=payload.strategy,
                seed=payload.seed,
                acting=dispatched.command_id,
            )

    return Applier(_apply_verify)


async def grade_bench(dispatched: DispatchedToCycle[GradeBenchPayload]) -> Applier[BenchScore]:
    """Answers the line's headline, which its end keeps."""
    stores, hop = dispatched.dispatcher.stores, dispatched.hop
    return Applier(
        lambda: passes.grade_line_bench(stores=stores, hop=hop, acting=dispatched.command_id)
    )
