"""Sweep-batch orchestration — one fork per ``OperatorSweepFile``, widened to a ``ForkSpec`` before the unified
``_mint_fork``. Reached from ``new --sweep-batch``; the second hand-rolled harness for this job is gone."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    _mint_fork,
    cleanup_stub_fork_if_empty,
)
from promptpotter.application.views.render import render_sweep_summary
from promptpotter.application.views.view_models import SweepPayloadRow, SweepSummaryView
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.results import PayloadOutcome, SweepBatchResult
from promptpotter.domain.run_records import ForkSpec, ForkTrigger, OperatorSweepFile
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.infrastructure.store.layout import root_cycle_id
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger(__name__)


def resolve_sweep_dir(dataset_dir: Path | None) -> Path | None:
    """``{dataset_dir}/sweep/`` if it exists. *dataset_dir* is the RESOLVED config dir (tenant-first), so a tenant dataset's
    sweep is honored alongside a repo benchmark's."""
    if dataset_dir is None:
        return None
    sweep_dir = dataset_dir / "sweep"
    return sweep_dir if sweep_dir.is_dir() else None


def load_sweep_payloads(sweep_dir: Path) -> list[tuple[Path, OperatorSweepFile]]:
    """Parse every ``*.yaml`` under ``sweep_dir``; ``extra='forbid'`` halts on typos."""
    files = sorted(sweep_dir.glob("*.yaml"))
    return [(p, OperatorSweepFile.model_validate(read_yaml(p))) for p in files]


def existing_fork_source_files(
    stores: Stores, campaign_id: str, parent_cycle_id: str
) -> dict[str, str]:
    """Prior FORK_CUT source files under this parent. A dropped fork dir is ignored, so a deleted-fork operator is not
    blocked from re-running it."""
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.layout import CycleLayout

    out: dict[str, str] = {}
    parent_dir = stores.campaigns.cycle_dir(
        CycleHop(campaign_id=campaign_id, cycle_id=parent_cycle_id)
    )
    if not CycleLayout(parent_dir).ledger.exists():
        return out
    ledger = CycleEventLog.open(CycleDir(parent_dir))
    for record in ledger.iter():
        if (
            isinstance(record, ResumeCheckpointRecord)
            and record.kind == ResumeCheckpointKind.FORK_CUT
        ):
            src = record.data.get("source_file")
            outcome = record.outcome
            outcome_hop = CycleHop(campaign_id=campaign_id, cycle_id=str(outcome))
            if src and outcome and stores.campaigns.cycle_dir(outcome_hop).exists():
                out[str(src)] = str(outcome)
    return out


async def run_sweep_batch(
    reload_ctx: Callable[[], SessionCtx],
    root_ctx: SessionCtx,
    campaign_config: CampaignConfig,
    train_data: list[Sample],
    sweep_payloads: list[tuple[Path, OperatorSweepFile]],
    *,
    observer_factory: Callable[..., Any],
    verbose: bool,
) -> SweepBatchResult:
    """Mint one fork per payload; restore the active pointer on exit. ``reload_ctx`` is INJECTED, not called: the pointer moves
    once per payload, so a single pre-resolved session would bind every fork to the first."""
    from promptpotter.application.initialization.wiring import init_services
    from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
    from promptpotter.application.runner.entry import RunMode
    from promptpotter.application.runner.entry import run_optimization as _orch_run_optimization

    # The parent context already resolved both — re-resolving them here was a third copy
    # of the `--tenant > registered developer > anonymous default` ladder, free to disagree
    # with the very session this batch forks off. `Stores.identity` is the sole source of
    # tenant scope; the store is the same object `load_session` built.
    store = root_ctx.store
    identity = store.identity
    tenant_id = identity.tenant_id
    parent_cycle_id = root_ctx.cycle_id
    campaign_id = root_ctx.campaign_id
    existing = existing_fork_source_files(store, campaign_id, parent_cycle_id)

    started_at = utcnow_iso()
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

    def _finalize_batch() -> SweepBatchResult:
        """Restore the pointer, close the batch index, write the summary — from a ``finally``, so an interrupt in any fork still
        leaves a readable batch instead of every payload stuck ``pending``."""
        completed_at = utcnow_iso()
        save_active_pointer(
            store.base_dir,
            root_ctx.session_id,
            CycleHop(campaign_id=campaign_id, cycle_id=parent_cycle_id),
        )
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
        store.sweeps.write_summary_md(
            campaign_id,
            batch_id,
            render_sweep_summary(
                SweepSummaryView(
                    batch_id=batch_id,
                    parent_cycle_id=parent_cycle_id,
                    family_root=family_root,
                    started_at=started_at,
                    completed_at=completed_at,
                    n_minted=len(new_cycle_ids),
                    n_payloads=len(sweep_payloads),
                    payloads=payload_rows,
                )
            ),
        )
        logger.info(
            "Sweep batch %s closed: %d forks under %s; active pointer restored",
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

    try:
        for path, payload in sweep_payloads:
            if path.name in existing:
                logger.info(
                    "Sweep payload %s already forked → %s; skipping (run `resume` "
                    "against that fork to continue it)",
                    path.name,
                    existing[path.name],
                )
                continue
            fork_payload = ForkSpec(
                trigger=ForkTrigger.OPERATOR_SWEEP,
                reason=payload.reason,
                issued_by=tenant_id,
                l1_layout=payload.l1_layout,
            )
            # try/finally below guards against a stub dir if the operator interrupts
            # before _orch_run_optimization commits round 1.
            new_cycle_id = _mint_fork(
                store.campaigns,
                CycleHop(campaign_id=campaign_id, cycle_id=parent_cycle_id),
                root_ctx.session_id,
                0,
                fork_payload,
                sweep_batch_id=batch_id,
                sweep_source_file=path.name,
            )
            fork_result = None
            try:
                # Active pointer already retargeted; reload + bind a fresh session.
                fork_ctx = reload_ctx()
                fork_session = await init_services(
                    **fork_ctx.init_params,
                    identity=identity,
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
                    task_context=fork_ctx.task_context,
                    mode=RunMode(sweep=True),
                    fork_payload=fork_payload,
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                # An unwind skips the status bookkeeping below; record it here or this payload
                # stays `pending`. The fork itself finalized as PAUSED and is resumable.
                status_by_source[path.name] = StopOutcome.PAUSED.value
                interrupted = True
                raise
            finally:
                # Drop the fork dir if the run never produced a round.
                n_l1_rounds = fork_result.n_l1_rounds if fork_result is not None else 0
                if n_l1_rounds == 0:
                    cleanup_stub_fork_if_empty(
                        campaign_store=store.campaigns,
                        hop=CycleHop(campaign_id=campaign_id, cycle_id=new_cycle_id),
                        parent_cycle_id=parent_cycle_id,
                    )

            # Per-payload status = the fork's StopOutcome (`stop_reason_outcome`, the one
            # StopReason classification) — never a sweep-private vocabulary re-encoding
            # the same fact the fork's finalize already wrote to its index.json.
            if fork_result is None:
                status_by_source[path.name] = StopOutcome.FAILED.value
                logger.warning(
                    "Sweep fork init crashed for payload=%s; stub cleaned, halting batch",
                    path.name,
                )
                interrupted = True
                break

            outcome = stop_reason_outcome(fork_result.stop_reason)
            status_by_source[path.name] = outcome.value

            if fork_result.n_l1_rounds == 0:
                stopped_by_operator = outcome is StopOutcome.PAUSED
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
            if outcome is StopOutcome.PAUSED:
                # The pause flag halts the fork and returns PAUSED — halt the batch too.
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
            logger.info(
                "Sweep fork %d/%d complete: %s (payload=%s)",
                len(new_cycle_ids),
                len(sweep_payloads),
                new_cycle_id,
                path.name,
            )
    finally:
        batch_result = _finalize_batch()

    return batch_result


__all__ = [
    "load_sweep_payloads",
    "resolve_sweep_dir",
    "run_sweep_batch",
]
