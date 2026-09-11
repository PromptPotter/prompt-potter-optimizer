"""Re-score one campaign candidate on N ADDITIONAL samples. Not a cycle or a fork: no round
id, and the verdict lands in the workspace ``diagnostics/`` tree. Its SPEND is the exception and
joins the campaign's ledger in the ``diagnostic`` bucket — inside every ceiling, banked apart."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.campaign_config import (
    load_campaign_config as validate_campaign_config,
)
from promptpotter.application.initialization.loop_start import arm_diagnostic_scoring
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.optimization.l1.population import merge_pipeline_params
from promptpotter.application.runner.termination import BudgetGate
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import (
    DiagnosticRunRecord,
    parse_candidate_label,
    resolved_fitness,
)
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    diagnostic_spend,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.store import archive_queries
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError

if TYPE_CHECKING:
    from promptpotter.domain.sample import Measurement
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

__all__ = [
    "VerifyError",
    "derive_verify_samples",
    "rounds_since_verified",
    "verify_candidate",
    "verify_on_saturation",
]


# A verify is a TEMPORARY LIFT of the per-candidate round budget, and the lift is how long the
# cycle has gone unchecked. The base keeps the bill on the scale the operator already set; the cap
# is what makes firing one automatically safe, so a 200-round campaign asks for five rounds' worth
# of cells and not 200. Five, because past a handful of rounds' cells the binding constraint stops
# being the noise and starts being the wallet.
_VERIFY_LIFT_CAP = 5


def derive_verify_samples(
    *, round_cell_budget: int, rounds_unverified: int, unmeasured: int
) -> int:
    """How many NEW cells one verify buys. See ``_VERIFY_LIFT_CAP`` above for the model."""
    lift = min(max(rounds_unverified, 1), _VERIFY_LIFT_CAP)
    return max(0, min(round_cell_budget * lift, unmeasured))


def rounds_since_verified(
    records: Sequence[DiagnosticRunRecord], *, cycle_id: str, round_num: int
) -> int:
    """Rounds this CYCLE has run since any of its candidates was last verified; never verified
    reads as ``round_num``. Scoped to the cycle because a fork inherits its parent's measurements
    but not its assurance — the branch is a different search from the point it left."""
    verified_at = [
        parse_candidate_label(r.source_label)[0]
        for r in records
        if r.source_cycle == cycle_id and r.source_label
    ]
    return round_num - max(verified_at) if verified_at else round_num


@contextmanager
def _diagnostic_trace(stores: Stores, hop: CycleHop) -> Iterator[None]:
    """A verify's spend joins the campaign's OWN trace, in the ``diagnostic`` bucket.

    Inside every ceiling, always: the bucket is folded into ``SpendRollup``'s totals like any other
    (``TOKEN_KIND_BUCKET``), so the budget gate sees this money. It is banked APART because it
    answers a question about the search rather than advancing it — folded into ``backend``, an
    operator reads re-measuring a candidate as the cost of finding one.

    The ledger is opened only when none is bound. In the loop and behind the API one already is
    (the round's, and the dispatcher's), and a second handle on one file is a second appender."""
    if active_cycle_ledger() is not None:
        with diagnostic_spend():
            yield
        return
    token = set_cycle_ledger(CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop))))
    try:
        with diagnostic_spend():
            yield
    finally:
        reset_cycle_ledger(token)


class VerifyError(ConflictError):
    """A resolved-state failure: campaign, round or candidate missing on disk. The CLI shell maps it to a clean exit — this
    module never raises ``SystemExit`` itself."""


@dataclass(frozen=True)
class VerifyOutcome:
    """``record`` is ``None`` on the "every sample already measured" path. Both verdict strings are formatted by the CLI
    shell from this outcome, never here."""

    dataset_name: str
    already_measured: int
    record: DiagnosticRunRecord | None = None
    cache_replays: int = 0


def _archive_measurement_to_qm(m: Measurement) -> QueryMeasurement:
    return cast(
        "QueryMeasurement",
        {
            "sample_id": m.sample_id,
            "query": m.query,
            "ground_truth": m.ground_truth,
            "predicted": m.predicted,
            "fitness": m.fitness,
            "error": None,
            "pipeline_data": m.pipeline_data,
        },
    )


async def verify_candidate(
    *,
    stores: Stores,
    identity: IdentityContext,
    hop: CycleHop,
    round_num: int,
    cand_idx: int,
    label: str,
    samples: int | None,
    seed: int | None,
    log: Callable[[str], None] | None = None,
) -> VerifyOutcome:
    """Re-score one candidate on *samples* UNMEASURED samples. Raises :class:`VerifyError` when it cannot be resolved off
    disk."""

    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise VerifyError(f"campaign {hop.campaign_id!r} has no manifest on disk.")

    if round_num == 0:
        raise VerifyError(
            "verifying C0 (origin) is not implemented yet — "
            "the origin's prompt fields don't live in the round-candidate cache. "
            "Pass a C{round}.{n} label instead."
        )
    _cached = stores.campaigns.load_round_candidates(hop, round_num)
    proposals = _cached[0] if _cached else None
    if not proposals:
        raise VerifyError(
            f"no cached candidates for round {round_num} in "
            f"{hop.campaign_id}/{hop.cycle_id} — looked under .runtime/cache/candidates/."
        )
    if cand_idx >= len(proposals):
        raise VerifyError(
            f"round {round_num} only has {len(proposals)} candidates; "
            f"{label!r} requested index {cand_idx + 1}."
        )
    proposal = proposals[cand_idx]
    opt_sp = OptSearchPoint.model_validate(proposal["opt_sp"])
    pipeline_overlay = proposal.get("pipeline_overlay") or {}

    round_file = stores.campaigns.load_round_file(hop, round_num)
    if round_file is None:
        raise VerifyError(
            f"round_{round_num:04d}.json missing in {hop.campaign_id}/{hop.cycle_id}."
        )
    cand_scores = round_file.candidate_scores
    cand_score = next((c for c in cand_scores if c.label == label), None)
    if cand_score is None:
        raise VerifyError(
            f"round_{round_num:04d}.json carries labels "
            f"{[c.label for c in cand_scores]} — none match {label!r}."
        )
    source_campaign_accuracy = cand_score.accuracy
    source_campaign_composite = cand_score.composite_fitness
    source_campaign_n = cand_score.scored_samples
    source_candidate_id = cand_score.candidate_id

    session = await init_services(
        backend_id=campaign.backend_id,
        dataset_name=campaign.dataset_name,
        identity=identity,
        stores=stores,
    )
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id

    campaign_config = validate_campaign_config(campaign.config)
    log_fn = log or (lambda *_a, **_k: None)
    pipeline_params = arm_diagnostic_scoring(
        session, campaign_config, source=f"verify:{hop.campaign_id}:{label}", log=log_fn
    )

    schema = session.pipeline_schema
    effective_pipeline_params = (
        merge_pipeline_params(pipeline_params, pipeline_overlay, schema) or {}
    )
    jsp = opt_sp.to_job_search_point(effective_pipeline_params, schema=schema)
    node_configs = schema.node_configs(effective_pipeline_params)
    predicate: dict[str, dict[str, Any]] = dict(node_configs)
    config_hash = schema.sp_hash(effective_pipeline_params)

    # Find samples this exact config has not yet been measured on.
    prior = archive_queries.measurements_for_config(
        stores,
        predicate=predicate,
        dataset_name=campaign.dataset_name,
    )
    measured_ids = {m.sample_id for m in prior}
    unmeasured = [s for s in session.samples if s.id not in measured_ids]
    if not unmeasured:
        return VerifyOutcome(dataset_name=campaign.dataset_name, already_measured=len(measured_ids))

    budget = derive_verify_samples(
        round_cell_budget=campaign_config.sp_budget_round,
        rounds_unverified=rounds_since_verified(
            stores.diagnostic_runs.list(campaign.dataset_name),
            cycle_id=hop.cycle_id,
            round_num=round_num,
        ),
        unmeasured=len(unmeasured),
    )
    if samples is not None and samples > budget:
        raise VerifyError(
            f"--samples {samples} is above this candidate's verify budget of {budget} "
            f"({campaign_config.sp_budget_round} cells per candidate per round, lifted by the "
            f"rounds run since the last verification, capped at {len(unmeasured)} unmeasured). "
            f"Pass {budget} or fewer, or verify again after more rounds."
        )
    rng = random.Random(seed)
    n_to_pick = budget if samples is None else min(samples, len(unmeasured))
    picked = rng.sample(unmeasured, n_to_pick)

    logger.info(
        "verify %s (%s/%s): scoring %d new sample(s); %d already in archive for this config",
        label,
        hop.campaign_id,
        hop.cycle_id,
        n_to_pick,
        len(measured_ids),
    )
    with _diagnostic_trace(stores, hop):
        await score_search_point(
            jsp,
            picked,
            session,
            label="verify",
            # `verify` replays a RECORDED config against fresh samples; the optimizer state that
            # produced it is not in scope here, and the workspace side it is compared against
            # (`compute_composite_fitness` below) has none either.
            opt_sp=None,
            measured=None,
            on_sample_scored=lambda *_a, **_k: None,
            on_sample_starting=lambda *_a, **_k: None,
            source=f"verify:{hop.campaign_id}:{label}",
        )

    # Workspace aggregate: archive rows matching this candidate's node-configs, deduped per sample (latest wins).
    workspace_measurements = archive_queries.measurements_for_config(
        stores,
        predicate=predicate,
        dataset_name=campaign.dataset_name,
    )
    by_sample: dict[int, QueryMeasurement] = {}
    for m in workspace_measurements:
        by_sample[m.sample_id] = _archive_measurement_to_qm(m)
    workspace_qms = list(by_sample.values())

    if session.scoring.scorer is not None:
        rescore_results(cast("list[dict[str, Any]]", workspace_qms), session.scoring.scorer)
    workspace_scores = compute_composite_fitness(
        workspace_qms,
        schema,
        # The source campaign's side of this comparison has no opt_sp either (see the
        # `score_search_point` call above) — a lift read across two different bases is not
        # a lift.
        opt_sp=None,
    )

    workspace_n = len(workspace_qms)
    # Same rule as the source side above: `compute_accuracy` returns None on no scoreable rows,
    # and a 0.0 here reads as "the fresh re-score collapsed", the exact verdict `verify` reports.
    if workspace_scores.get("accuracy") is None:
        raise VerifyError(
            f"the fresh re-score of {label!r} produced no scoreable sample — there is nothing "
            "to compare against the recorded measurement."
        )
    workspace_accuracy = float(workspace_scores["accuracy"])
    workspace_composite = resolved_fitness(
        workspace_scores.get("composite_fitness"), workspace_accuracy
    )
    samples_added = max(0, workspace_n - len(measured_ids))

    record = DiagnosticRunRecord(
        ts=utcnow_iso(),
        dataset=campaign.dataset_name,
        source_campaign=hop.campaign_id,
        source_cycle=hop.cycle_id,
        source_label=label,
        source_candidate_id=source_candidate_id,
        config_hash=config_hash[:12],
        samples_requested=budget if samples is None else samples,
        samples_added=samples_added,
        workspace_n=workspace_n,
        workspace_accuracy=workspace_accuracy,
        workspace_composite=workspace_composite,
        source_campaign_accuracy=source_campaign_accuracy,
        source_campaign_composite=source_campaign_composite,
        source_campaign_n=source_campaign_n,
    )
    sidecar_path = stores.diagnostic_runs.save(record)
    logger.info("verify: wrote diagnostic-run record → %s", sidecar_path)

    cache_replays = max(0, workspace_n - samples_added - len(measured_ids))
    return VerifyOutcome(
        dataset_name=campaign.dataset_name,
        already_measured=len(measured_ids),
        record=record,
        cache_replays=cache_replays,
    )


async def verify_on_saturation(
    *,
    stores: Stores,
    identity: IdentityContext,
    hop: CycleHop,
    round_num: int,
    accuracy: float | None,
    winner_label: str | None,
    budget: BudgetGate,
    log: Callable[[str], None] | None = None,
) -> VerifyOutcome | None:
    """A round that reads 100% gets checked, automatically, on cells it has never seen — the one
    question a round's own panel cannot answer, and until now one only an operator at a terminal
    could ask.

    ``derive_verify_samples`` bounds the cells; the ``_VERIFY_LIFT_CAP`` gate below bounds the
    CADENCE, so a cycle sitting at 100% for twenty rounds pays for four checks and not twenty. The
    gate reads records that PERSIST, which is also what makes a resumed round decline to repeat its
    own earlier check. Never fatal: a ``VerifyError`` means the candidate would not resolve off disk
    (C0, or a pruned candidate cache), which is a reason to say nothing, not to end a healthy run.

    ``budget`` is the SAME ceiling the round loop halts on, and it is required rather than optional
    because this is the loop spending, not an operator: a discretionary check that could start on an
    exhausted budget would make ``max_usd`` mean whatever the checks happened to cost. The loop's own
    ceiling is consulted at the next round boundary, which is AFTER this runs.
    """
    if accuracy is None or accuracy < 1.0 or not winner_label:
        return None
    if budget.tripped() is not None:
        return None
    mine = [r for r in stores.diagnostic_runs.list() if r.source_cycle == hop.cycle_id]
    since = rounds_since_verified(mine, cycle_id=hop.cycle_id, round_num=round_num)
    if mine and since < _VERIFY_LIFT_CAP:
        return None
    say = log or (lambda *_a, **_k: None)
    try:
        cand_round, cand_idx = parse_candidate_label(winner_label)
        return await verify_candidate(
            stores=stores,
            identity=identity,
            hop=hop,
            round_num=cand_round,
            cand_idx=cand_idx,
            label=winner_label,
            samples=None,
            seed=None,
            log=log,
        )
    except (VerifyError, ValueError) as exc:
        say(f"verify skipped for {winner_label}: {exc}")
        return None
