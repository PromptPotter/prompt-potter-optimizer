"""``set-task`` subcommand — decompose task description into domain context."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from promptpotter.presentation.cli.bootstrap import init_services_cli
from promptpotter.presentation.cli.result import CommandResult
from promptpotter.presentation.cli.session import load_session

logger = logging.getLogger("promptpotter.presentation.cli")


async def cmd_task_context(args: argparse.Namespace) -> CommandResult:
    """Decompose task description into structured domain context."""
    from promptpotter.application.campaign.config import create_llm_client
    from promptpotter.application.optimization.pipeline import (
        decompose_task_context as _svc_decompose,
    )

    ctx = load_session(args)

    if args.task_file:
        task_description = Path(args.task_file).read_text(encoding="utf-8")
    elif args.task_text:
        task_description = args.task_text
    else:
        sys.exit("ERROR: Provide --task-file or --task-text")

    session = await init_services_cli(**ctx.init_params)

    llm_client, model = create_llm_client(ctx.campaign_config)
    result = await _svc_decompose(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )
    cache_tag = " (cached)" if result.was_cached else ""
    logger.info("Task context decomposed%s: %d fields", cache_tag, len(result.task_context))

    ctx.state["task_context"] = result.task_context.to_dict()
    ctx.save_phase("task-context")
    return CommandResult(
        data={"task_context": result.task_context.to_dict(), "cached": result.was_cached}
    )
