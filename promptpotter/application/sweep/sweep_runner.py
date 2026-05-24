"""Sweep-batch orchestration — one fork per ``OperatorSweepFile`` via ``_mint_fork``."""

from __future__ import annotations

import argparse
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.resume_and_fork import _mint_fork
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    cleanup_stub_fork_if_empty,
)
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import PayloadOutcome, SweepBatchResult
from promptpotter.domain.run_records import ForkPayload, ForkTrigger, OperatorSweepFile
from promptpotter.infrastructure.store import (
    build_stores,
    root_cycle_id,
    save_active_pointer,
)
from promptpotter.presentation.views.render import render_sweep_summary
from promptpotter.presentation.views.view_models import (
    SweepPayloadRow,
    SweepSummaryView,
)

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger(__name__)


def resolve_sweep_dir(dataset_name: str | None) -> Path | None:
    """Path to ``datasets/{name}/sweep/`` if it exists, else None."""
    if not dataset_name:
        return None
    sweep_dir = Path("datasets") / dataset_name / "sweep"
    return sweep_dir if sweep_dir.is_dir() else None


def load_sweep_payloads(sweep_dir: Path) -> list[tuple[Path, OperatorSweepFile]]:
    """Parse every ``*.json`` under ``sweep_dir``; ``extra='forbid'`` halts on typos."""
    files = sorted(sweep_dir.glob("*.json"))
    return [
        (p, OperatorSweepFile.model_validate_json(p.read_text(encoding="utf-8"))) for p in files
    ]


def existing_fork_source_files(
    store: Any, campaign_id: str, parent_cycle_id: str
) -> dict[str, str]:
    """``{source_file: fork_cycle_id}`` for prior FORK_CUTs under this parent.

    Dropped fork dirs are ignored so a deleted-fork operator isn't blocked from re-running.
    """
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord
    from promptpotter.infrastructure.ledger import CycleEventLog

    out: dict[str, str] = {}
    parent_dir = store.campaigns.cycle_dir(campaign_id, parent_cycle_id)
    if not (parent_dir / ".runtime" / "ledger.jsonl").exists():
        return out
    ledger = CycleEventLog.open(CycleDir(parent_dir))
    for record in ledger.iter():
        if (
            isinstance(record, ResumeCheckpointRecord)
            and record.kind == ResumeCheckpointKind.FORK_CUT
        ):
            src = record.data.get("source_file")
            outcome = record.outcome
            if src and outcome and store.campaigns.cycle_dir(campaign_id, str(outcome)).exists():
                out[str(src)] = str(outcome)
    return out


async def run_sweep_batch(
    args: argparse.Namespace,
    root_ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    sweep_payloads: list[tuple[Path, OperatorSweepFile]],
    *,
    observer_factory: Callable[..., Any],
    verbose: bool,
) -> SweepBatchResult:
    """Mint one fork per ``OperatorSweepFile`` off ``root_ctx.cycle_id``; restore active pointer on exit."""
    from promptpotter.application.bootstrap import init_services
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.runner import (
        run_optimization as _orch_run_optimization,
    )
    from promptpotter.presentation.cli.session import load_session

    tenant_id = getattr(args, "tenant", "default")
    store = build_stores(tenant_id=tenant_id)
    parent_cycle_id = root_ctx.cycle_id
    campaign_id = root_ctx.campaign_id
    existing = existing_fork_source_files(store, campaign_id, parent_cycle_id)

    started_at = datetime.now(UTC).isoformat()
    # No underscores in batch_id — the cycle-id regex requires it.
    batch_id = datetime.now(UTC).strftime("b%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)
    family_root = root_cycle_id(parent_cycle_id)

    initial_payloads = [
        {
            "source_file": p.name,
            "status": "skipped_already_forked" if p.name in existing else "pending",
            "cycle_id": existing.get(p.name, ""),
        }
        for p, _ in sweep_payloads
    ]
    store.sweeps.init_batch(
        campaign_id,
        batch_id,
        parent_cycle_id=parent_cycle_id,
        started_at=started_at,
        payloads=initial_payloads,
    )

    new_cycle_ids: list[str] = []
    status_by_source: dict[str, str] = {}
    interrupted = False
    for path, payload in sweep_payloads:
        if path.name in existing:
            logger.info(
                "Sweep payload %s already forked → %s; skipping (run `resume` "
                "against that fork to continue it)",
                path.name,
                existing[path.name],
            )
            continue
        fork_payload = ForkPayload(
            trigger=ForkTrigger.OPERATOR_SWEEP,
            reason=payload.reason,
            issued_by=tenant_id,
            l1_layout=payload.l1_layout,
        )
        # try/finally below guards against a stub dir if the operator interrupts
        # before _orch_run_optimization commits round 1.
        new_cycle_id = _mint_fork(
            store.campaigns,
            campaign_id,
            tenant_id,
            root_ctx.session_id,
            parent_cycle_id,
            0,
            fork_payload,
            sweep_batch_id=batch_id,
            sweep_source_file=path.name,
        )
        fork_result = None
        try:
            # Active pointer already retargeted; reload + bind a fresh session.
            fork_ctx = load_session(args)
            fork_session = await init_services(
                **fork_ctx.init_params,
                tenant_id=tenant_id,
                on_status=(lambda msg: logger.info(msg) if verbose else None),
            )
            fork_session.session_id = fork_ctx.session_id
            fork_session.campaign_id = fork_ctx.campaign_id
            fork_session.state.cycle_id = fork_ctx.cycle_id
            # init_services leaves session.pipeline_params empty until this fires.
            configure_and_apply_pipeline(
                fork_session,
                campaign_config,
                log=logger.info if verbose else (lambda *_a, **_k: None),
            )
            logger.info(
                "Sweep fork session bound: session_id=%s cycle_id=%s pp.llm_only.model=%s",
                fork_session.session_id,
                fork_session.state.cycle_id,
                (fork_session.pipeline_params or {}).get("llm_only", {}).get("model"),
            )

            observers = observer_factory(fork_session, 0.0)
            fork_result = await _orch_run_optimization(
                train_data,
                campaign_config,
                session=fork_session,
                observers=observers,
                experiment_id=fork_ctx.state["experiment_id"],
                task_context=fork_ctx.task_context,
                sweep=True,
                fork_payload=fork_payload,
            )
        finally:
            # Drop the fork dir if the run never produced a round.
            n_rounds = fork_result.n_rounds if fork_result is not None else 0
            if n_rounds == 0:
                cleanup_stub_fork_if_empty(
                    campaign_store=store.campaigns,
                    campaign_id=campaign_id,
                    tenant_id=tenant_id,
                    session_id=root_ctx.session_id,
                    cycle_id=new_cycle_id,
                    parent_cycle_id=parent_cycle_id,
                )

        if fork_result is None:
            status_by_source[path.name] = "crashed"
            logger.warning(
                "Sweep fork init crashed for payload=%s; stub cleaned, halting batch",
                path.name,
            )
            interrupted = True
            break

        if fork_result.n_rounds == 0:
            stopped_by_operator = fork_result.stop_reason == StopReason.INTERRUPTED
            status_by_source[path.name] = "interrupted" if stopped_by_operator else "cleaned"
            logger.info(
                "Sweep fork %s never ran a round (payload=%s, stop_reason=%s); cleaned up",
                new_cycle_id,
                path.name,
                fork_result.stop_reason,
            )
            if stopped_by_operator:
                interrupted = True
                break
            continue

        new_cycle_ids.append(new_cycle_id)
        if fork_result.stop_reason == StopReason.INTERRUPTED:
            # _run_round_loop catches Ctrl+C and returns INTERRUPTED — halt the batch.
            status_by_source[path.name] = "interrupted"
            logger.warning(
                "Sweep fork %d/%d interrupted: %s (payload=%s) — halting "
                "batch. Resume the partial fork with `resume` against "
                "that cycle, or re-run `new --sweep-batch` to pick up "
                "remaining payloads.",
                len(new_cycle_ids),
                len(sweep_payloads),
                new_cycle_id,
                path.name,
            )
            interrupted = True
            break
        status_by_source[path.name] = "completed"
        logger.info(
            "Sweep fork %d/%d complete: %s (payload=%s)",
            len(new_cycle_ids),
            len(sweep_payloads),
            new_cycle_id,
            path.name,
        )

    save_active_pointer(tenant_id, root_ctx.session_id, campaign_id, parent_cycle_id)

    completed_at = datetime.now(UTC).isoformat()
    cycle_by_source = {
        p.name: cid for (p, _), cid in zip(sweep_payloads, new_cycle_ids, strict=False)
    }
    store.sweeps.finalize_batch(
        campaign_id,
        batch_id,
        completed_at=completed_at,
        status_by_source=status_by_source,
        cycle_by_source=cycle_by_source,
    )

    final_index = store.sweeps.load_batch(campaign_id, batch_id) or {}
    payload_rows = tuple(
        SweepPayloadRow(
            source_file=entry["source_file"],
            status=entry["status"],
            cycle_id=entry["cycle_id"],
        )
        for entry in final_index.get("payloads", [])
    )
    summary_view = SweepSummaryView(
        batch_id=batch_id,
        parent_cycle_id=parent_cycle_id,
        family_root=family_root,
        started_at=started_at,
        completed_at=completed_at,
        n_minted=len(new_cycle_ids),
        n_payloads=len(sweep_payloads),
        payloads=payload_rows,
    )
    store.sweeps.write_summary_md(campaign_id, batch_id, render_sweep_summary(summary_view))

    logger.info(
        "Sweep batch %s complete: %d forks under %s; active pointer restored",
        batch_id,
        len(new_cycle_ids),
        parent_cycle_id,
    )
    return SweepBatchResult(
        batch_id=batch_id,
        parent_cycle_id=parent_cycle_id,
        family_root=family_root,
        started_at=started_at,
        completed_at=completed_at,
        fork_cycle_ids=new_cycle_ids,
        payload_outcomes=[
            PayloadOutcome(
                source_file=row.source_file,
                status=row.status,
                cycle_id=row.cycle_id,
            )
            for row in payload_rows
        ],
        interrupted=interrupted,
    )


__all__ = [
    "existing_fork_source_files",
    "load_sweep_payloads",
    "resolve_sweep_dir",
    "run_sweep_batch",
]
