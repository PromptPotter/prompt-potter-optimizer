from __future__ import annotations

import argparse

from promptpotter.application.jobs.capacity import machine_status
from promptpotter.application.jobs.quota import quota_status
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.presentation.cli.commands.result import CommandResult
from promptpotter.presentation.cli.commands.workspace import open_stores
from promptpotter.shared.identity import acting_principal_id

__all__ = ["cmd_machine_status"]


async def cmd_machine_status(args: argparse.Namespace) -> CommandResult:
    stores = open_stores(args)
    jobs = JobRegistry.attach()
    status = machine_status(jobs, principal_id=acting_principal_id(stores.identity))
    account = quota_status(stores=stores, job_registry=jobs)
    lines = [
        f"running {status.running} of {status.capacity} (ceiling {status.ceiling}), "
        f"{status.queued} queued"
    ]
    if status.held_back is not None:
        lines.append(status.held_back)
    if account.machine_binds is not None:
        lines.append(account.machine_binds)
    lines.append(
        f"{status.notice.title} — {status.notice.detail}" if status.notice else "a slot is free"
    )
    if status.holder is not None:
        holder = status.holder
        lines.append(f"oldest run: {holder.campaign_id}/{holder.cycle_id} ({holder.user})")
    lines += [
        f"queued #{entry.position}: {entry.job_id}  {entry.dataset_name}  since {entry.created_at}"
        for entry in status.queue
    ]
    lines += [
        f"refused {refusal.released_at}: {refusal.job_id}  {refusal.dataset_name}  {refusal.reason}"
        for refusal in status.refused
    ]
    return CommandResult(
        data={**status.model_dump(mode="json"), "machine_binds": account.machine_binds},
        human="\n".join(lines),
    )
