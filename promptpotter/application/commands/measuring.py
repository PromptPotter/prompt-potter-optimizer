from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.diagnostics import verify
from promptpotter.application.jobs.quota import paid_verb
from promptpotter.application.runner.grade_bench import grade_line_bench

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.payloads import (
        GradeBenchPayload,
        VerifyCandidatePayload,
    )
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop

__all__ = ["grade_bench", "verify_candidate"]


def verify_candidate(
    dispatcher: CommandDispatcher,
    payload: VerifyCandidatePayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    async def _apply_verify() -> verify.VerifyOutcome:
        # Taken HERE, not inside `verify_candidate`: the loop's saturation check calls that under its book.
        async with paid_verb(stores=dispatcher.stores, bucket="verify", hop=hop):
            return await verify.verify_candidate(
                stores=dispatcher.stores,
                hop=hop,
                candidate_id=payload.candidate_id,
                samples=payload.samples,
                strategy=payload.strategy,
                seed=payload.seed,
            )

    return Applier.silent(_apply_verify)


def grade_bench(
    dispatcher: CommandDispatcher, payload: GradeBenchPayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    return Applier.silent(lambda: grade_line_bench(stores=dispatcher.stores, hop=hop))
