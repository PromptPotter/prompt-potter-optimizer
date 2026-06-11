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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.session import auto_mint_session
from promptpotter.application.config import configure_and_apply_pipeline
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.runner import build_origin_cycle_id
from promptpotter.domain.run_records import CycleSeed

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample


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

    pipeline_params: dict[str, Any]
    origin: OptSearchPoint
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
    """Apply the pipeline overlay, resolve the origin OSP, derive its cycle_id.

    ``origin_override`` (campaign-from-origin) is a chosen prior origin's prompt
    fields — when set it *is* the origin, so the cycle_id derives from it, not the
    dataset's authored origin. No disk mint — ``resume`` calls this (with no
    override) to recompute the expected cycle_id and compare it against
    ``campaign.json::root_content_hash`` for drift detection. ``log`` traces the
    pipeline-apply step (CLI passes ``logger.info`` under ``-v``).
    """
    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log or _noop_log)
    origin = resolve_origin_opt_search_point(
        session.experiment_extract,
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_dir=session.dataset_config_dir,
        seed=_campaign_origin_seed(origin_override),
    )
    return CyclePlan(
        pipeline_params=pipeline_params,
        origin=origin,
        cycle_id=build_origin_cycle_id(origin, schema, dataset),
    )


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
    :class:`CycleSeed` is written to ``.overrides/seed.json`` so the runner seam
    resolves the same origin at bootstrap (the generic fork/steer read path). The
    root is parentless, so the origin still scores fresh — no inherited measurement.
    """
    seed = _campaign_origin_seed(origin_override)
    plan = resolve_cycle_plan(
        session, campaign_config, dataset, origin_override=origin_override, log=log
    )
    session_id, campaign_id, cycle_id = auto_mint_session(
        session,
        campaign_config,
        cycle_id=plan.cycle_id,
        origin_prompt_fields=plan.origin.prompt_field_dict(),
        dataset_size=len(dataset),
        experiment_id=session.experiment_id,
        pipeline_params=plan.pipeline_params,
        active_steps=list(plan.pipeline_params.get("steps", [])),
    )
    if seed is not None:
        session.store.campaigns.write_cycle_seed(campaign_id, cycle_id, seed)
    return MintedCycle(
        pipeline_params=plan.pipeline_params,
        origin=plan.origin,
        cycle_id=cycle_id,
        session_id=session_id,
        campaign_id=campaign_id,
    )


__all__ = ["CyclePlan", "MintedCycle", "prepare_fresh_cycle", "resolve_cycle_plan"]
