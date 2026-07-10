"""Cross-command helpers + shared module state for the CLI commands.

* ``CommandResult`` — the typed return shape every subcommand emits.
* ``set_verbose`` / ``get_verbose`` — module-level verbose flag,
  flipped once from :func:`main` before dispatch.
* ``init_services_cli`` — CLI-style service init (logging style).
* ``log_startup_summary`` — one-line pipeline/backend/dataset summary.
* ``_DIVERGENCE_HINT`` — operator hint derived from the
  ``ResumeCheckpointKind`` gating table.

The mint prologue (pipeline → origin → cycle_id → mint) lives in the
application layer at ``application/jobs/mint.py`` — both CLI ``new`` and the
web mint call it; the CLI commands no longer assemble it here.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.infrastructure.store.paths import REPO_ROOT
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.infrastructure.store import Stores
    from promptpotter.presentation.cli.session import SessionCtx

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

    Names the dataset, campaign, and cycle. ``campaign.json`` + ``log.md``
    live at the campaign dir; per-cycle ``dashboard.json`` and round detail
    live under ``cycles/{cycle_id}/``.
    """
    return (
        f"Dataset:   {dataset_name}\n"
        f"Campaign:  {campaign_dir.name}\n"
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
    project_root = REPO_ROOT
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


def bind_session_identity(session: Session, ctx: SessionCtx) -> None:
    """Stamp a resumed/loaded session's identity (session/campaign/cycle id) onto
    the freshly-initialized :class:`Session` — the shared bind every cycle-scoped
    CLI command runs after :func:`init_services_cli`."""
    session.session_id = ctx.session_id
    session.campaign_id = ctx.campaign_id
    session.state.cycle_id = ctx.cycle_id


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
    # Import-time exhaustiveness (built once at module load): every gated kind
    # must surface in the operator hint. Fails at the source if a format edit
    # ever drops a branch — replaces a standalone completeness test.
    assert all(k.value in hint for k in RESUME_CHECKPOINT_GATING), (
        "divergence hint must name every ResumeCheckpointKind"
    )
    return hint


_DIVERGENCE_HINT = _build_divergence_hint()


def resolve_campaign(stores: Stores, needle: str) -> str:
    """Resolve *needle* to a campaign id; accepts full id, 6-hex suffix, or unambiguous prefix.

    Shared by any command that names a campaign by needle (``verify``, ``noise-floor``) — one
    resolver, not a copy per command."""
    ids = stores.campaigns.list_campaign_ids()
    if needle in ids:
        return needle
    candidates = [cid for cid in ids if cid.endswith(f"__{needle}") or cid.startswith(needle)]
    if needle and not candidates:
        candidates = [cid for cid in ids if needle in cid]
    if not candidates:
        raise SystemExit(f"ERROR: no campaign matches {needle!r}.")
    if len(candidates) > 1:
        raise SystemExit(
            f"ERROR: {needle!r} matches {len(candidates)} campaigns: "
            f"{', '.join(candidates[:5])}{'…' if len(candidates) > 5 else ''}. "
            "Pass the full id."
        )
    return candidates[0]


def resolve_cycle(stores: Stores, campaign_id: str, hint: str | None) -> str:
    """Resolve a cycle id within *campaign_id*; ``hint=None`` auto-picks the sole cycle (raises on ambiguity)."""
    cycles_dir = stores.campaigns.campaign_root_dir(campaign_id) / "cycles"
    if not cycles_dir.exists():
        raise SystemExit(f"ERROR: campaign {campaign_id!r} has no cycles/ directory.")
    ids = sorted(p.name for p in cycles_dir.iterdir() if p.is_dir())
    if not ids:
        raise SystemExit(f"ERROR: campaign {campaign_id!r} has no cycles on disk.")
    if hint:
        matches = [cid for cid in ids if cid == hint or cid.startswith(hint) or hint in cid]
        if not matches:
            raise SystemExit(f"ERROR: no cycle in {campaign_id!r} matches {hint!r}.")
        if len(matches) > 1:
            raise SystemExit(
                f"ERROR: {hint!r} matches {len(matches)} cycles in {campaign_id!r}: "
                f"{', '.join(matches[:5])}."
            )
        return matches[0]
    if len(ids) > 1:
        raise SystemExit(
            f"ERROR: campaign {campaign_id!r} has {len(ids)} cycles; pass --cycle <prefix>. "
            f"Available: {', '.join(ids[:5])}{'…' if len(ids) > 5 else ''}."
        )
    return ids[0]


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
    "bind_session_identity",
    "campaign_result_human",
    "confirm_tty",
    "get_verbose",
    "identity_from_args",
    "init_services_cli",
    "log_startup_summary",
    "resolve_campaign",
    "resolve_cycle",
    "set_verbose",
]
