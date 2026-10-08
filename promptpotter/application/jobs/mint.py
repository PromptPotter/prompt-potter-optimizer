"""One application seam for the "dataset name → minted cycle" prologue, shared by the web mint,
the CLI and the embedded launch; each caller keeps only its own surface concerns."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.task_context import (
    campaign_framing,
    commit_task_framing,
    committed_task_context,
)
from promptpotter.application.campaign_config import under_record
from promptpotter.application.datasets.authored import config_cell_scorer
from promptpotter.application.initialization.session import auto_mint_session
from promptpotter.application.jobs.quota import admit_spend
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    resolved_dataset_name,
)
from promptpotter.application.preflight import (
    check_search_pool_holds_round,
    refuse_below_reasoning_floor,
)
from promptpotter.application.runner.campaign_ids import build_origin_cycle_id, mint_campaign_id
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.bench import BankPartition, partition_bank
from promptpotter.domain.campaign import (
    Arm,
    ArmRequest,
    HeadToHeadRecord,
    Instrument,
    Treatment,
    bench_instrument,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.l4.inner_origin import instrument_of
from promptpotter.domain.launch_limits import LaunchLimits, refuse_arm_halt
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.domain.run_records import CycleSeed, OriginSource
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, campaign_cycles_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, PayloadInvalidError
from promptpotter.shared.hashing import dataset_hash

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores


logger = logging.getLogger(__name__)


def _noop_log(*_args: Any, **_kwargs: Any) -> None:
    pass


class _Runnable(NamedTuple):
    treatment: Treatment
    partition: BankPartition


def _runnable(campaign_config: CampaignConfig, dataset: list[Sample]) -> _Runnable:
    """What the config and the bank alone decide — no session, no framing — so it is asked before
    a mint writes or a check-in bills."""
    treatment = select_optimizer(campaign_config.optimization).treatment()
    partition = partition_bank(dataset, campaign_config.dataset_split)
    refusal = check_search_pool_holds_round(campaign_config, len(partition.search))
    if refusal is not None:
        raise PayloadInvalidError(refusal, code="search_pool_below_round")
    return _Runnable(treatment, partition)


@dataclass(frozen=True)
class CyclePlan:
    pipeline_params: dict[str, Any]
    origin: OptSearchPoint
    cycle_id: str
    treatment: Treatment
    partition: BankPartition
    # A chosen prior origin's prompt fields as C0 — the same :class:`CycleSeed` an operator-steered
    # fork rides — or ``None`` for the dataset's authored one.
    seed: CycleSeed | None


@dataclass(frozen=True)
class MintedCycle:
    cycle_id: str
    session_id: str
    campaign_id: str
    # What the campaign runs: an arm's under its head-to-head's record (`under_record`).
    campaign_config: CampaignConfig


def _plan(
    session: Session,
    campaign_config: CampaignConfig,
    runnable: _Runnable,
    *,
    origin_override: dict[str, Any] | None,
    log: Callable[..., None] | None,
) -> CyclePlan:
    schema = session.pipeline_schema
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log or _noop_log)
    refuse_below_reasoning_floor(campaign_config, pipeline_params)
    seed = (
        CycleSeed(origin_prompt_fields=origin_override, origin_source=OriginSource.CAMPAIGN_ORIGIN)
        if origin_override
        else None
    )
    origin = resolve_origin_opt_search_point(
        prompt_node_names=schema.prompt_node_names(),
        dataset_dir=session.dataset_config_dir,
        seed=seed,
    )
    partition = runnable.partition
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
        treatment=runnable.treatment,
        partition=partition,
        seed=seed,
    )


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
    return _plan(
        session,
        campaign_config,
        _runnable(campaign_config, dataset),
        origin_override=origin_override,
        log=log,
    )


def write_plan_seed(stores: Stores, hop: CycleHop, plan: CyclePlan) -> None:
    """Put *plan*'s chosen origin on the cycle it was planned for — the one writer of a
    campaign-from-origin seed, for a fresh mint and a check-in's Start alike."""
    if plan.seed is not None:
        stores.campaigns.write_cycle_seed(hop, plan.seed)


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
            entry.campaign_id
            for entry in session.store.campaigns.enumerate_cycles()
            if entry.cycle_id == cycle_id
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


def _join_head_to_head(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    plan: CyclePlan,
    request: ArmRequest,
) -> Arm:
    """Declare the head-to-head off this arm's own instrument and budget when none is recorded,
    else refuse an arm off its instrument — or re-use a key another arm holds."""
    optimization = campaign_config.optimization
    partition = plan.partition
    instrument = bench_instrument(
        dataset_name=resolved_dataset_name(session, campaign_config),
        dataset_hash=dataset_hash(dataset),
        split=campaign_config.dataset_split,
        bench_ids=[s.id for s in partition.bench],
        scorer_id=config_cell_scorer(campaign_config)[1],
        origin_params=plan.pipeline_params,
        origin=plan.cycle_id.removeprefix("cycle_"),
    )
    budget = optimization.arm_budget
    campaigns = session.store.campaigns
    declared = campaigns.load_head_to_head(request.head_to_head_id)
    if declared is None:
        campaigns.declare_head_to_head(
            HeadToHeadRecord(
                head_to_head_id=request.head_to_head_id,
                created_at=utcnow_iso(),
                instrument=instrument,
                budget=budget,
            )
        )
    else:
        differs = [
            f"instrument.{name}"
            for name in Instrument.model_fields
            if getattr(declared.instrument, name) != getattr(instrument, name)
        ]
        if differs:
            raise ConflictError(
                f"head-to-head {request.head_to_head_id} declares another "
                f"{', '.join(differs)}: this arm would not be graded on its instrument",
                code="arm_off_instrument",
                details={"differs_on": differs},
            )
    taken = [
        other.campaign_id
        for campaign_dir in campaigns.iter_campaign_dirs()
        if (other := campaigns.load_campaign(campaign_dir.name)) is not None
        and other.arm is not None
        and other.arm.head_to_head_id == request.head_to_head_id
        and other.arm.arm_key == request.arm_key
    ]
    if taken:
        raise ConflictError(
            f"arm {request.arm_key} of {request.head_to_head_id} is campaign {taken[0]} already",
            code="arm_taken",
        )
    return Arm(
        head_to_head_id=request.head_to_head_id,
        arm_key=request.arm_key,
        treatment_digest=plan.treatment.digest,
    )


def _prompt_axes_only(
    session: Session, campaign_config: CampaignConfig
) -> dict[str, NodeSearchNarrowing]:
    """Every node's search space closed to the prompt's own fields: an optimizer that also moved
    a call's sampling or its reasoning rung would be graded on more than the prompt it wrote."""
    narrowing: dict[str, NodeSearchNarrowing] = {}
    for node in session.pipeline_schema.declared_nodes:
        held = campaign_config.optimizer_narrowing.get(node.name, NodeSearchNarrowing())
        still_open = PROMPT_STRING_FIELDS if held.param_keys is None else held.param_keys
        narrowing[node.name] = held.model_copy(
            update={"param_keys": [key for key in PROMPT_STRING_FIELDS if key in still_open]}
        )
    return narrowing


def under_declared_record(
    stores: Stores, campaign_config: CampaignConfig, arm: ArmRequest | None
) -> CampaignConfig:
    """*campaign_config* under the split and budget *arm*'s head-to-head has declared
    (``under_record``). Sessionless, so admission asks it: a launch is admitted on the ceiling it holds."""
    if arm is None:
        return campaign_config
    declared = stores.campaigns.load_head_to_head(arm.head_to_head_id)
    return campaign_config if declared is None else under_record(campaign_config, declared)


def _under_declaration(
    session: Session, campaign_config: CampaignConfig, arm: ArmRequest | None
) -> CampaignConfig:
    """The config an arm runs. It searches the prompt alone, and runs under its head-to-head's
    record — before its origin resolves, whose id the split moves."""
    if arm is None:
        return campaign_config
    narrowed = campaign_config.model_copy(
        update={"optimizer_narrowing": _prompt_axes_only(session, campaign_config)}
    )
    return under_declared_record(session.store, narrowed, arm)


def fresh_campaign_id(session: Session, campaign_config: CampaignConfig) -> str:
    """A brand-new random campaign id — what every mint that does NOT own its campaign's identity
    passes on. The L4 inner spawn is the one caller that does, deriving it from its cell."""
    return mint_campaign_id(resolved_dataset_name(session, campaign_config))


def _mint_runnable(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    runnable: _Runnable,
    *,
    campaign_id: str,
    arm: ArmRequest | None,
    origin_override: dict[str, Any] | None,
    log: Callable[..., None] | None,
) -> MintedCycle:
    """The mint itself, over a config already under its declaration and a bank already found
    runnable — each resolved once, by whichever entry below was asked."""
    plan = _plan(session, campaign_config, runnable, origin_override=origin_override, log=log)
    arm_of = (
        None if arm is None else _join_head_to_head(session, campaign_config, dataset, plan, arm)
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
        dataset_size=len(dataset),
        treatment=plan.treatment,
        arm=arm_of,
    )
    write_plan_seed(session.store, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), plan)
    return MintedCycle(
        cycle_id=cycle_id,
        session_id=session_id,
        campaign_id=campaign_id,
        campaign_config=campaign_config,
    )


def prepare_fresh_cycle(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    campaign_id: str,
    arm: ArmRequest | None,
    origin_override: dict[str, Any] | None = None,
    log: Callable[..., None] | None = None,
) -> MintedCycle:
    """Mint a fresh campaign + session + root cycle. ``campaign_id`` is a REQUIRED keyword with no
    default: who owns the campaign's identity is a decision, and a default picks it for you."""
    campaign_config = _under_declaration(session, campaign_config, arm)
    return _mint_runnable(
        session,
        campaign_config,
        dataset,
        _runnable(campaign_config, dataset),
        campaign_id=campaign_id,
        arm=arm,
        origin_override=origin_override,
        log=log,
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
    arm: ArmRequest | None,
    limits: LaunchLimits,
    origin_override: dict[str, Any] | None = None,
    log: Callable[..., None] | None = None,
) -> MintedCycle:
    """The fresh mint behind the dataset's framing, for every entry point that starts a campaign. An
    L4 inner cell mints through :func:`prepare_fresh_cycle`, so a round's cells never race to decompose."""
    if arm is not None:
        if task_text or origin_override:
            raise PayloadInvalidError(
                "an arm runs the head-to-head's origin and framing: no task text, no origin override"
            )
        refuse_arm_halt(limits.halt_at_accuracy)
    campaign_config = _under_declaration(session, campaign_config, arm)
    # Before the check-in below bills. The rest of the plan waits on the framing it commits.
    runnable = _runnable(campaign_config, dataset)
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
        minted = _mint_runnable(
            session,
            campaign_config,
            dataset,
            runnable,
            campaign_id=campaign_id,
            arm=arm,
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
    "under_declared_record",
    "write_plan_seed",
]
