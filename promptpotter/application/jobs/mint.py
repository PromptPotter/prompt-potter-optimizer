"""One application seam for the "dataset name → minted cycle" prologue, shared by the web mint,
the CLI and the embedded launch; each caller keeps only its own surface concerns."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from promptpotter.application.initialization.session import auto_mint_session
from promptpotter.application.jobs.quota import admit_spend
from promptpotter.application.optimization.task_context import (
    campaign_framing,
    commit_task_framing,
    committed_task_context,
)
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    resolved_dataset_name,
)
from promptpotter.application.runner.campaign_ids import build_origin_cycle_id, mint_campaign_id
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.l4.inner_origin import instrument_of
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, campaign_cycles_dir

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.campaign_config import CampaignConfig
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
    partition = partition_bank(dataset, campaign_config.dataset_split)
    return CyclePlan(
        pipeline_params=pipeline_params,
        origin=origin,
        # Config-aware identity: the overlay-merged params (connector model/config included) AND
        # the campaign's framing, so the id reflects the same render the measurement key does —
        # over the rows the search draws, which `run_optimization` partitions the same way.
        cycle_id=build_origin_cycle_id(
            origin,
            schema,
            list(partition.search),
            pipeline_params,
            # PURE read, and the reason identity can hold the framing at all: check-in commits
            # `task_context.yaml` before anything asks for an id (`mint_framed_cycle`), so the id
            # hashes the prompt the run will actually score.
            framing=campaign_framing(session.store, campaign_config, session.dataset_name),
            demo=partition.demo,
        ),
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


def _warn_on_novel_instrument(
    session: Session,
    plan: CyclePlan,
    campaign_config: CampaignConfig,
    *,
    log: Callable[..., None],
) -> None:
    """Say so, BEFORE the spend, when nothing this dataset has already banked will replay — the
    inner INSTRUMENT (``connectors/promptpotter.py::_identity_config``) is computed from the engine,
    so an ordinary edit to a panel, a layout or the estimator moves it and silently strands every
    prior cell. The cost otherwise lands weeks later as "why does nothing accumulate", a question
    about numbers that no longer exist to be asked about.

    Silent on the healthy path and on every backend declaring no instrument."""
    instrument = instrument_of(plan.pipeline_params)
    if instrument is None:
        return
    dataset_name = resolved_dataset_name(session, campaign_config)
    prior: list[str] = []
    for campaign_dir in session.store.campaigns.iter_campaign_dirs():
        campaign = session.store.campaigns.load_campaign(campaign_dir.name)
        if campaign is None or campaign.dataset_name != dataset_name:
            continue
        root = campaign_cycles_dir(campaign_dir) / campaign.root_cycle_id
        doc = read_json_tolerant(CycleLayout(root).round_file(0), {})
        banked = instrument_of(doc.get("pipeline_params") if isinstance(doc, dict) else None)
        if banked is not None:
            prior.append(banked)
    if not prior or instrument in prior:
        return
    logger.warning(
        "Inner instrument %s matches none of the %d prior %s campaign(s), which carry %d other "
        "fingerprint(s) — no banked cell replays, so this run re-measures its origin and can be "
        "compared to none of them. Expected while the engine is being rewritten; it is also the "
        "reason a panel does not accumulate across runs.",
        instrument,
        len(prior),
        dataset_name,
        len(set(prior)),
    )
    log(f"NOTE: instrument {instrument} is new — none of {len(prior)} prior campaigns replay")


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
    _warn_on_novel_instrument(session, plan, campaign_config, log=log or _noop_log)
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


def _description_to_decompose(
    session: Session, campaign_config: CampaignConfig, task_text: str | None
) -> str | None:
    """An operator's ``task_text`` always commits. Otherwise a framed campaign decomposes its
    dataset's ``task_description.md`` once, while no ``task_context.yaml`` is committed."""
    if task_text:
        return task_text
    if campaign_config.task_framing == "off" or session.dataset_config_dir is None:
        return None
    if committed_task_context(session.store, session.dataset_name):
        return None
    path = Path(session.dataset_config_dir) / "task_description.md"
    return (path.read_text(encoding="utf-8").strip() if path.is_file() else "") or None


async def mint_framed_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    campaign_id: str,
    task_text: str | None,
    origin_override: dict[str, Any] | None = None,
    log: Callable[..., None] | None = None,
) -> MintedCycle:
    """:func:`prepare_fresh_cycle` behind the dataset's framing — the mint of every entry point
    that starts a campaign. An L4 inner cell mints through ``prepare_fresh_cycle`` alone, so a
    round's cells never race to decompose. ``task_text`` is an operator's own description."""
    description = _description_to_decompose(session, campaign_config, task_text)
    # The cycle id hashes the framing this commits, so the check-in bills a scratch ledger first
    # and its records are carried onto the minted cycle — the run's own meter.
    with TemporaryDirectory() as scratch:
        checkin_ledger = CycleEventLog.open(CycleDir(Path(scratch)))
        if description is not None:
            assert session.dataset_name, "a framing commits to the dataset the session opened"
            await commit_task_framing(
                session.store,
                session.dataset_name,
                description,
                campaign_id=campaign_id,
                ledger=checkin_ledger,
                book=await asyncio.to_thread(admit_spend, stores=session.store, bucket="checkin"),
            )
            logger.info("Committed task framing for %s from its check-in", session.dataset_name)
        minted = prepare_fresh_cycle(
            session,
            campaign_config,
            dataset,
            campaign_id=campaign_id,
            origin_override=origin_override,
            log=log,
        )
        run_ledger = CycleEventLog.open(
            CycleDir(
                session.store.campaigns.cycle_dir(
                    CycleHop(campaign_id=minted.campaign_id, cycle_id=minted.cycle_id)
                )
            )
        )
        for _offset, record in checkin_ledger.iter():
            run_ledger.append(record)
    return minted


__all__ = [
    "CyclePlan",
    "fresh_campaign_id",
    "mint_framed_cycle",
    "prepare_fresh_cycle",
    "resolve_cycle_plan",
]
