from __future__ import annotations

import argparse

from promptpotter.application.jobs.machine_status import machine_status
from promptpotter.application.jobs.quota import quota_status
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores

__all__ = ["cmd_machine_status"]


async def cmd_machine_status(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    jobs = JobRegistry.attach()
    status = machine_status(jobs, identity=stores.identity)
    account = quota_status(stores=stores, job_registry=jobs)
    lines = [
        f"running {status.occupied} of {status.capacity} (ceiling {status.ceiling}), "
        f"{status.queued} queued"
    ]
    if status.held_back is not None:
        lines.append(status.held_back)
    if account.machine_binds is not None:
        lines.append(account.machine_binds)
    lines.append(
        f"{status.notice.title} — {status.notice.detail}" if status.notice else "a slot is free"
    )
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
        data={**status.model_dump(mode="json"), "machine_binds": account.machine_binds},
        human="\n".join(lines),
    )
