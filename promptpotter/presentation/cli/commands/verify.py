"""``cmd_verify`` — re-score one campaign candidate on more samples.

The operator names a campaign (short or full id) and a candidate label
(``C{round}.{idx}`` or ``C0``); the command rebuilds that candidate's
:class:`JobSearchPoint`, picks ``--samples`` samples the candidate has not
already been measured on, calls :func:`score_search_point` (which auto-
archives the new measurements into the cross-cycle archive), then writes
one :class:`DiagnosticRunRecord` sidecar summarising the workspace-scope
verdict for the Verify tab to read.

This is *not* a cycle / fork / sweep — no ledger event, no round_id, no
mutation of any existing cycle's index. The only persistence is into the
workspace-scoped ``archive/`` tree.
"""

from __future__ import annotations

import argparse
import copy
import logging
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from promptpotter.domain.results import DiagnosticRunRecord
from promptpotter.infrastructure.store import archive_views, build_stores
from promptpotter.presentation.cli.commands._shared import (
    CommandResult,
    get_verbose,
    init_services_cli,
)

if TYPE_CHECKING:
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store import Stores

logger = logging.getLogger("promptpotter.presentation.cli")


def _resolve_campaign(stores: Stores, needle: str) -> str:
    """Resolve *needle* to a campaign id (exact match, then prefix match).

    Accepts the full id (``justlogic__ca6d4d``), the 6-hex suffix
    (``ca6d4d``), or any unambiguous prefix.
    """
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


def _resolve_cycle(stores: Stores, campaign_id: str, hint: str | None) -> str:
    """Resolve a cycle id within *campaign_id*.

    When ``hint`` is None, auto-picks the sole cycle; raises if the campaign
    has multiple cycles (operator must disambiguate with ``--cycle``).
    """
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


def _parse_label(label: str) -> tuple[int, int]:
    """Parse ``C0`` or ``C{round}.{n}`` → ``(round, idx_in_round)``.

    ``C0`` ⇒ ``(0, 0)`` (origin). ``C4.1`` ⇒ ``(4, 0)`` (round 4, first
    candidate — labels are 1-indexed; the on-disk position is 0-indexed).
    """
    if label == "C0":
        return 0, 0
    if not label.startswith("C") or "." not in label:
        raise SystemExit(f"ERROR: bad candidate label {label!r}; expected C0 or C{{round}}.{{n}}.")
    round_part, idx_part = label[1:].split(".", 1)
    try:
        round_num = int(round_part)
        idx_one_based = int(idx_part)
    except ValueError as exc:
        raise SystemExit(f"ERROR: bad candidate label {label!r}: {exc}") from None
    if idx_one_based < 1:
        raise SystemExit(f"ERROR: candidate index in {label!r} must be ≥ 1.")
    return round_num, idx_one_based - 1


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge — *overlay* wins; lists / scalars replaced wholesale."""
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _archive_measurement_to_qm(m: Any) -> QueryMeasurement:
    """Project a :class:`Measurement` archive row into a :class:`QueryMeasurement` dict."""
    return cast(
        "QueryMeasurement",
        {
            "sample_id": m.sample_id,
            "query": m.query,
            "ground_truth": m.ground_truth,
            "predicted": m.predicted,
            "hit": m.hit,
            "fitness": m.fitness,
            "error": None,
            "pipeline_data": m.pipeline_data or {},
        },
    )


async def cmd_verify(args: argparse.Namespace) -> CommandResult:
    """Re-score a campaign candidate on N additional samples; persist the workspace verdict."""
    from promptpotter.application.bootstrap.scoring_context import populate_session_scoring
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.config import (
        load_campaign_config as validate_campaign_config,
    )
    from promptpotter.application.scoring.formula import rescore_results, split_scoring_block
    from promptpotter.application.scoring.metrics import compute_composite_fitness
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.domain.opt_search_point import OptSearchPoint

    stores = build_stores(tenant_id=getattr(args, "tenant", "default"))
    campaign_id = _resolve_campaign(stores, args.campaign)
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise SystemExit(f"ERROR: campaign {campaign_id!r} has no manifest on disk.")
    cycle_id = _resolve_cycle(stores, campaign_id, args.cycle)
    round_num, cand_idx = _parse_label(args.label)

    # Find the candidate's persisted OSP + override + campaign-scope score.
    if round_num == 0:
        raise SystemExit(
            "ERROR: verifying C0 (origin) is not implemented yet — "
            "the origin's prompt fields don't live in the round-candidate cache. "
            "Pass a C{round}.{n} label instead."
        )
    proposals = stores.campaigns.load_round_candidates(campaign_id, cycle_id, round_num)
    if not proposals:
        raise SystemExit(
            f"ERROR: no cached candidates for round {round_num} in "
            f"{campaign_id}/{cycle_id} — looked under .runtime/cache/candidates/."
        )
    if cand_idx >= len(proposals):
        raise SystemExit(
            f"ERROR: round {round_num} only has {len(proposals)} candidates; "
            f"{args.label!r} requested index {cand_idx + 1}."
        )
    proposal = proposals[cand_idx]
    osp = OptSearchPoint.model_validate(proposal["osp"])
    pp_override = proposal.get("pipeline_params_override") or {}

    round_file = stores.campaigns.load_round_file(campaign_id, cycle_id, round_num)
    if round_file is None:
        raise SystemExit(f"ERROR: round_{round_num:04d}.json missing in {campaign_id}/{cycle_id}.")
    cand_scores: list[dict[str, Any]] = round_file.get("candidate_scores") or []
    cand_score = next((c for c in cand_scores if c.get("label") == args.label), None)
    if cand_score is None:
        raise SystemExit(
            f"ERROR: round_{round_num:04d}.json carries labels "
            f"{[c.get('label') for c in cand_scores]} — none match {args.label!r}."
        )
    source_campaign_accuracy = float(cand_score.get("accuracy") or 0.0)
    source_campaign_composite = float(cand_score.get("composite_fitness") or 0.0)
    source_campaign_n = int(cand_score.get("scored_samples") or 0)
    source_candidate_id = str(cand_score.get("candidate_id") or "")

    # Wire a read-only session against the campaign's dataset. ``take_over=False``
    # leaves the operator's active pointer untouched.
    session = await init_services_cli(
        backend_id=campaign.backend_id or campaign.dataset_name,
        dataset_name=campaign.dataset_name,
        take_over=False,
        tenant_id=getattr(args, "tenant", "default"),
    )
    session.campaign_id = campaign_id
    session.state.cycle_id = cycle_id

    campaign_config = validate_campaign_config(campaign.config)
    log_fn = logger.info if get_verbose() else (lambda *_a, **_k: None)
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log_fn)
    scoring_spec = split_scoring_block(campaign_config.scoring)
    populate_session_scoring(
        session,
        obs=None,
        scoring_formula=scoring_spec.per_sample,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        cycle_id=cycle_id,
        source=f"verify:{campaign_id}:{args.label}",
    )

    merged_pp = _deep_merge(pipeline_params, pp_override)
    schema = session.pipeline_schema
    jsp = osp.to_job_search_point(merged_pp, schema=schema)
    if schema is None:
        raise SystemExit("ERROR: pipeline schema unavailable; cannot resolve candidate config.")
    node_configs = schema.node_configs(merged_pp or {})
    predicate: dict[str, dict[str, Any]] = dict(node_configs)
    config_hash = schema.sp_hash(merged_pp or {})

    # Find samples this exact config has not yet been measured on.
    prior = archive_views.measurements_for_config(
        stores,
        session.backend_id,
        predicate=predicate,
        dataset_name=campaign.dataset_name,
    )
    measured_ids = {m.sample_id for m in prior}
    unmeasured = [s for s in session.samples if s.id not in measured_ids]
    if not unmeasured:
        return CommandResult(
            human=(
                f"{args.label}: every sample in the {campaign.dataset_name} bank is "
                f"already measured for this config ({len(measured_ids)} total). "
                "Nothing to add."
            ),
        )

    rng = random.Random(args.seed)
    n_to_pick = min(int(args.samples), len(unmeasured))
    picked = rng.sample(unmeasured, n_to_pick)

    logger.info(
        "verify %s (%s/%s): scoring %d new sample(s); %d already in archive for this config",
        args.label,
        campaign_id,
        cycle_id,
        n_to_pick,
        len(measured_ids),
    )
    await score_search_point(
        jsp,
        picked,
        session,
        label="verify",
        on_sample_scored=lambda *_a, **_k: None,
        on_sample_starting=lambda *_a, **_k: None,
        source=f"verify:{campaign_id}:{args.label}",
    )

    # Workspace aggregate — every archive row whose node-configs match this
    # candidate exactly, deduped to one measurement per sample (latest wins).
    workspace_measurements = archive_views.measurements_for_config(
        stores,
        session.backend_id,
        predicate=predicate,
        dataset_name=campaign.dataset_name,
    )
    by_sample: dict[int, QueryMeasurement] = {}
    for m in workspace_measurements:
        by_sample[m.sample_id] = _archive_measurement_to_qm(m)
    workspace_qms = list(by_sample.values())

    if session.scoring.scorer is not None:
        rescore_results(
            cast("list[dict[str, Any]]", workspace_qms),
            session.scoring.scorer,
            session.scoring.scorer_id,
            session.scoring.scorer_formula,
        )
    workspace_scores = compute_composite_fitness(
        workspace_qms,
        schema,
        round_scorer=session.scoring.round_scorer,
    )

    workspace_n = len(workspace_qms)
    workspace_composite = float(workspace_scores.get("composite_fitness") or 0.0)
    workspace_accuracy = float(workspace_scores.get("accuracy") or 0.0)
    samples_added = max(0, workspace_n - len(measured_ids))

    record = DiagnosticRunRecord(
        ts=datetime.now(UTC).isoformat(),
        dataset=campaign.dataset_name,
        source_campaign=campaign_id,
        source_cycle=cycle_id,
        source_label=args.label,
        source_candidate_id=source_candidate_id,
        config_hash=config_hash[:12],
        samples_requested=int(args.samples),
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

    cache_replays = workspace_n - samples_added - len(measured_ids)
    cache_replays = max(0, cache_replays)
    human = (
        f"{args.label}: acc {source_campaign_accuracy:.3f}→{workspace_accuracy:.3f} "
        f"(cf {source_campaign_composite:.3f}→{workspace_composite:.3f}) "
        f"on {workspace_n} samples (+{samples_added} new from "
        f"{source_campaign_n} in campaign"
        + (f", {cache_replays} cache-replay" if cache_replays else "")
        + ")."
    )
    return CommandResult(data=record.model_dump(), human=human)


__all__ = ["cmd_verify"]
