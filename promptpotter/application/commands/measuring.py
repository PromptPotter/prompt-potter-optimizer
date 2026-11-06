from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.diagnostics import verify
from promptpotter.application.jobs.launcher.admission import campaign_sends, paid_pass
from promptpotter.application.runner.grade_bench import grade_line_bench
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.phase_views import VerifyGradedView
from promptpotter.domain.run_records import (
    VerifySaturated,
)
from promptpotter.domain.spend import Budget

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import DispatchedToCycle
    from promptpotter.domain.command_kinds import (
        GradeBenchPayload,
        VerifyCandidatePayload,
    )


async def verify_candidate(
    dispatched: DispatchedToCycle[VerifyCandidatePayload],
) -> Applier[VerifyGradedView | VerifySaturated]:
    """Answers the graded pass, or what was already measured where that is every sample; a retry
    answers it as its command's end kept it, its cycle deleted since or not, never a second pass."""
    stores, hop, payload = dispatched.dispatcher.stores, dispatched.hop, dispatched.payload

    async def _apply_verify() -> VerifyGradedView | VerifySaturated:
        # Taken HERE, not inside `verify_candidate`: the loop's saturation check calls that under its book.
        async with paid_pass(
            stores=stores,
            verb="verify",
            hop=hop,
            declared=Budget(),
            sends=campaign_sends(stores, hop),
        ):
            outcome = await verify.verify_candidate(
                stores=stores,
                hop=hop,
                candidate_id=payload.candidate_id,
                samples=payload.samples,
                strategy=payload.strategy,
                seed=payload.seed,
            )
        if outcome.verify_pass is None or outcome.reading is None:
            return VerifySaturated(
                dataset_name=outcome.dataset_name, already_measured=outcome.already_measured
            )
        return VerifyGradedView(verify_pass=outcome.verify_pass, reading=outcome.reading)

    return Applier.keeping(_apply_verify, payload.kind)


async def grade_bench(dispatched: DispatchedToCycle[GradeBenchPayload]) -> Applier[BenchScore]:
    """Answers the line's headline; a retry answers it as its command's end kept it."""
    stores, hop = dispatched.dispatcher.stores, dispatched.hop
    return Applier.keeping(
        lambda: grade_line_bench(stores=stores, hop=hop), dispatched.payload.kind
    )
