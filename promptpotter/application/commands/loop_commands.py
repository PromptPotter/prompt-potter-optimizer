"""Refused where no running loop will take it: accepted then, the next launch would drop it with nothing done."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier, refused_on_an_arm

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.payloads import (
        OriginGateDecisionPayload,
        PauseCyclePayload,
        SetSampleLookaheadPayload,
        SkipSearchpointPayload,
    )
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop

__all__ = ["origin_gate_decision", "pause_cycle", "set_sample_lookahead", "skip_searchpoint"]


def pause_cycle(
    dispatcher: CommandDispatcher, payload: PauseCyclePayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    return Applier.for_loop(dispatcher.run_state(hop).pause_refusal)


def origin_gate_decision(
    dispatcher: CommandDispatcher,
    payload: OriginGateDecisionPayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    return Applier.for_loop(dispatcher.run_state(hop).gate_refusal)


def skip_searchpoint(
    dispatcher: CommandDispatcher,
    payload: SkipSearchpointPayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    return refused_on_an_arm(campaign) or Applier.for_loop(
        dispatcher.run_state(hop).admission.skip_refusal
    )


def set_sample_lookahead(
    dispatcher: CommandDispatcher,
    payload: SetSampleLookaheadPayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    if payload.cells == 1 and not payload.auto:
        # A disarm leaves the loop nothing to take: the record removes the arming.
        return Applier.silent(lambda: None)
    # `auto` is a mode and stands across launches; one press is spent by its round, so it needs a run.
    return Applier.for_loop(
        "" if payload.auto else dispatcher.run_state(hop).admission.lookahead_refusal
    )
