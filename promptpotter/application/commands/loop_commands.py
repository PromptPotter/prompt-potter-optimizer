"""Refused where no running loop will take it: accepted then, the next launch would drop it with nothing done."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.application.commands.dispatcher import Applier, DispatchedToCycle
from promptpotter.domain.command_kinds import (
    PauseCyclePayload,
    SetSampleLookaheadPayload,
    SkipSearchpointPayload,
    SubmitArmsPayload,
)
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.shared.errors import PayloadInvalidError


def read_payload_file(path: Path) -> Any:
    """A structured payload field the terminal names by a YAML file."""
    try:
        return read_yaml(path)
    except (OSError, ValueError) as exc:
        raise PayloadInvalidError(str(exc)) from exc


async def pause_cycle(dispatched: DispatchedToCycle[PauseCyclePayload]) -> Applier[object]:
    return Applier.for_loop(
        dispatched, dispatched.dispatcher.stores.run_state(dispatched.hop).pause_refusal
    )


async def submit_arms(dispatched: DispatchedToCycle[SubmitArmsPayload]) -> Applier[object]:
    state = dispatched.dispatcher.stores.run_state(dispatched.hop)
    return Applier.for_loop(dispatched, state.arms_refusal(dispatched.payload.keep))


async def skip_searchpoint(
    dispatched: DispatchedToCycle[SkipSearchpointPayload],
) -> Applier[object]:
    return Applier.for_loop(
        dispatched, dispatched.dispatcher.stores.run_state(dispatched.hop).admission.skip_refusal
    )


async def set_sample_lookahead(
    dispatched: DispatchedToCycle[SetSampleLookaheadPayload],
) -> Applier[object]:
    payload = dispatched.payload
    if payload.cells == 1 and not payload.auto:
        # A disarm leaves the loop nothing to take: the record removes the arming.
        async def _disarm() -> None:
            return None

        return Applier.silent(_disarm)
    # `auto` is a mode and stands across launches; one press is spent by its round, so it needs a run.
    return Applier.for_loop(
        dispatched,
        ""
        if payload.auto
        else dispatched.dispatcher.stores.run_state(dispatched.hop).admission.lookahead_refusal,
    )
