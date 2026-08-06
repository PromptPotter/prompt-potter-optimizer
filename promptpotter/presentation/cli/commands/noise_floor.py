"""Re-score a cached origin *k* times to read the backend's run-to-run noise. A fenced debug diagnostic — no config field, no
L1 injection, no ledger event; the loop never learns this verb exists."""

from __future__ import annotations

import argparse
import logging

from promptpotter.application.noise_floor import NoiseFloorError, measure_noise_floor
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    identity_from_args,
    resolve_campaign,
    resolve_cycle,
)

logger = logging.getLogger("promptpotter.presentation.cli")


async def cmd_noise_floor(args: argparse.Namespace) -> CommandResult:
    """Re-score the active/named cycle's cached C0 origin ``--k`` times (force_fresh)
    and report the spread as a workspace diagnostic-run record."""
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if get_verbose() else "cli")
    identity = identity_from_args(args)
    stores = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)
    campaign_id = resolve_campaign(stores, args.campaign)
    cycle_id = resolve_cycle(stores, campaign_id, args.cycle)

    try:
        outcome = await measure_noise_floor(
            stores=stores,
            identity=identity,
            hop=CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
            k=args.k,
            log=logger.info if get_verbose() else None,
        )
    except NoiseFloorError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    record = outcome.record
    assert record.noise_floor_mean is not None
    assert record.noise_floor_ci_lo is not None
    assert record.noise_floor_ci_hi is not None
    raw = ", ".join(f"{v:.4f}" for v in (record.noise_floor_raw or []))
    human = (
        f"noise-floor {campaign_id}/{cycle_id} C0: k={record.noise_floor_k} "
        f"composite {record.noise_floor_mean:.4f} "
        f"[{record.noise_floor_ci_lo:.4f}, {record.noise_floor_ci_hi:.4f}] "
        f"on {record.workspace_n} samples (raw: {raw}). "
        f"Origin's recorded composite was {record.source_campaign_composite:.4f}. "
        f"Record → {outcome.artifact_path}"
    )
    return CommandResult(data=record.model_dump(), human=human)


__all__ = ["cmd_noise_floor"]
