from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.diagnostics import verify
from promptpotter.application.jobs.launcher.admission import paid_pass
from promptpotter.application.runner.grade_bench import grade_line_bench
from promptpotter.domain.spend import Budget

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import DispatchedToCycle
    from promptpotter.domain.command_kinds import GradeBenchPayload, VerifyCandidatePayload


def verify_candidate(dispatched: DispatchedToCycle[VerifyCandidatePayload]) -> Applier[object]:
    stores, hop, payload = dispatched.dispatcher.stores, dispatched.hop, dispatched.payload

    async def _apply_verify() -> verify.VerifyOutcome:
        # Taken HERE, not inside `verify_candidate`: the loop's saturation check calls that under its book.
        async with paid_pass(stores=stores, verb="verify", hop=hop, declared=Budget()):
            return await verify.verify_candidate(
                stores=stores,
                hop=hop,
                candidate_id=payload.candidate_id,
                samples=payload.samples,
                strategy=payload.strategy,
                seed=payload.seed,
            )

    return Applier.silent(_apply_verify)


def grade_bench(dispatched: DispatchedToCycle[GradeBenchPayload]) -> Applier[object]:
    stores, hop = dispatched.dispatcher.stores, dispatched.hop
    return Applier.silent(lambda: grade_line_bench(stores=stores, hop=hop))
