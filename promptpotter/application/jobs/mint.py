"""One application seam for the "dataset name → minted cycle" prologue, shared by the web mint
and the CLI; each caller keeps only its own surface concerns. Assembling it by hand drifted."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.config import configure_and_apply_pipeline, resolved_dataset_name
from promptpotter.application.initialization.session import auto_mint_session
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.runner.campaign_ids import build_origin_cycle_id, mint_campaign_id
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.run_records import CycleSeed

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample


logger = logging.getLogger(__name__)


def _noop_log(*_args: Any, **_kwargs: Any) -> None:
    pass


def _campaign_origin_seed(origin_override: dict[str, Any] | None) -> CycleSeed | None:
    """A campaign-from-origin seed — a chosen prior origin's prompt fields as C0, or ``None`` for the
    dataset's authored one. The same :class:`CycleSeed` an operator-steered fork rides."""
    if not origin_override:
        return None
    return CycleSeed(origin_prompt_fields=origin_override, origin_source="campaign_origin")


@dataclass(frozen=True)
class CyclePlan:
    pipeline_params: dict[str, Any]
    origin: OptSearchPoint
    cycle_id: str


@dataclass(frozen=True)
class MintedCycle:
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
    """``origin_override`` IS the origin when set, so the cycle_id derives from it. No disk mint:
    ``resume`` calls this to recompute the expected id and compare it for drift."""
    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log or _noop_log)
    origin = resolve_origin_opt_search_point(
        prompt_node_names=schema.prompt_node_names(),
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
    """Say so, BEFORE the spend, when this exact origin has already been run — ``cycle_id`` is
    content-addressed, so a second ``new`` re-runs the identical seed under a fresh campaign."""
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
        "for an origin you have CHANGED (optimizer prompt, config, or dataset).",
        cycle_id,
        ", ".join(prior),
    )
    log(f"NOTE: identical origin already run in {', '.join(prior)} — consider `resume`")


def fresh_campaign_id(session: Session, campaign_config: CampaignConfig) -> str:
    """A brand-new random campaign id — what every mint that does NOT own its campaign's identity
    passes on. The L4 inner spawn is the one caller that does, deriving it from its cell."""
    return mint_campaign_id(resolved_dataset_name(session, campaign_config))


def prepare_fresh_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    campaign_id: str,
    origin_override: dict[str, Any] | None = None,
    log: Callable[..., None] | None = None,
) -> MintedCycle:
    """Mint a fresh campaign + session + root cycle. ``campaign_id`` is a REQUIRED keyword with no
    default: who owns the campaign's identity is a decision, and a default picks it for you."""
    seed = _campaign_origin_seed(origin_override)
    plan = resolve_cycle_plan(
        session, campaign_config, dataset, origin_override=origin_override, log=log
    )
    # **Never sweep the inner sandbox here.** A fresh mint has a fresh ``campaign_id`` and the
    # key carries it (``store/layout.py::inner_sandbox_key``), so an rmtree at this line can
    # only destroy a DIFFERENT campaign's inner history. One did: 39 banked inner campaigns.
    _warn_on_duplicate_origin(session, plan.cycle_id, log=log or _noop_log)
    session_id, campaign_id, cycle_id = auto_mint_session(
        session,
        campaign_config,
        hop=CycleHop(campaign_id=campaign_id, cycle_id=plan.cycle_id),
        origin_prompt_fields=plan.origin.prompt_field_dict(),
        dataset_size=len(dataset),
        pipeline_params=plan.pipeline_params,
        active_steps=list(plan.pipeline_params.get("steps", [])),
    )
    if seed is not None:
        session.store.campaigns.write_cycle_seed(
            CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), seed
        )
    return MintedCycle(
        cycle_id=cycle_id,
        session_id=session_id,
        campaign_id=campaign_id,
    )


__all__ = [
    "CyclePlan",
    "MintedCycle",
    "fresh_campaign_id",
    "prepare_fresh_cycle",
    "resolve_cycle_plan",
]
