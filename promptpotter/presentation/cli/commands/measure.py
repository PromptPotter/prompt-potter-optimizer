"""A replay, never a second run: candidate generation is non-deterministic, so two runs are no A/B."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.account import local_stores
from promptpotter.application.bench.resume_and_fork.ab_replay import ab_replay_cycle
from promptpotter.application.commands import measuring
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.cycle_addressing import launched, named_campaign, resolve_cycle_id
from promptpotter.application.initialization.scoring_scope import open_diagnostic
from promptpotter.application.views.render.primitives import bench_readout
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_kinds import Door, GradeBenchPayload, VerifyCandidatePayload
from promptpotter.domain.cycle_paths import CycleHop, Subject
from promptpotter.domain.paired_reading import READING_STATE_INFO, Estimate
from promptpotter.domain.records.run_records import VERIFY_WORDS, VerifySaturated
from promptpotter.domain.verification import VerifyReading
from promptpotter.presentation.cli.commands.launch import ctrl_c_stops_pass
from promptpotter.presentation.cli.main import CommandResult
from promptpotter.shared.errors import BadRequestError, PayloadInvalidError


async def cmd_ab(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    stores = local_stores(tenant=args.tenant)

    def target() -> CycleHop:
        if args.campaign:
            root = named_campaign(stores, args.campaign).root_hop
            if args.cycle:
                cycle_id = resolve_cycle_id(stores, root.campaign_id, args.cycle)
                return CycleHop(campaign_id=root.campaign_id, cycle_id=cycle_id)
            return root
        hop = launched(stores)
        if not args.cycle:
            return hop
        return CycleHop(
            campaign_id=hop.campaign_id,
            cycle_id=resolve_cycle_id(stores, hop.campaign_id, args.cycle),
        )

    hop = target()
    async with open_diagnostic(stores, hop) as opened:
        report = ab_replay_cycle(hop, opened.session, opened.config)

        scope = f"campaign {report.campaign_id} — {report.n_cycles} cycle(s), {report.n_rounds} round(s)"
        if not report.divergences:
            human = (
                f"A/B replay: {scope} re-derived IDENTICALLY under the current engine + scorer "
                f"'{report.scorer_id}'. Nothing departs: every measurement in this lineage still "
                "carries over, so a change of this size needs no fork."
            )
        else:
            lines = [
                f"A/B replay: {scope} under scorer '{report.scorer_id}' — the change departs the "
                f"record at {len(report.divergences)} point(s), leaving {len(report.divergent)} "
                "round(s) counterfactual. Fork at each departure to carry the rest forward:"
            ]
            for d in report.divergences:
                alt = (
                    f" → would elect {d.alternative_candidate_id}"
                    if d.alternative_candidate_id
                    else ""
                )
                lines.append(f"  {d.cycle_id}::r{d.round}{alt}")
            lines.append(
                f"  ({report.n_mismatches} decision(s) re-derived differently up to those points; "
                "beyond them nothing was replayed, because it describes a history that would not "
                "have happened.)"
            )
            lines += [
                f"    round {d.round_num} [{d.kind}]: recorded={d.recorded_outcome!r} "
                f"→ current={d.current_outcome!r}"
                for d in report.mismatches
            ]
            human = "\n".join(lines)

        return CommandResult(data=report.to_dict(), human=human)


logger = logging.getLogger("promptpotter.presentation.cli.bench")


async def cmd_bench(args: argparse.Namespace) -> CommandResult:
    store = local_stores(tenant=args.tenant)
    campaign_id = args.campaign or launched(store).campaign_id
    call = CommandCall(GradeBenchPayload(campaign_id=campaign_id), door=Door.TERMINAL)
    dispatcher = CommandDispatcher(store)
    with ctrl_c_stops_pass(dispatcher, call):
        sent = await dispatcher.answer(measuring.grade_bench, call)
    score, graded = sent.result, sent.payload.campaign_id
    logger.info("bench: %s -> graded", graded)
    return CommandResult(
        data=score.model_dump(mode="json"), human=f"{graded} -> bench: {bench_readout(score)}"
    )


def _level(level: Estimate | None) -> str:
    return "—" if level is None else level.text


async def cmd_verify(args: argparse.Namespace) -> CommandResult:
    setup_logging(style="full" if args.verbose else "cli")
    spec = Subject.parse(args.subject)
    if spec.kind != "candidate" or spec.lens or spec.samples:
        raise BadRequestError(
            "verify takes one searchpoint and no mask: "
            "`candidate:<campaign>/<cycle>/<candidate_id>[;in=<c::y~…>]`."
        )
    stores = local_stores(tenant=args.tenant)
    fields = {
        **spec.command_fields,
        "candidate_id": spec.candidate_id,
        "samples": args.samples,
        "strategy": args.strategy,
        "seed": args.seed,
    }
    payload = PayloadInvalidError.validating(VerifyCandidatePayload, fields)
    call = CommandCall(payload, door=Door.TERMINAL)
    dispatcher = CommandDispatcher(stores)
    with ctrl_c_stops_pass(dispatcher, call):
        dispatched = await dispatcher.answer(measuring.verify_candidate, call)

    graded = dispatched.result
    if isinstance(graded, VerifySaturated):
        said = VERIFY_WORDS["saturated"].format(**graded.model_dump())
        return CommandResult(human=f"{spec.candidate_id}: {said}")
    reading = graded.reading
    assert isinstance(reading, VerifyReading), "a graded verify pass reads under its scorer"

    verdict = {True: " — held", False: " — DROPPED", None: ""}[reading.held]
    lines = [
        f"{reading.label}: fitness {_level(reading.recorded.fitness)} on its round's "
        f"{reading.recorded.n} cells → {_level(reading.fresh.fitness)} on {reading.fresh.n} "
        f"unseen ({reading.strategy}){verdict}",
        f"  composite {_level(reading.recorded.composite)} → {_level(reading.fresh.composite)}",
    ]
    pair = reading.vs_origin
    if pair.headline is None or pair.coverage is None:
        lines.append(f"  lift over C0: {READING_STATE_INFO[pair.state].sentence}")
    else:
        lifts = ", ".join(
            f"{lift.measurand.key} {lift.estimate.text}" for lift in (pair.headline, *pair.beside)
        )
        lines.append(f"  lift over C0, paired on {pair.coverage.scored} shared cells: {lifts}")
    return CommandResult(data=reading.model_dump(mode="json"), human="\n".join(lines))
