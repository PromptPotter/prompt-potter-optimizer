"""Fresh-init body — minted from inside ``cmd_optimize`` when
``--config`` or ``--dataset-name`` is set.

No standalone command; ``cmd_optimize`` calls ``_run_init_body`` first
in fresh mode, then proceeds with the optimize loop.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store.base import read_text_optional
from promptpotter.presentation.cli.commands._shared import (
    _mint_session_and_cycle,
    _prepare_cycle,
    init_services_cli,
)
from promptpotter.presentation.cli.session import load_campaign_config

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig

logger = logging.getLogger("promptpotter.presentation.cli")


async def _maybe_decompose_task(
    session: Session,
    campaign_config: CampaignConfig,
    session_id: str,
    *,
    dataset_name: str,
    task_file: str | None,
    task_text: str | None,
) -> None:
    """Decompose task description once at session-creation time.

    ``datasets/{name}/task_description.md`` is the canonical source;
    ``--task-file`` and ``--task-text`` override for ad-hoc cases. Result
    is disk-cached, so re-init against the same dataset is free.
    """
    from promptpotter.application.config import create_llm_client
    from promptpotter.application.optimization.task_context import decompose_task_context

    if task_file:
        task_description = Path(task_file).read_text(encoding="utf-8")
    elif task_text:
        task_description = task_text
    else:
        task_description = read_text_optional(
            Path("datasets") / dataset_name / "task_description.md"
        )

    if not task_description:
        return

    llm_client, model = create_llm_client(campaign_config)
    task_context, _consultation, was_cached = await decompose_task_context(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )
    logger.info(
        "Task context decomposed%s: %d fields",
        " (cached)" if was_cached else "",
        len(task_context),
    )
    state = session.store.sessions.read(session_id) or {}
    state["task_context"] = task_context.to_dict()
    session.store.sessions.update(session_id, state)


async def _run_init_body(args: argparse.Namespace) -> dict[str, Any]:
    """Mint a fresh session+cycle: services, datasets, pipeline, session record.

    Called from the top of :func:`cmd_optimize` when ``--config`` or
    ``--dataset-name`` is set. Returns a small dict describing the minted
    cycle so the optimize body (which reloads via ``load_session``) can
    log + sanity-check.

    No scoring calls here — the origin runs as phase 0 of optimize on the
    ``sp_budget_ttest`` slice. The one LLM cost is the ``restructure``
    template call via :func:`_maybe_decompose_task`, content-hash cached
    at ``restructure_cache.json`` so subsequent fresh-mode runs are free.
    """
    from promptpotter.application.config import load_campaign_config as _load_cfg
    from promptpotter.application.origin import prepare_datasets

    file_config = load_campaign_config(args.config)
    dataset_name = args.dataset_name or file_config.get("dataset_name")
    if not dataset_name:
        from promptpotter.presentation.cli.session import no_dataset_hint

        raise SystemExit(
            "ERROR: fresh-init mode requires a dataset name. Pass "
            "--dataset-name <name> or a --config that names one.\n\n" + no_dataset_hint()
        )

    # Auto-load the dataset's campaign.json when --config wasn't given. Without this,
    # the session persists with scoring=null and default optimization knobs — the
    # dataset's own file is the intended source of truth.
    if not args.config:
        default_config_path = Path("datasets") / dataset_name / "campaign.json"
        if default_config_path.exists():
            file_config = load_campaign_config(str(default_config_path))

    session = await init_services_cli(
        backend_url=args.backend_url,
        backend_id=args.backend_id,
        experiment_id=args.experiment_id,
        dataset_name=dataset_name,
        take_over=True,
        tenant_id=getattr(args, "tenant", "default"),
    )
    backend_id = session.backend_id

    profile = session.store.backends.load_connector_profile(backend_id) or {}
    campaign_config = _load_cfg({**profile, **file_config})

    excluded = list(campaign_config.exclude_nodes)

    if args.excel_path:
        train_data = prepare_datasets(session.store, args.excel_path).train_data or []
    else:
        train_data = session.samples or []

    pipeline_params, origin, cycle_id = _prepare_cycle(session, campaign_config, train_data)
    active = list(pipeline_params.get("steps", [])) if pipeline_params else []
    init_params = {
        "backend_url": args.backend_url,
        "backend_id": backend_id,
        "experiment_id": args.experiment_id,
        "dataset_name": dataset_name,
    }
    session_id = _mint_session_and_cycle(
        session,
        campaign_config,
        cycle_id=cycle_id,
        init_params=init_params,
        pipeline_params=pipeline_params,
        origin=origin,
        dataset_count=len(train_data),
    )

    await _maybe_decompose_task(
        session,
        campaign_config,
        session_id,
        dataset_name=dataset_name,
        task_file=args.task_file,
        task_text=args.task_text,
    )

    logger.info(
        "Fresh cycle minted: session=%s cycle=%s dataset=%s (%d queries)",
        session_id,
        cycle_id,
        dataset_name,
        len(train_data),
    )

    return {
        "session_id": session_id,
        "cycle_id": cycle_id,
        "backend_id": backend_id,
        "dataset_count": len(train_data),
        "active_steps": active,
        "excluded_nodes": excluded,
    }
