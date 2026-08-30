from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.phases import StopReason
from promptpotter.domain.results import HeadlineMetric
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellScorer
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store.io import validate_path_component
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.session_pointer import mint_session_id, save_active_pointer
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.jobs.mint import CyclePlan
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailView
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger


logger = logging.getLogger(__name__)


@dataclass
class ScorerSetup:
    scorer: CellScorer | None = None
    scorer_id: str = "none"
    scorer_formula: str | None = None
    scorer_cell_formula: str | None = None
    # WHICH number the operator's surfaces headline. Here rather than only on
    # `dashboard.json` because the terminal is an entry point too: served to the browser
    # alone, a campaign that declares `ability` still led every CLI line with the
    # subset-relative accuracy, which is the one reading `per_round_resubset` makes
    # unsafe (`knobs.py::headline_subset_relative_under_resubset`).
    headline_metric: HeadlineMetric = "accuracy"
    scoring_set: list[Sample] = field(default_factory=list)
    degradation_checks: list[StopRule] = field(default_factory=list)

    def require_scorer(self) -> CellScorer:
        """The compiled scorer, or a loud stop. ``None`` means ``populate_session_scoring`` has not
        run, so anything grading a cell here would be grading it under no declared formula."""
        if self.scorer is None:
            raise RuntimeError(
                "session.scoring.scorer is unset — populate_session_scoring must run first."
            )
        return self.scorer


@dataclass
class CycleSnapshot:
    """Per-cycle bundle — flips on fork."""

    cycle_id: str = ""
    tracing_campaign_id: str = ""
    resumed_from_round: int = 1
    obs: ObservabilityBridge | None = None
    audit_projection: AuditTrailView | None = None
    ledger: CycleEventLog | None = None
    # Forensic traceback for ``index.json::crash_traceback`` written by
    # ``mark_finished``. Operator-facing summary (kind + message) is owned by
    # the canonical ``ErrorRecord`` on the ledger; this field is the in-process
    # conduit between the runner's ``except`` block and ``_finalize_run`` only.
    crash_traceback: str | None = None


@dataclass
class Session:
    store: Stores
    backend_id: str
    backend_client: BackendClient
    # Non-optional: the schema decides what a measurement MEANS (which node carries the
    # prompt, which configs are hashed into the identity), so a session without one is
    # not a degraded session, it is a session that cannot measure. `_resolve_pipeline_schema`
    # raises instead of handing back a `None` for ~40 readers to each mis-handle quietly.
    pipeline_schema: PipelineSchema
    samples: list[Sample] = field(default_factory=list)
    index_terms: list[str] = field(default_factory=list)
    identity: IdentityContext = field(default_factory=default_identity)
    dataset_name: str | None = None
    # Resolved tenant-first at init (`readable_dataset_dir`): tenant
    # uploads at `projects/{tenant}/datasets/{slug}/`, else repo `datasets/{name}/`.
    # The single resolution seam — every dataset-file loader reads this rather than
    # recomputing a repo path from the bare name.
    dataset_config_dir: Path | None = None
    tenant_root: str = ""
    pipeline_params: dict[str, Any] = field(default_factory=dict)
    langfuse: LangfuseLogger | None = None

    session_id: str = ""
    campaign_id: str = ""

    state: CycleSnapshot = field(default_factory=CycleSnapshot)
    scoring: ScorerSetup = field(default_factory=ScorerSetup)

    source: str = ""
    # This cycle was babysat — an operator directly edited an engine-owned/locked
    # value (ADR-0005). Read from the cycle index at init; forces every run
    # this cycle scores to grade C (excluded from digest / reuse / L4).
    human_intervened: bool = False

    @property
    def hop(self) -> CycleHop:
        """This session's cycle as the PAIR that names it. Derived, never stored — ``cycle_id`` flips on a fork, and it
        repeats across sibling ``.inner`` sandboxes, so reading either half alone crosses one fan-out into another."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.state.cycle_id)

    def llm_node_name(self) -> str:
        """The dataset's prompt-bearing LLM node — the override target for a per-cell seed or model pin. Derived from the
        schema, never a literal, and RAISES rather than guessing: a dataset with no prompt node cannot carry an override."""
        names = self.pipeline_schema.prompt_node_names()
        if not names:
            raise ValueError(
                f"dataset {self.dataset_name!r} declares no prompt-bearing node — "
                "cannot target a per-cell seed / model override"
            )
        return names[0]

    # `pause_check` returns True while the operator has requested a pause
    # (`.runtime/pause.flag` present — the single operator-interrupt flag). The
    # loop checkpoints poll it and, when set, declare PAUSED and exit cleanly:
    # the worker ends, the cycle stays resumable. Bound at the runner seam and
    # also fed to `set_abort_check` so a pause breaks a long rate-limit wait.
    pause_check: Callable[[], bool] | None = None
    # The ENCLOSING run's `pause_check`, so an L4 outer cycle's stop reaches the inner cycle
    # instrumenting it. Captured once at the runner seam; `None` for a top-level run.
    inherited_pause_check: Callable[[], bool] | None = None
    # `skip_check` returns True while a one-shot `.runtime/skip.flag` is present
    # (operator early-abort of the searchpoint scoring now). Unlike pause it
    # does NOT end the cycle — the per-sample checkpoint accepts the partial and
    # continues. `skip_consume` removes the flag the instant it fires so exactly
    # one searchpoint is cut, not the whole round.
    skip_check: Callable[[], bool] | None = None
    skip_consume: Callable[[], None] | None = None
    # `sample_lookahead_check`: how many samples the operator armed the walk to hold in flight, 1
    # when nothing is armed. Same read-and-consume pair as skip, spent a phase later — by the ROUND
    # that scored under it or the GROUP the press released, per `Connector.concurrency_arming`.
    sample_lookahead_check: Callable[[], int] | None = None
    sample_lookahead_consume: Callable[[], None] | None = None
    # `budget_tripped` returns the `StopReason` once a spend/token ceiling is met, else None.
    # Bound at the runner seam to the SAME `BudgetGate.tripped` the round loop consults — one
    # object, so the two cadences can't disagree and a mid-flight ceiling change moves both.
    # The round-boundary check alone let a whole round of scoring run past the ceiling; for an
    # L4 outer round that is `n_candidates x n_samples` inner CAMPAIGNS of overshoot.
    budget_tripped: Callable[[], StopReason | None] | None = None
    # What has been spent so far, for the panel that tells an optimizer how much run is left.
    # A callable off the same rollup `budget_tripped` reads, bound at the same seam, because the
    # amount lives only on the dashboard projection and `application/optimization/` must not
    # import one. A FLOOR while unpriced tokens are outstanding.
    spend_used: Callable[[], float] | None = None


def new_session_state(
    *,
    init_params: dict[str, Any],
    pipeline_params: dict[str, Any],
    active_steps: list[str],
) -> dict[str, Any]:
    return {
        "init_params": init_params,
        "pipeline_params": pipeline_params,
        "active_steps": active_steps,
        "origin_prompt_fields": {},
        "dataset_count": 0,
        "origin_accuracy": 0.0,
        "task_context": None,
    }


def _build_index_header(session: Session, dataset_size: int) -> dict[str, Any]:
    from promptpotter.config.settings import APP_VERSION

    nodes = list(session.pipeline_schema.nodes)
    return {
        "tool": "promptpotter",
        "version": APP_VERSION,
        "n_nodes": len(nodes),
        "steps": [n.name for n in nodes],
        "backend_url": session.backend_client.base_url,
        "backend_id": session.backend_id,
        # No dataset_name here — campaign.json::dataset_name is the one owner;
        # every reader derives from it (a header copy needed its own re-sync).
        "dataset_size": dataset_size,
    }


def auto_mint_session(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    hop: CycleHop,
    origin_acc: float = 0.0,
    origin_prompt_fields: dict[str, Any] | None = None,
    dataset_size: int = 0,
    pipeline_params: dict[str, Any] | None = None,
    active_steps: list[str] | None = None,
    label: str = "",
) -> tuple[str, str, str]:
    """Mint fresh campaign + session + root cycle; claim the active pointer. ``campaign_id`` comes from the CALLER, so an
    L4 inner spawn can hand in an id derived from the cell it measures and land back on a campaign it already ran."""
    from promptpotter.application.campaign_config import freeze_campaign_config
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        combined_optimizer_prompt_hash,
    )
    from promptpotter.application.pipeline_resolve import resolved_dataset_name
    from promptpotter.domain.campaign import Campaign

    target_hash = hop.cycle_id.removeprefix("cycle_")
    validate_path_component(target_hash)
    session_id = mint_session_id()
    now = utcnow_iso()
    dataset_name = resolved_dataset_name(session, campaign_config)
    optimizer_hash = combined_optimizer_prompt_hash()
    validate_path_component(hop.campaign_id)
    root_cycle = hop.cycle_id

    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "dataset_name": session.dataset_name,
        },
        pipeline_params=pipeline_params or {},
        active_steps=list(active_steps or []),
    )
    state["origin_accuracy"] = origin_acc
    state["dataset_count"] = dataset_size
    state["origin_prompt_fields"] = origin_prompt_fields or {}

    sessions = session.store.sessions
    sessions.create(session_id, state)

    campaigns = session.store.campaigns
    campaigns.create_campaign(
        Campaign(
            campaign_id=hop.campaign_id,
            dataset_name=dataset_name,
            label=label,
            created_at=now,
            root_cycle_id=root_cycle,
            root_content_hash=target_hash,
            optimizer_prompt_hash=optimizer_hash,
            backend_id=session.backend_id,
            owner_user_id=str(session.identity.user_id),
            lifecycle_status="active",
            lifecycle_changed_at=now,
            config=freeze_campaign_config(campaign_config),
        )
    )

    root_hop = CycleHop(campaign_id=hop.campaign_id, cycle_id=root_cycle)
    campaigns.create(
        root_hop,
        {
            "parent_session_id": session_id,
            "header": _build_index_header(session, dataset_size),
        },
    )

    session.session_id = session_id
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = root_cycle

    save_active_pointer(session.store.base_dir, session_id, root_hop)

    # Pre-seed dashboard.json so the webapp doesn't 404 in the mint→loop-start window.
    from promptpotter.application.run_observers import build_campaign_emitter
    from promptpotter.shared.errors import graceful

    with graceful("Pre-seeding dashboard.json failed"):
        build_campaign_emitter(session, campaign_config, origin_accuracy=origin_acc)

    logger.info(
        "Minted fresh campaign %s — session %s, cycle %s",
        hop.campaign_id,
        session_id,
        root_cycle,
    )
    return session_id, hop.campaign_id, root_cycle


def mint_checkin_skeleton(stores: Stores, *, slug: str) -> tuple[str, str, str]:
    """Mint a disk-backed campaign in the ``checkin`` lifecycle. It does NOT claim the active pointer — a
    not-yet-run check-in following it snaps a watching workspace out of the authoring flow."""
    from promptpotter.application.runner.campaign_ids import mint_campaign_id, mint_checkin_cycle_id
    from promptpotter.config.settings import APP_VERSION
    from promptpotter.domain.campaign import Campaign

    now = utcnow_iso()
    campaign_id = mint_campaign_id(slug)
    cycle_id = mint_checkin_cycle_id()
    session_id = mint_session_id()

    stores.campaigns.create_campaign(
        Campaign(
            campaign_id=campaign_id,
            dataset_name=slug,
            created_at=now,
            root_cycle_id=cycle_id,
            root_content_hash="",
            backend_id="",
            owner_user_id=str(stores.identity.user_id),
            lifecycle_changed_at=now,
            config={},
        )
    )
    stores.campaigns.create(
        CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
        {
            "parent_session_id": session_id,
            "header": {
                "tool": "promptpotter",
                "version": APP_VERSION,
                "dataset_name": slug,
                "backend_id": "",
            },
        },
    )
    # Placeholder session row so re-open + the sidebar's session count resolve a real
    # session between skeleton and Start; finalize overwrites it with the run state.
    stores.sessions.create(session_id, {"dataset_name": slug})

    checkin_flag = CycleLayout(
        stores.campaigns.cycle_dir(CycleHop(campaign_id=campaign_id, cycle_id=cycle_id))
    ).checkin_flag
    checkin_flag.parent.mkdir(parents=True, exist_ok=True)
    checkin_flag.write_text("", encoding="utf-8")

    logger.info(
        "Minted check-in campaign %s — session %s, cycle %s", campaign_id, session_id, cycle_id
    )
    return session_id, campaign_id, cycle_id


def finalize_checkin_to_active(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    hop: CycleHop,
    session_id: str,
    cycle_plan: CyclePlan,
    dataset_size: int,
) -> None:
    """Flip a ``checkin`` campaign to ``active`` against its EXISTING ids — the cycle id stays the provisional
    ``cycle_chk_*``, since drift reads ``root_content_hash`` and not the parsed id. This mints nothing new."""
    from promptpotter.application.campaign_config import freeze_campaign_config
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        combined_optimizer_prompt_hash,
    )

    target_hash = cycle_plan.cycle_id.removeprefix("cycle_")
    plan_origin_fields = cycle_plan.origin.prompt_field_dict()

    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "dataset_name": session.dataset_name,
        },
        pipeline_params=cycle_plan.pipeline_params,
        active_steps=list(cycle_plan.pipeline_params.get("steps", [])),
    )
    state["dataset_count"] = dataset_size
    state["origin_prompt_fields"] = plan_origin_fields
    session.store.sessions.create(session_id, state)

    session.store.campaigns.update_campaign(
        hop.campaign_id,
        {
            "root_content_hash": target_hash,
            "optimizer_prompt_hash": combined_optimizer_prompt_hash(),
            "backend_id": session.backend_id,
            "config": freeze_campaign_config(campaign_config),
        },
    )
    session.store.campaigns.create(
        hop,
        {
            "parent_session_id": session_id,
            "header": _build_index_header(session, dataset_size),
        },
    )

    session.session_id = session_id
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id

    # Claim the active pointer now (not at skeleton mint) — Start is when the cycle
    # becomes the running one the dashboard follows. A following workspace snaps to
    # it here, so the operator lands on the live run instead of being bounced
    # mid-authoring (the skeleton deliberately left the pointer alone).
    # The store's OWN workspace, so a sandboxed inner cycle (L4) stamps its own
    # pointer and never the outer tenant's — otherwise the inner mint clobbers the
    # outer's active_session.json and the webapp (which reads the default root)
    # follows a pointer to a campaign that lives under `.inner/…` and 404s.
    save_active_pointer(session.store.base_dir, session_id, hop)

    cycle_dir = session.store.campaigns.cycle_dir(hop)
    CycleLayout(cycle_dir).checkin_flag.unlink(missing_ok=True)

    from promptpotter.application.run_observers import build_campaign_emitter
    from promptpotter.shared.errors import graceful

    with graceful("Pre-seeding dashboard.json failed"):
        build_campaign_emitter(session, campaign_config, origin_accuracy=0.0)

    logger.info(
        "Check-in campaign %s started — session %s, cycle %s",
        hop.campaign_id,
        session_id,
        hop.cycle_id,
    )


def open_cycle_ledger(session: Session, cycle_id: str) -> CycleEventLog | None:
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import CycleEventLog

    if session.store is None:
        return None
    cycle_dir = CycleDir(
        session.store.campaigns.cycle_dir(
            CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id)
        )
    )
    return CycleEventLog.open(cycle_dir)


__all__ = [
    "ScorerSetup",
    "Session",
    "auto_mint_session",
    "finalize_checkin_to_active",
    "mint_checkin_skeleton",
]
