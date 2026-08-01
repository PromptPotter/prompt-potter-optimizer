"""One application seam for the "dataset name → minted cycle" prologue.

Both entry points assembled this prologue by hand and drifted — the web mint
once ran with EMPTY framing until ``load_or_build_task_context`` was bolted on,
and the steps (pipeline overlay → origin OSP → cycle_id → ``auto_mint_session``)
are application work the CLI was doing from ``presentation/``. They live here so
the web (``launcher.mint_campaign_command``, detached) and the CLI (``new``,
inline) share one definition; each caller keeps only its own surface concerns
(quota/spend gates + detached task vs. inline display + task-context check-in).

:func:`resolve_cycle_plan` (pipeline → origin → cycle_id, no disk mint) is also
what ``resume`` recomputes to detect config drift, so it stays callable on its
own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.session import auto_mint_session
from promptpotter.application.config import configure_and_apply_pipeline
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.runner.identity import build_origin_cycle_id
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.store.io import rmtree_robust

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample


logger = logging.getLogger(__name__)


def _noop_log(*_args: Any, **_kwargs: Any) -> None:
    """Default pipeline-apply trace sink — silent unless a caller wires one."""


def _campaign_origin_seed(origin_override: dict[str, Any] | None) -> CycleSeed | None:
    """A campaign-from-origin seed (a chosen prior origin's prompt fields as C0),
    or ``None`` to use the dataset's authored origin.

    The same :class:`CycleSeed` an operator-steered fork rides — so the fresh root
    mint funnels through the one seed seam the runner already reads at bootstrap
    (``runner/entry.py::_read_cycle_seed``). ``origin_source`` stamps the C0 lineage."""
    if not origin_override:
        return None
    return CycleSeed(origin_prompt_fields=origin_override, origin_source="campaign_origin")


@dataclass(frozen=True)
class CyclePlan:
    """Pre-mint cycle shape: applied pipeline + resolved origin + its cycle_id."""

    pipeline_params: dict[str, Any]
    origin: OptSearchPoint
    cycle_id: str


@dataclass(frozen=True)
class MintedCycle:
    """A fresh campaign + root cycle on disk, carrying the plan it was minted from."""

    cycle_id: str
    session_id: str
    campaign_id: str


def resolve_cycle_plan(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    origin_override: dict[str, Any] | None = None,
    log: Callable[..., None] | None = None,
) -> CyclePlan:
    """``origin_override`` (campaign-from-origin) is a chosen prior origin's prompt
    fields — when set it *is* the origin, so the cycle_id derives from it, not the
    dataset's authored origin. No disk mint — ``resume`` calls this (with no
    override) to recompute the expected cycle_id and compare it against
    ``campaign.json::root_content_hash`` for drift detection. ``log`` traces the
    pipeline-apply step (CLI passes ``logger.info`` under ``-v``).
    """
    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log or _noop_log)
    origin = resolve_origin_opt_search_point(
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_dir=session.dataset_config_dir,
        seed=_campaign_origin_seed(origin_override),
    )
    return CyclePlan(
        pipeline_params=pipeline_params,
        origin=origin,
        # Config-aware identity: pass the overlay-merged params (connector model/config
        # included) so the cycle id reflects the connector config and agrees with the
        # measurement key. Resume recomputes this for drift detection — an existing
        # config-blind campaign's stored hash won't match, handled as a benign re-stamp
        # when the config diff is NONE (resume.py).
        cycle_id=build_origin_cycle_id(origin, schema, dataset, pipeline_params),
    )


def _warn_on_duplicate_origin(
    session: Session,
    cycle_id: str,
    *,
    log: Callable[..., None],
) -> None:
    """Say so, BEFORE the spend, when this exact origin has already been run.

    ``cycle_id`` is content-addressed, so a second ``new`` over an unedited origin mints a
    fresh campaign whose root cycle carries the SAME id and re-runs the identical seed. The
    store takes it happily — each campaign has its own directory — and the operator learns
    hours and dollars later, from a picker holding N campaigns with identical stats.

    A warning and not a refusal: ``new`` is the verb an operator reaches for, the duplicate
    is legal, and a gate that guesses wrong blocks the main path. What they need is the
    campaign id to ``resume`` instead, which is what this prints.
    """
    prior = sorted(
        {
            str(entry.get("campaign_id") or "")
            for entry in session.store.campaigns.enumerate_cycles()
            if entry.get("cycle_id") == cycle_id and entry.get("campaign_id")
        }
    )
    if not prior:
        return
    logger.warning(
        "This origin has already been run as cycle %s in campaign(s) %s — a fresh `new` "
        "re-measures the identical seed. `resume` continues one of those instead; `new` is "
        "for an origin you have CHANGED (meta-prompt, config, or dataset).",
        cycle_id,
        ", ".join(prior),
    )
    log(f"NOTE: identical origin already run in {', '.join(prior)} — consider `resume`")


def prepare_fresh_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    origin_override: dict[str, Any] | None = None,
    log: Callable[..., None] | None = None,
) -> MintedCycle:
    """Mint a fresh campaign + session + root cycle from a resolved dataset.

    The one prologue ``new`` and the web mint share: resolve the cycle plan, then
    ``auto_mint_session`` writes ``campaign.json``, the root cycle index, and the
    4-key active pointer (also threading ``campaign_id`` onto *session*). Callers
    own everything around it — quota/spend gates + the detached task (web), the
    inline display + task-context check-in (CLI).

    ``origin_override`` (campaign-from-origin) re-homes C0 to a chosen prior
    origin: the plan's cycle_id derives from it, and the matching
    :class:`CycleSeed` is appended to the cycle's ledger as a ``CycleSeedRecord`` so
    the runner seam resolves the same origin at bootstrap (the generic fork/steer read
    path). The
    root is parentless, so the origin still scores fresh — no inherited measurement.
    """
    seed = _campaign_origin_seed(origin_override)
    plan = resolve_cycle_plan(
        session, campaign_config, dataset, origin_override=origin_override, log=log
    )
    # A fresh mint of a content-addressed cycle_id means "restart THIS exact origin".
    # Its off-registry inner proxy sandbox (``<workspace>/.inner/<cycle_id>``, written
    # only by an L4 recursion) is therefore a PRIOR run's leftover — a stale,
    # tenant-pooled measurement archive that would warm this run's inner δ-ruler from
    # foreign campaigns and un-attribute its proxy signal. Sweep it so every fresh
    # outer run starts isolated. Resume/fork reach the sandbox by other paths (they
    # never call this prologue), so their live trees are untouched; capacity-1 minting
    # keeps a concurrent same-origin run from racing this. For a non-L4 mint (or an
    # inner-campaign mint, whose sandbox root sits one level deeper) the path is
    # absent, so this is a no-op.
    # ``rmtree_robust``, never a bare ``shutil.rmtree``: these trees nest langfuse
    # observation dirs past Windows MAX_PATH (measured at 668 chars), where a plain
    # rmtree fails — and with ``ignore_errors=True``, which this call used to carry, it
    # failed SILENTLY and left the stale sandbox exactly where the comment above promises
    # it is gone. A sweep that cannot report its own failure is not a sweep.
    inner_sandbox = session.store.projects_root.parent / ".inner" / plan.cycle_id
    if inner_sandbox.exists():
        rmtree_robust(inner_sandbox)
    _warn_on_duplicate_origin(session, plan.cycle_id, log=log or _noop_log)
    session_id, campaign_id, cycle_id = auto_mint_session(
        session,
        campaign_config,
        cycle_id=plan.cycle_id,
        origin_prompt_fields=plan.origin.prompt_field_dict(),
        dataset_size=len(dataset),
        pipeline_params=plan.pipeline_params,
        active_steps=list(plan.pipeline_params.get("steps", [])),
    )
    if seed is not None:
        session.store.campaigns.write_cycle_seed(campaign_id, cycle_id, seed)
    return MintedCycle(
        cycle_id=cycle_id,
        session_id=session_id,
        campaign_id=campaign_id,
    )


__all__ = ["CyclePlan", "MintedCycle", "prepare_fresh_cycle", "resolve_cycle_plan"]
