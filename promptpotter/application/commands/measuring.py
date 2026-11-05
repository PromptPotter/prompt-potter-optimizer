from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.diagnostics import verify
from promptpotter.application.jobs.launcher.admission import campaign_sends, paid_pass
from promptpotter.application.runner.grade_bench import grade_line_bench
from promptpotter.domain.bench import BenchScore
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_views import VerifyGradedView
from promptpotter.domain.run_records import CommandEffect, NoEffect, VerifySaturated
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_verify
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import DispatchedToCycle
    from promptpotter.domain.command_kinds import GradeBenchPayload, VerifyCandidatePayload


async def verify_candidate(
    dispatched: DispatchedToCycle[VerifyCandidatePayload],
) -> Applier[VerifyGradedView | VerifySaturated]:
    """Answers the graded pass, or what was already measured where that is every sample; a retry
    answers the candidate's pass as its cycle's ledger banked it, or the saturation its ack
    recorded, never a second pass."""
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

    async def _banked(effect: CommandEffect) -> VerifyGradedView | VerifySaturated:
        if isinstance(effect, VerifySaturated):
            return effect
        graded = scan_ledger_verify(stores.campaigns.ledger(hop)).graded
        if (banked := graded.get(ArmKey(payload.candidate_id))) is None:
            raise NotFoundError(f"{hop.cycle_id} banks no verify of {payload.candidate_id}")
        return VerifyGradedView(verify_pass=banked[0], reading=banked[1])

    def _effect(answer: VerifyGradedView | VerifySaturated) -> CommandEffect:
        return answer if isinstance(answer, VerifySaturated) else NoEffect()

    return Applier(_apply_verify, replay=_banked, effect_fn=_effect)


async def grade_bench(dispatched: DispatchedToCycle[GradeBenchPayload]) -> Applier[BenchScore]:
    """Answers the line's headline; a retry answers it as the cycle's export restated it."""
    stores, hop = dispatched.dispatcher.stores, dispatched.hop

    async def _restated(_: CommandEffect) -> BenchScore:
        export = stores.campaigns.read_export(hop)
        if export is None:
            raise NotFoundError(
                f"{hop.campaign_id}/{hop.cycle_id} holds no export to read its bench"
            )
        return export.bench

    return Applier(lambda: grade_line_bench(stores=stores, hop=hop), replay=_restated)
