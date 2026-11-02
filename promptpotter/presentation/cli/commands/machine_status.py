from __future__ import annotations

import argparse
import asyncio

from promptpotter.application import health
from promptpotter.application.jobs.capacity import SLOT_BOUND_WORDS
from promptpotter.application.jobs.machine_status import machine_status
from promptpotter.application.jobs.quota import quota_status
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.presentation.admin_bot import check_health_and_tell
from promptpotter.presentation.cli.commands.result import CommandResult, said_text
from promptpotter.presentation.cli.commands.workspace import open_stores


async def cmd_machine_status(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    jobs = JobRegistry.attach(stores.jobs_dir)
    status = machine_status(jobs, identity=stores.identity)
    account = quota_status(stores=stores, job_registry=jobs)
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
