"""Cross-command helpers + shared module state for the CLI commands. The mint prologue
(pipeline → origin → cycle_id → mint) is application work at ``application/jobs/mint.py``."""

from __future__ import annotations

import contextlib
import functools
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter import connectors
from promptpotter.application.bench.resume_and_fork.decisions import (
    GatingMode,
    resume_checkpoint_gating,
)
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_and_hold,
    refuse_as_busy,
    request_launch,
)
from promptpotter.application.jobs.launcher.run_job import run_held_job
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
)
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.launch_limits import HeldLimits, LaunchLimits
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store.dataset_access import backend_type_of_dataset
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.presentation.terminal.completion import render_completion
from promptpotter.shared.identity import IdentityContext

if TYPE_CHECKING:
    import argparse

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.registry import Job, JobRegistry
    from promptpotter.application.run_observers import RunObservers
    from promptpotter.application.runner.entry import RunMode
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.presentation.cli.session import SessionCtx

logger = logging.getLogger("promptpotter.presentation.cli")


@dataclass
class CommandResult:
    """``data`` is machine-readable; ``human`` is pre-rendered text. ``main()`` picks one, and
    exits non-zero on a ``FAILED`` ``outcome`` — set only by a verb that ran a cycle."""

    data: dict[str, Any] | None = None
    human: str | None = None
    outcome: StopOutcome | None = None


_VERBOSE = False


def set_verbose(value: bool) -> None:
    global _VERBOSE
    _VERBOSE = value


def get_verbose() -> bool:
    return _VERBOSE


def pipeline_summary(session: Session, pipeline_params: dict[str, Any] | None) -> str:
    """Pipeline name + active-node summary — the check-in line and the startup log share it."""
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}" if ps else "pipeline unavailable"
    active = list((pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    return f"{pipe} · {nodes}"


def log_startup_summary(
    session: Session,
    pipeline_params: dict[str, Any] | None,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info(
        "%s · backend %s · dataset %s", pipeline_summary(session, pipeline_params), backend_url, ds
    )


async def init_services_cli(
    dataset_name: str,
    *,
    identity: IdentityContext,
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
) -> Session:
    """*identity* is REQUIRED: a :func:`default_identity` fallback here disagrees with the CLI's own
    rule, and writes a terminal run into the anonymous workspace instead of the operator's."""

    setup_logging(style="full" if _VERBOSE else "cli")
    return await init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        dataset_name=dataset_name,
        on_status=lambda msg: logger.info(msg) if _VERBOSE else None,
        identity=identity,
    )


def identity_from_args(args: argparse.Namespace) -> IdentityContext:
    """Build the Stage-0 :class:`IdentityContext` from CLI flags — ``--tenant``, else the registered
    operator, else anonymous. This is the seam where a flag becomes a :class:`TenantId`."""

    return registered_or_default_identity(getattr(args, "tenant", None))


def launch_limits_from_args(args: argparse.Namespace) -> LaunchLimits:
    """``--halt-at`` / ``--spend-budget`` / ``--token-budget`` as the one model the route validates
    too — argparse types these and bounds none of them."""
    return LaunchLimits(
        halt_at_accuracy=getattr(args, "halt_at_accuracy", None),
        spend_budget_usd=getattr(args, "spend_budget_usd", None),
        token_budget=getattr(args, "token_budget", None),
    )


def backend_reach_line(backend_type: str, backend_url: str) -> str:
    """What the check-in readout says once the preflight passed. An ``in_process`` connector was
    never contacted, so claiming a URL is reachable states a fact nothing established."""
    if connectors.get(backend_type).execution == "in_process":
        return f"in-process ({backend_type}) — no wire"
    return f"reachable at {backend_url}"


def backend_unreachable_result(exc: BackendUnreachableError) -> CommandResult:
    """The shared preflight failure every loop verb (``new`` / ``resume``) returns when the
    connector's own probe reports the backend down (``launcher.admission::probe_backend``).

    **The connector's own ``detail`` IS the message.** A cure hardcoded here is a cure for whichever
    backend happened to be the only one — harbor alone refuses for three reasons (no extra, a
    non-UTF-8 interpreter, a stopped Docker daemon) and its probe names which. An ingress renders
    what the probe said; knowing the remedy is the connector's job."""
    return CommandResult(
        data={
            "error": "backend_unreachable",
            "backend_url": exc.backend_url,
            "backend_type": exc.backend_type,
            "detail": exc.detail,
        },
        human=(
            f"Backend '{exc.backend_type}' is not ready.\n\n{exc.detail}"
            if exc.detail
            else f"Backend '{exc.backend_type}' at {exc.backend_url} is not reachable."
        ),
    )


# Holds the PATH of the newest terminal launch's readout, never a copy: parallel runs each own
# their cycle's file, and one shared copy interleaves them.
_LATEST_READOUT_POINTER = Path("logs/latest-readout-path.txt")


def _point_at_readout(session: Session) -> None:
    readout = CycleLayout(session.store.campaigns.cycle_dir(session.hop).absolute()).readout
    with contextlib.suppress(OSError):
        write_text(_LATEST_READOUT_POINTER, f"{readout}\n")


async def _hold_machine_slot(
    args: argparse.Namespace, ctx: SessionCtx, session: Session, campaign_config: CampaignConfig
) -> tuple[JobRegistry, Job, HeldLimits]:
    """Take the SAME machine slot the browser takes, joining the SAME queue when the box is full.

    A terminal run that holds nothing makes every statement the machine makes about itself false
    while it runs — how full it is, who holds it, whether another launch fits — and lets the
    browser start a second producer on the cycle the terminal is already running. A scheduler that
    cannot see half its workload is not one.

    It WAITS here rather than detaching, because a person is watching this process and the run
    happens inside it. Being in the shared QUEUE is what makes the wait fair: a terminal that
    merely retried in a loop would take the next free slot ahead of a browser launch that has
    waited longer. ``--no-wait`` leaves the line and refuses instead, naming the holder."""

    # Releasing needs no ownership — a job's producer holds an OS lock for its own lifetime, so a
    # crashed server's jobs cannot wedge the box until it restarts, and a live run is never touched.
    registry = JobRegistry.attach()
    dataset_name = ctx.init_params.get("dataset_name") or ""
    job = request_launch(
        stores=session.store,
        job_registry=registry,
        dataset_name=dataset_name,
        hop=ctx.hop,
        # The launch-rate and daily-campaign arms bound a STRANGER spending the host's key, and the
        # terminal IS the host (`jobs/quota.py::_is_host`). The CONCURRENCY arm is outside them and
        # still applies.
        rate_limited=False,
    )
    if job.status == "queued":
        if getattr(args, "no_wait", False):
            refuse_as_busy(registry, job)
        position = next(
            (i for i, q in enumerate(registry.queue_order(), 1) if q.job_id == job.job_id), 1
        )
        holder = registry.holder()
        sys.stderr.write(
            f"Machine full — queued at position {position} "
            f"(oldest run: {holder.campaign_id if holder else '?'}). "
            f"It starts by itself; Ctrl+C to leave the queue.\n"
        )
        sys.stderr.flush()
    held = await admit_and_hold(
        stores=session.store,
        job_registry=registry,
        job=job,
        verb=str(getattr(args, "command", None) or "cli"),
        dataset_name=dataset_name,
        backend_type=backend_type_of_dataset(session.store, dataset_name),
        backend_url=ctx.backend_url,
        requested=launch_limits_from_args(args),
        config=lambda: campaign_config,
        hop=ctx.hop,
    )
    return registry, job, held


async def drive_cycle(
    args: argparse.Namespace,
    ctx: SessionCtx,
    campaign_config: CampaignConfig,
    session: Session,
    train_data: list[Sample],
    *,
    mode: RunMode,
) -> tuple[CycleResult, RunObservers]:
    """One pass through the optimization loop — the single CLI driver. It owns the scaffolding every
    loop verb shares; callers construct only the verb's :class:`RunMode`.

    It is also where a terminal run holds its machine slot, because a slot stands for a RUN and
    this is where one begins and ends. Later than the web path admits (which gates before its
    mint), and deliberately so: the front of a CLI verb can sit for minutes on an interactive
    check-in, and a slot held across operator typing is a slot nobody else can have."""

    registry, job, held = await _hold_machine_slot(args, ctx, session, campaign_config)
    _point_at_readout(session)
    return await run_held_job(
        registry,
        job.job_id,
        session,
        campaign_config,
        train_data,
        mode=mode,
        # For the box's operator, metered in neither unit, what admission held is the declaration
        # itself; a delegate under `--tenant` is held to their grant's ceiling, as in the browser.
        limits=held,
        readout_sink=functools.partial(print, flush=True),
    )


def cycle_result_command(
    ctx: SessionCtx, session: Session, cycle_result: CycleResult
) -> CommandResult:
    """The shared finish tail for ``new`` / ``resume`` — result payload + human summary."""
    return CommandResult(
        data=cycle_result.model_dump(),
        human=render_completion(
            cycle_result,
            pipeline_schema=session.pipeline_schema,
            dataset_name=ctx.init_params.get("dataset_name"),
            campaign_dir=session.store.campaigns.campaign_root_dir(ctx.campaign_id),
        ),
        outcome=stop_reason_outcome(cycle_result.stop_reason),
    )


@functools.cache
def divergence_hint() -> str:
    """Derive the divergence-checked kinds from ``resume_checkpoint_gating``. Walking the table means
    adding a kind updates the operator message automatically."""

    gating = resume_checkpoint_gating()
    replayed = sorted(k.value for k, m in gating.items() if m is GatingMode.REPLAYED)
    archival = sorted(k.value for k, m in gating.items() if m is GatingMode.ARCHIVAL)
    hint = (
        f"Checked decisions: {', '.join(replayed)}.\n"
        f"(Archival, not divergence-gated: {', '.join(archival)}.)\n\n"
        "Options:\n"
        "  • `python -m promptpotter new <dataset>` — start a fresh "
        "campaign (most common: you wanted a new run, not a resume).\n"
        "  • `python -m promptpotter resume --fork-on-divergence` — branch "
        "a sibling cycle here under the current scorer.\n"
        "  • Revert `campaign.json::scoring` — continue the original trajectory.\n"
        "  • `python -m promptpotter resume --no-check` — accept the divergence."
    )
    # Exhaustiveness at first build: every gated kind must surface in the operator hint.
    if not all(k.value in hint for k in gating):
        raise RuntimeError("divergence hint must name every CheckpointKind")
    return hint


def _campaign_matches(stores: Stores, needle: str) -> list[str]:
    """The store's matcher, exiting on ambiguity: picking one of several is the only outcome worse
    than not resolving at all."""
    candidates = stores.campaigns.match_campaign_ids(needle)
    if len(candidates) > 1:
        raise SystemExit(
            f"ERROR: {needle!r} matches {len(candidates)} campaigns: "
            f"{', '.join(candidates[:5])}{'…' if len(candidates) > 5 else ''}. "
            "Pass the full id."
        )
    return candidates


def resolve_campaign(stores: Stores, needle: str) -> str:
    """*needle* → a campaign id, exiting when nothing matches. For a verb that has no other answer."""
    matches = _campaign_matches(stores, needle)
    if not matches:
        raise SystemExit(f"ERROR: no campaign matches {needle!r}.")
    return matches[0]


def resolve_campaign_hint(stores: Stores, needle: str) -> str:
    """*needle* resolved where one campaign matches, returned UNCHANGED where none does.

    For the verbs whose dispatcher already answers ``not_found`` for an absent id: they keep
    answering it, and gain the prefix / 6-hex-suffix reach ``verify`` and ``noise-floor`` have.
    Without it one verb resolves a needle the verb beside it reports missing."""
    return next(iter(_campaign_matches(stores, needle)), needle)


def resolve_cycle(stores: Stores, campaign_id: str, hint: str | None) -> str:
    """*hint* → a cycle id of *campaign_id* through the store's matcher, the rule a campaign needle
    resolves by; ``hint=None`` names every cycle, so it resolves only where the campaign has one."""
    matches = stores.campaigns.match_cycle_ids(campaign_id, hint or "")
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(
            f"ERROR: no cycle in {campaign_id!r} matches {hint!r}."
            if hint
            else f"ERROR: campaign {campaign_id!r} has no cycles on disk."
        )
    listed = f"{', '.join(matches[:5])}{'…' if len(matches) > 5 else ''}"
    raise SystemExit(
        f"ERROR: {hint!r} matches {len(matches)} cycles in {campaign_id!r}: {listed}."
        if hint
        else f"ERROR: campaign {campaign_id!r} has {len(matches)} cycles; pass --cycle <prefix>. "
        f"Available: {listed}."
    )


def resolve_target(args: argparse.Namespace, store: Stores) -> tuple[str, str]:
    """The ``--campaign``/``--cycle`` pair, else the active pointer's — never half of each: the
    pointer's cycle belongs to the pointer's campaign, so a named one resolves its own."""
    campaign_id: str = getattr(args, "campaign", None) or ""
    cycle_id: str = getattr(args, "cycle", None) or ""
    if not campaign_id:
        _sid, pointer_cid, pointer_cyid = read_active_pointer(store.base_dir)
        return pointer_cid, cycle_id or pointer_cyid
    # A hand-typed `--campaign` gets the same reach here as it does for `verify`, through the one
    # matcher rather than a second rule.
    campaign_id = resolve_campaign_hint(store, campaign_id)
    return campaign_id, cycle_id or resolve_cycle(store, campaign_id, None)


def confirm_tty(prompt: str, *, default_no: bool = True) -> bool | None:
    """Ask y/N at the terminal; ``None`` when stdin is not a TTY, so callers fall back to a
    non-interactive default. One shared site keeps that detection consistent across the CLI."""
    if not sys.stdin.isatty():
        return None
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        raw = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not raw:
        return not default_no
    return raw in {"y", "yes"}


__all__ = [
    "CommandResult",
    "backend_unreachable_result",
    "confirm_tty",
    "cycle_result_command",
    "drive_cycle",
    "get_verbose",
    "identity_from_args",
    "init_services_cli",
    "launch_limits_from_args",
    "log_startup_summary",
    "pipeline_summary",
    "resolve_campaign",
    "resolve_cycle",
    "resolve_target",
    "set_verbose",
]
