"""Dataset scoring gateway — ``score_search_point``: cache resolution, archival, observability.
The sole scoring ingress (§0.5)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.datasets.loaders import build_dataset_run_data
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.query_loop import run_query_loop
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.scoring import CellScorer, QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.store import archive_views
from promptpotter.shared.errors import DatasetIdentityError, error_category, is_error_result
from promptpotter.shared.instrument import MeasuredCandidate, measured_candidate_scope

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.application.scoring.query_loop import QueryLoopResult
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)

__all__ = ["merge_with_unprocessed_priors", "rescored_prior_tail", "score_search_point"]


def rescored_prior_tail(
    *,
    cached_sample_results: dict[int, QueryMeasurement],
    dataset_sample_ids: set[int],
    deprecated_samples: dict[int, QueryMeasurement],
    scorer: CellScorer | None,
) -> dict[int, QueryMeasurement]:
    """The cache priors this run may archive without re-measuring, rescored ONCE. The active scorer
    is fixed for one call, so rescoring per prior per sample was O(samples²) for the same answers."""
    tail: dict[int, QueryMeasurement] = {}
    for sid, prior in cached_sample_results.items():
        if sid not in dataset_sample_ids or sid in deprecated_samples:
            continue
        entry = cast(QueryMeasurement, dict(prior))
        if scorer is not None:
            rescore_results([cast("dict[str, Any]", entry)], scorer)
        tail[sid] = entry
    return tail


def merge_with_unprocessed_priors(
    results: list[QueryMeasurement],
    prior_tail: dict[int, QueryMeasurement],
) -> list[QueryMeasurement]:
    """Union results with the priors for samples the walk has not reached, or a partial run (cache
    hits + Ctrl+C) shrinks the archive's record. The run's derived fields read this merged view."""
    if not prior_tail:
        return results
    processed = {r["sample_id"] for r in results}
    return results + [p for sid, p in prior_tail.items() if sid not in processed]


def _build_scoring_error_signal(
    *,
    results: list[QueryMeasurement],
    stop_reason: str,
    candidate_idx: int,
    n_total_candidates: int,
) -> EscalationSignal:
    # Skip rows whose error is the abort-reason padding — those are synthetic
    # markers inserted after the cascade to bring results up to dataset length,
    # not the real backend failure that triggered it.
    real_errors = [r for r in results if is_error_result(r) and str(r["error"]) != stop_reason]
    warning_types: dict[str, int] = {}
    for r in real_errors:
        key = str(error_category(r) or "unknown")
        warning_types[key] = warning_types.get(key, 0) + 1
    # Every ``real_error`` is an error row, so ``error`` is present + non-empty.
    last_error = str(real_errors[-1]["error"]) if real_errors else ""
    dominant = last_error or stop_reason or "scoring_error"
    return EscalationSignal(
        check_name="scoring_error_abort",
        target=EscalationTarget.ELIMINATE_CANDIDATE,
        check_result={
            "stop_reason": stop_reason,
            "dominant_warning": dominant,
            "warning_types": warning_types,
            "degraded_count": len(real_errors),
            "total_scored": len(results),
            "last_error": last_error,
        },
        candidate_idx=candidate_idx,
        candidates_scored=candidate_idx + 1,
        candidates_skipped=n_total_candidates - candidate_idx - 1,
    )


def _split_off_deprecated_samples(
    cached_sample_results: dict[int, QueryMeasurement],
) -> tuple[dict[int, QueryMeasurement], dict[int, QueryMeasurement]]:
    """Load-side cache split: (kept, deprecated rows that need fresh re-measure)."""
    from promptpotter.application.optimization.pobb.classification import is_deprecated

    deprecated = {sid: r for sid, r in cached_sample_results.items() if is_deprecated(r)}
    kept = {sid: r for sid, r in cached_sample_results.items() if sid not in deprecated}
    return kept, deprecated


def _assert_measured_content_matches(
    cached: dict[int, QueryMeasurement],
    dataset: list[Sample],
    dataset_name: str,
) -> None:
    """The one gate on positional sample identity. Every stored row carries the content it was
    measured against, so this needs nothing on disk that is not already there — and it catches a
    single edited row, which a whole-dataset fingerprint would only catch in aggregate."""
    for sample in dataset:
        prior = cached.get(sample.id)
        if prior is None:
            continue
        stored = (prior.get("query", ""), prior.get("ground_truth", ""))
        if stored != (sample.query, sample.ground_truth):
            raise DatasetIdentityError(
                dataset_name=dataset_name,
                sample_id=sample.id,
                stored=stored,
                current=(sample.query, sample.ground_truth),
            )


def _resolve_prior_cache(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    pipeline_schema: PipelineSchema,
    force_fresh: bool,
    label: str,
) -> tuple[dict[int, QueryMeasurement], dict[int, QueryMeasurement], set[int]]:
    """Load-side cache resolution — reusable-archive lookup, deprecated-row split, preamble log.
    ``force_fresh`` skips reuse, and so does a session with no dataset: ``sample_id`` needs one."""
    store = session.store
    backend_id = session.backend_id
    dataset_name = session.dataset_name
    cached_sample_results: dict[int, QueryMeasurement] = {}
    if store and backend_id and dataset_name and not force_fresh:
        from promptpotter.application.optimization.pobb.classification import (
            is_deprecated,
        )

        node_configs = pipeline_schema.node_configs(search_point.pipeline_params)
        cached_sample_results = cast(
            "dict[int, QueryMeasurement]",
            archive_views.reusable_results(
                store,
                node_configs,
                is_fatal=is_deprecated,
                dataset_name=dataset_name,
            ),
        )

    if cached_sample_results and dataset_name:
        _assert_measured_content_matches(cached_sample_results, dataset, dataset_name)

    cached_sample_results, deprecated_samples = _split_off_deprecated_samples(cached_sample_results)
    if deprecated_samples:
        logger.info(
            "Evicted %d deprecated prior result(s) (fatal warnings); will remeasure.",
            len(deprecated_samples),
        )

    dataset_sample_ids = {s.id for s in dataset}
    # Preamble: when the JSP-keyed archive already covers some/all of this dataset's
    # samples, announce the split so the operator sees inline whether the upcoming
    # per-sample lines are cache replays vs fresh. Suppressed when no priors match.
    cached_in_dataset = sum(
        1
        for sid in cached_sample_results
        if sid in dataset_sample_ids and sid not in deprecated_samples
    )
    if cached_in_dataset:
        total = len(dataset)
        logger.debug(
            "%s cache: %d/%d already measured for this JSP — will replay %d, measure %d fresh.",
            label,
            cached_in_dataset,
            total,
            cached_in_dataset,
            total - cached_in_dataset,
        )
    return cached_sample_results, deprecated_samples, dataset_sample_ids


def _resolve_partial_escalation(
    batch: QueryLoopResult,
    *,
    candidate_idx: int,
    n_total_candidates: int,
) -> EscalationSignal | None:
    """Resolve the escalation signal for a non-completed, non-escalated batch. Raises
    ``KeyboardInterrupt`` to unwind a stop — it must propagate uncaught. Branch order is load-bearing."""
    escalation_signal = batch.escalation_signal
    if not batch.completed and not escalation_signal:
        if batch.stop_reason == "skip":
            # Operator early-abort of THIS searchpoint. The partial is already on
            # disk; accept it as a normal partial score (escalation_signal stays
            # None) and fall through to compute fitness over what was scored —
            # identical in shape to a PoBB early-abort. The round continues to the
            # next candidate; the cycle is NOT unwound.
            pass
        elif batch.stop_reason in {"graceful", "force"}:
            # Graceful/force stop — partial state is already on disk via
            # per-fresh-sample persist; just unwind. The instance carries which of the two.
            raise KeyboardInterrupt(batch.stop_reason)
        else:
            # Per-candidate scoring-error abort (consecutive 5xx, client 4xx,
            # pipeline ERROR). Synthesize a candidate-scoped escalation so the
            # caller can attach a RuntimeFailure and continue with the next
            # candidate — never kill the round.
            return _build_scoring_error_signal(
                results=batch.results,
                stop_reason=batch.stop_reason or "",
                candidate_idx=candidate_idx,
                n_total_candidates=n_total_candidates,
            )
    return escalation_signal


def _emit_dataset_run(
    session: Session,
    *,
    run_id: str,
    content_hash: str,
    search_point: JobSearchPoint,
    pipeline_schema: PipelineSchema,
    scores: dict[str, Any],
) -> None:
    """Emit the ``DatasetRun`` observability trace for a completed score (best-effort)."""
    store = session.store
    backend_id = session.backend_id
    if not (store and backend_id):
        return
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge
    from promptpotter.infrastructure.tracing.events import DatasetRun
    from promptpotter.shared.errors import graceful

    with graceful("DatasetRun emit failed"):
        obs = session.state.obs or ObservabilityBridge.file_only(store.base_dir)
        obs.emit(
            DatasetRun(
                campaign_id="",
                round_num=-1,
                run_id=run_id,
                content_hash=content_hash,
                prompt_fields_id=search_point.sp_hash(pipeline_schema),
                accuracy=scores["accuracy"],
                total=scores["total"],
            )
        )


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    label: str = "Score",
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None,
    on_sample_starting: Callable[[str, int, int, int, int], None] | None,
    source: str = "",
    degradation_checks: list[StopRule] | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
    axes: AxisIndex | None = None,
    l1_diversity: float = 1.0,
    opt_sp: OptSearchPoint | None,
    measured: MeasuredCandidate | None,
    on_sample_pre_check: Callable[[Sample], Awaitable[None]] | None = None,
    force_fresh: bool = False,
) -> tuple[list[QueryMeasurement], dict[str, Any], EscalationSignal | None]:
    """``opt_sp``, ``measured`` and the two per-sample callbacks are required keywords with NO
    default — each decides what the numbers MEAN, and the signature is the only enforcement."""
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = session.pipeline_schema
    source = source or session.source

    content_hash = search_point.content_hash(dataset)
    safe_label = label.lower().replace(" ", "_")
    # The FULL hash, not a second cut of it. `label` is a round-local position (`c1.1`, `origin`)
    # that every campaign re-mints, so the hash is the only thing telling two runs apart — and at
    # 8 hex it was 32 bits carrying that alone. A collision does not raise: `append_run` folds
    # last-wins per `m:{sample_id}`, so both searchpoints' measurements merge into one run file and
    # the archive reports a configuration that did not produce them.
    run_id = f"{safe_label}_{content_hash}"

    cached_sample_results, deprecated_samples, dataset_sample_ids = _resolve_prior_cache(
        search_point,
        dataset,
        session,
        pipeline_schema=pipeline_schema,
        force_fresh=force_fresh,
        label=label,
    )

    prior_tail = rescored_prior_tail(
        cached_sample_results=cached_sample_results,
        dataset_sample_ids=dataset_sample_ids,
        deprecated_samples=deprecated_samples,
        scorer=session.scoring.scorer,
    )
    if force_fresh and store:
        # An append-only log does not overwrite: force_fresh means REPLACE these rows.
        archive_views.reset_measurement_run(store, run_id)
    elif store:
        # Drop a previous walk's dead header rows before appending more (a no-op on a log
        # that closed cleanly — the end-of-walk compaction already tightened it).
        archive_views.compact_measurement_run(store, run_id)

    # How many of ``results`` are already appended to the run's log, and whether the priors
    # have been. The log is append-only, so each save writes only what is new.
    appended = 0
    priors_appended = not prior_tail

    def _composite(rows: list[QueryMeasurement]) -> dict[str, Any]:
        """This candidate's fitness over *rows*. Also the loop's `running_scores`, so the number a
        live surface shows converging is the one the round banks — never a second fold."""
        return compute_composite_fitness(
            rows,
            pipeline_schema,
            opt_sp=opt_sp,
            l1_diversity=l1_diversity,
        )

    def _save_run(results: list[QueryMeasurement], scores: dict[str, Any]) -> None:
        nonlocal appended, priors_appended
        if not (store and backend_id):
            return
        merged = merge_with_unprocessed_priors(results, prior_tail)
        run_data = build_dataset_run_data(
            run_id,
            safe_label,
            content_hash,
            search_point,
            scores,
            merged,
            dataset_name=session.dataset_name,
            source=source,
            pipeline_schema=pipeline_schema,
            human_intervened=session.human_intervened,
        )
        # The cursor is over ``results``, not "the last row": a cache hit appends a
        # MATERIALIZED row (rescored, recovered — which can cost real backend calls) without
        # persisting, so a save has to sweep up everything the walk has produced since the
        # last one. The priors go down once — ``merged[len(results):]`` is exactly the ones
        # the walk has not reached; a sample walked later supersedes its own prior by
        # ``sample_id``, which is what makes an append-only log safe to re-walk.
        new_rows: list[QueryMeasurement] = []
        if not priors_appended:
            new_rows.extend(merged[len(results) :])
            priors_appended = True
        new_rows.extend(results[appended:])
        appended = len(results)
        archive_views.record_measurement_run(
            store, run_id, run_data, cast("list[dict[str, Any]]", new_rows)
        )

    def _persist_fresh(results: list[QueryMeasurement]) -> dict[str, Any]:
        """Persist the walk's new rows; return the candidate's running fitness. The ARCHIVED score is
        the merged fold, the running one is over ``results`` alone — the candidate's own, what PoBB reads."""
        running = _composite(results)
        if not (store and backend_id):
            return running
        merged = merge_with_unprocessed_priors(results, prior_tail)
        _save_run(results, running if merged is results else _composite(merged))
        return running

    # Bound over the walk alone: it is the only part reaching the backend seam, the seam is the
    # only reader, and the scope bounds `on_sample_pre_check`'s re-entry into this same function.
    with measured_candidate_scope(measured):
        batch = await run_query_loop(
            search_point,
            dataset,
            session,
            cached_sample_results=cached_sample_results,
            deprecated_samples=deprecated_samples,
            on_sample_scored=on_sample_scored,
            on_sample_starting=on_sample_starting,
            degradation_checks=degradation_checks,
            candidate_idx=candidate_idx,
            n_total_candidates=n_total_candidates,
            axes=axes,
            persist_fresh=_persist_fresh,
            running_scores=_composite,
            on_sample_pre_check=on_sample_pre_check,
        )
    results = batch.results
    escalation_signal = _resolve_partial_escalation(
        batch,
        candidate_idx=candidate_idx,
        n_total_candidates=n_total_candidates,
    )

    scores = _composite(results)
    if batch.stop_reason == "skip":
        # Mark the partial as an operator early-abort so the candidate report and
        # measurement record carry the provenance (the cycle is babysat).
        scores["partial_reason"] = "skip"

    _save_run(results, scores)
    if store:
        archive_views.compact_measurement_run(store, run_id)
    _emit_dataset_run(
        session,
        run_id=run_id,
        content_hash=content_hash,
        search_point=search_point,
        pipeline_schema=pipeline_schema,
        scores=scores,
    )

    return results, scores, escalation_signal
