"""Refused where no running loop will take it: accepted then, the next launch would drop it with nothing done."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.commands.dispatcher import Applier, refused_on_an_arm
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.commands.dispatcher import DispatchedToCycle
    from promptpotter.domain.command_kinds import (
        OriginGateDecisionPayload,
        PauseCyclePayload,
        SetSampleLookaheadPayload,
        SkipSearchpointPayload,
        SubmitArmsPayload,
    )


def read_payload_file(path: Path) -> Any:
    """A structured payload field the terminal names by a YAML file."""
    try:
        return read_yaml(path)
    except (OSError, ValueError) as exc:
        raise PayloadInvalidError(str(exc)) from exc


def pause_cycle(dispatched: DispatchedToCycle[PauseCyclePayload]) -> Applier[object]:
    return Applier.for_loop(dispatched.dispatcher.run_state(dispatched.hop).pause_refusal)


def origin_gate_decision(
    dispatched: DispatchedToCycle[OriginGateDecisionPayload],
) -> Applier[object]:
    return Applier.for_loop(dispatched.dispatcher.run_state(dispatched.hop).gate_refusal)


def submit_arms(dispatched: DispatchedToCycle[SubmitArmsPayload]) -> Applier[object]:
    return refused_on_an_arm(dispatched.campaign) or Applier.for_loop(
        dispatched.dispatcher.run_state(dispatched.hop).arms_refusal(dispatched.payload.keep)
    )


def skip_searchpoint(dispatched: DispatchedToCycle[SkipSearchpointPayload]) -> Applier[object]:
    return refused_on_an_arm(dispatched.campaign) or Applier.for_loop(
        dispatched.dispatcher.run_state(dispatched.hop).admission.skip_refusal
    )


def set_sample_lookahead(
    dispatched: DispatchedToCycle[SetSampleLookaheadPayload],
) -> Applier[object]:
    payload = dispatched.payload
    if payload.cells == 1 and not payload.auto:
        # A disarm leaves the loop nothing to take: the record removes the arming.
        return Applier.silent(lambda: None)
    # `auto` is a mode and stands across launches; one press is spent by its round, so it needs a run.
    return Applier.for_loop(
        ""
        if payload.auto
        else dispatched.dispatcher.run_state(dispatched.hop).admission.lookahead_refusal
    )
