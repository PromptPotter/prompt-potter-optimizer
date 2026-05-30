"""Cross-command helpers + shared module state for the CLI commands.

* ``CommandResult`` — the typed return shape every subcommand emits.
* ``set_verbose`` / ``get_verbose`` — module-level verbose flag,
  flipped once from :func:`main` before dispatch.
* ``init_services_cli`` — CLI-style service init (logging style).
* ``log_startup_summary`` — one-line pipeline/backend/dataset summary.
* ``_prepare_cycle`` / ``_mint_session_and_cycle`` — pipeline+origin
  application; used by both fresh-init and divergence-detect paths.
* ``_DIVERGENCE_HINT`` — operator hint derived from the
  ``ResumeCheckpointKind`` gating table.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample

logger = logging.getLogger("promptpotter.presentation.cli")


@dataclass
class CommandResult:
    """``data`` is machine-readable; ``human`` is pre-rendered text. ``main()`` picks one."""

    data: dict[str, Any] | None = None
    human: str | None = None


_VERBOSE = False


def set_verbose(value: bool) -> None:
    """Toggle verbose mode. Called once from ``main()`` before dispatch."""
    global _VERBOSE
    _VERBOSE = value


def get_verbose() -> bool:
    """Read the current verbose flag (other command modules consult this)."""
    return _VERBOSE


def log_startup_summary(
    session: Session,
    pipeline_params: dict[str, Any] | None,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    """One-line collapsed summary of pipeline + backend + dataset + active nodes."""
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}" if ps else "pipeline unavailable"
    active = list((pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info("%s · %s · backend %s · dataset %s", pipe, nodes, backend_url, ds)


def campaign_result_human(campaign_dir: Any, *, dataset_name: str, cycle_id: str | None) -> str:
    """Operator-facing summary block for a finished ``new`` / ``resume`` run.

    Names the dataset, campaign, session, and cycle. ``campaign.json`` +
    ``log.md`` live at the campaign dir; per-session ``dashboard.json``
    and round detail live under ``cycles/{cycle_id}/``.
    """
    from promptpotter.infrastructure.store import root_cycle_id, session_index

    session_n = session_index(root_cycle_id(cycle_id)) if cycle_id else 1
    return (
        f"Dataset:   {dataset_name}\n"
        f"Campaign:  {campaign_dir.name}\n"
        f"Session:   {session_n}\n"
        f"Cycle:     {cycle_id or '?'}\n"
        f"Directory: {campaign_dir}\n"
        f"  campaign.json          — manifest\n"
        f"  log.md                 — campaign digest\n"
        f"  cycles/{cycle_id or '?'}/  — session telemetry (dashboard.json) + rounds"
    )


async def init_services_cli(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
    identity: IdentityContext | None = None,
) -> Session:
    """Initialize services for a CLI command (logging style + service init).

    *identity* defaults to the Stage-0 :func:`default_identity`; CLI callers
    derive it from ``args.tenant`` via :func:`identity_from_args`.
    """
    from promptpotter.application.bootstrap import init_services
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if _VERBOSE else "cli")
    project_root = Path(__file__).resolve().parents[4]
    return await init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=lambda msg: logger.info(msg) if _VERBOSE else None,
        identity=identity if identity is not None else default_identity(),
    )


def identity_from_args(args: Any) -> IdentityContext:
    """Build the Stage-0 :class:`IdentityContext` from CLI ``argparse`` flags.

    Resolution order:

    1. Explicit ``--tenant <slug>`` always wins (operator override).
    2. Otherwise, if a developer has registered (the default-tenant claim
       marker from first web sign-in records their ``user_id``), resolve to
       *that* registered operator — so a terminal ``new`` / ``resume`` lands in
       the same single workspace the authenticated web reads, instead of
       recreating an orphaned anonymous ``projects/default/`` tree.
    3. Otherwise fall back to anonymous ``default`` (never-registered install).

    The CLI is the seam where the flag/marker becomes the
    :class:`~promptpotter.domain.identity.TenantId`; everything past this point
    passes :class:`IdentityContext`, never bare strings.
    """
    from promptpotter.infrastructure.identity import registered_or_default_identity

    return registered_or_default_identity(getattr(args, "tenant", None))


def _prepare_cycle(
    session: Session, campaign_config: CampaignConfig, dataset: list[Sample]
) -> tuple[dict[Any, Any], OptSearchPoint, str]:
    """Apply pipeline → load origin → compute cycle_id. Returns (pipeline_params, origin, cycle_id)."""
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.origin import load_origin_prompt
    from promptpotter.application.runner import build_origin_cycle_id

    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(
        session, campaign_config, log=logger.info if _VERBOSE else (lambda *_a, **_k: None)
    )
    origin = load_origin_prompt(
        session.experiment_extract,
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_dir=session.dataset_config_dir,
    )
    return pipeline_params, origin, build_origin_cycle_id(origin, schema, dataset)


def _mint_session_and_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    cycle_id: str,
    init_params: dict[str, Any],
    pipeline_params: dict[str, Any],
    origin: OptSearchPoint,
    dataset_count: int,
) -> tuple[str, str, str]:
    """Mint session + campaign + root cycle with the CLI's pipeline-snapshot extras.

    Returns ``(session_id, campaign_id, cycle_id)``. ``auto_mint_session``
    writes ``campaign.json``, the root cycle index, and the 4-key active
    pointer; it also threads ``campaign_id`` onto *session*.
    """
    from promptpotter.application.bootstrap.session import auto_mint_session

    return auto_mint_session(
        session,
        campaign_config,
        cycle_id=cycle_id,
        origin_prompt_fields=origin.prompt_field_dict(),
        dataset_size=dataset_count,
        experiment_id=init_params.get("experiment_id"),
        pipeline_params=pipeline_params,
        active_steps=list(pipeline_params.get("steps", [])),
    )


def _build_divergence_hint() -> str:
    """Derive the divergence-checked-kinds list from the RESUME_CHECKPOINT_GATING table.

    The hint used to hardcode the gated kinds, which silently rotted
    every time a new ``ResumeCheckpointKind`` member landed. Now it walks the
    enum so adding a kind (with its gating choice) updates the operator
    message automatically.
    """
    from promptpotter.application.optimization.resume_and_fork import (
        RESUME_CHECKPOINT_GATING,
        GatingMode,
    )

    replayed = sorted(
        k.value for k, m in RESUME_CHECKPOINT_GATING.items() if m is GatingMode.REPLAYED
    )
    archival = sorted(
        k.value for k, m in RESUME_CHECKPOINT_GATING.items() if m is GatingMode.ARCHIVAL
    )
    return (
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


_DIVERGENCE_HINT = _build_divergence_hint()


def confirm_tty(prompt: str, *, default_no: bool = True) -> bool | None:
    """Ask y/N at the terminal. Returns:

    * ``True`` if the operator typed y/Y/yes
    * ``False`` if they typed n/N/no (or just Enter when ``default_no``)
    * ``None`` if stdin is not a TTY (callers should fall back to a
      non-interactive default — typically the same as ``no``).

    Keeps interactive prompts at one shared site so non-TTY detection
    (CI, piped invocations) is consistent across the CLI.
    """
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
    "campaign_result_human",
    "confirm_tty",
    "get_verbose",
    "identity_from_args",
    "init_services_cli",
    "log_startup_summary",
    "set_verbose",
]
