from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from promptpotter.application import health
from promptpotter.application.jobs.capacity import SLOT_BOUND_WORDS
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.read_params import QuotaStatusParams
from promptpotter.presentation.admin_bot import check_health_and_tell
from promptpotter.presentation.cli.commands.result import CommandResult, said_text
from promptpotter.presentation.cli.commands.workspace import open_stores

if TYPE_CHECKING:
    import argparse

    from promptpotter.application.caller import Caller
    from promptpotter.application.jobs.machine_status import MachineStatusResponse
    from promptpotter.application.read_params import MachineStatusParams


def machine_status_human(
    _params: MachineStatusParams,
    status: MachineStatusResponse,
    _args: argparse.Namespace,
    caller: Caller,
) -> CommandResult:
    # The caller's own bound beside the machine's: the `quota-status` row, read on the same caller.
    account = QuotaStatusParams().answer(caller)
    lines = [
        f"running {status.occupied} of {status.capacity} (ceiling {status.ceiling}), "
        f"{status.queued} queued"
    ]
    bounds = [(status.bound, status.model_dump()), (account.bound, account.model_dump())]
    lines += [SLOT_BOUND_WORDS[bound].format(**said) for bound, said in bounds if bound]
    notice = status.notice
    lines.append("a slot is free" if notice is None else said_text(notice))
    lines += [
        f"running: {slot.what} ({slot.user}) since {slot.started_at or 'starting'}"
        for slot in status.running
    ]
    lines += [
        f"queued #{entry.position}: {entry.job_id}  {entry.what}  since {entry.created_at}"
        for entry in status.queue
    ]
    lines += [
        f"refused {refusal.released_at}: {refusal.job_id}  {refusal.what}  {refusal.reason}"
        for refusal in status.refused
    ]
    return CommandResult(
        data={**status.model_dump(mode="json"), "account_bound": account.bound},
        human="\n".join(lines),
    )


async def cmd_close_admission(args: argparse.Namespace) -> CommandResult:
    held = JobRegistry.attach(open_stores(args).jobs_dir).close_for_landing(args.target)
    return CommandResult(
        data={"target": args.target, "held": [job.job_id for job in held]},
        # The first word is the verdict `deploy-linux/update.sh` reads.
        human="\n".join(
            [f"held {job.what} ({job.user_id}) on {job.code_revision[:12]}" for job in held]
            or [f"closed to all but {args.target}"]
        ),
    )


async def cmd_health(_args: argparse.Namespace) -> CommandResult:
    checked = await asyncio.to_thread(check_health_and_tell)
    return CommandResult(
        data={one.probe.name: one.reading._asdict() for one in checked},
        # The state leads each line: `deploy-linux/update.sh` reads it.
        human="\n".join(f"{one.reading.state} {health.row(one)}" for one in checked),
        exit_code=int(any(one.reading.state == "broken" for one in checked)),
    )
