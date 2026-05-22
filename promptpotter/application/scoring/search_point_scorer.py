"""Dataset scoring gateway — ``score_search_point``: cache resolution, archival, observability.

The sole scoring ingress (§0.5). Resolves the prior-cache split, runs the
per-sample loop (:func:`run_query_loop` in ``query_loop.py``), persists each
fresh measurement to the archive, and emits the ``DatasetRun`` trace.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.datasets import build_dataset_run_data
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.application.scoring.query_loop import run_query_loop
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.scoring import QueryMeasurement, Scorer
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.store import archive_views
from promptpotter.shared.errors import error_category, is_error_result

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint

logger = logging.getLogger(__name__)

__all__ = ["merge_with_unprocessed_priors", "score_search_point"]


def merge_with_unprocessed_priors(
    results: list[QueryMeasurement],
    *,
    cached_sample_results: dict[str, QueryMeasurement],
    dataset_queries: set[str],
    deprecated_samples: dict[str, QueryMeasurement],
    scorer: Scorer | None,
    scorer_id: str,
    scorer_formula: str | None,
) -> list[QueryMeasurement]:
    """Union results with cache priors for unprocessed dataset queries; rescore via active scorer.

    Without this, partial runs (cache hits + Ctrl+C) shrink the archive on
    overwrite. Evicted (deprecated) priors must re-measure, not re-archive.
    """
    processed = {r["query"] for r in results}
    merged = list(results)
    for q, prior in cached_sample_results.items():
        if q not in dataset_queries or q in processed or q in deprecated_samples:
            continue
        entry = cast(QueryMeasurement, dict(prior))
        if scorer is not None:
            rescore_results([cast(dict, entry)], scorer, scorer_id, scorer_formula)
        merged.append(entry)
    return merged


def _build_scoring_error_signal(
    *,
    results: list[QueryMeasurement],
    stop_reason: str,
    candidate_idx: int,
    n_total_candidates: int,
) -> EscalationSignal:
    """Build ELIMINATE_CANDIDATE signal carrying error histogram for RuntimeFailure mint."""
    # Skip rows whose error is the abort-reason padding — those are synthetic
    # markers inserted after the cascade to bring results up to dataset length,
    # not the real backend failure that triggered it.
    real_errors = [
        r for r in results if is_error_result(r) and str(r.get("error") or "") != stop_reason
    ]
    warning_types: dict[str, int] = {}
    for r in real_errors:
        key = str(error_category(r.get("error")) or "unknown")
        warning_types[key] = warning_types.get(key, 0) + 1
    last_error = ""
    for r in reversed(real_errors):
        err = r.get("error")
        if err:
            last_error = str(err)
            break
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
    cached_sample_results: dict[str, QueryMeasurement],
) -> tuple[dict[str, QueryMeasurement], dict[str, QueryMeasurement]]:
    """Load-side cache split: (kept, deprecated rows that need fresh re-measure)."""
    from promptpotter.application.optimization.pobb.elimination import is_deprecated

    deprecated = {q: r for q, r in cached_sample_results.items() if is_deprecated(r)}
    kept = {q: r for q, r in cached_sample_results.items() if q not in deprecated}
    return kept, deprecated


async def score_search_point(
    search_point: JobSearchPoint,
    dataset: list[Sample],
    session: Session,
    *,
    label: str = "Score",
    on_sample_scored: Callable[[QueryMeasurement, int, int], None] | None,
    on_sample_starting: Callable[[str, int, int, int], None] | None,
    source: str = "",
    degradation_checks: list[StopRule] | None = None,
    candidate_idx: int = 0,
    n_total_candidates: int = 1,
    axes: AxisIndex | None = None,
    l1_diversity: float = 1.0,
    next_sample: Callable[[dict[int, bool]], int | None] | None = None,
) -> tuple[list[QueryMeasurement], dict[str, Any], bool, EscalationSignal | None]:
    """Score search point with chain-addressed cache; per-sample persist (Ctrl+C-safe).

    Per-sample callbacks ``on_sample_scored`` and ``on_sample_starting`` are
    **required keywords without a default** — every call site must declare its
    visibility choice (wire a callback, or pass ``None`` with documented
    intent). The class of bug being guarded: a backend running
    ``measure_sample`` for tens of seconds while the CLI stays silent,
    burning LLM credits with the operator unable to tell the front-end
    apart from a frozen process. ``tests/test_invariants.py::
    test_score_search_point_callers_explicit_per_sample_visibility``
    statically enforces the same invariant.
    """
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = session.pipeline_schema
    source = source or session.source
    assert pipeline_schema is not None, "pipeline_schema required for scoring"

    content_hash = search_point.content_hash(dataset)
    safe_label = label.lower().replace(" ", "_")
    run_id = f"{safe_label}_{content_hash[:8]}"

    cached_sample_results: dict[str, QueryMeasurement] = {}
    if store and backend_id:
        from promptpotter.application.optimization.pobb.elimination import is_deprecated

        node_configs = pipeline_schema.node_configs(search_point.pipeline_params or {})
        cached_sample_results = cast(
            "dict[str, QueryMeasurement]",
            archive_views.reusable_results(
                store,
                backend_id,
                node_configs,
                is_fatal=is_deprecated,
                dataset_name=session.dataset_name,
            ),
        )

    cached_sample_results, deprecated_samples = _split_off_deprecated_samples(cached_sample_results)
    if deprecated_samples:
        logger.info(
            "Evicted %d deprecated prior result(s) (fatal warnings); will remeasure.",
            len(deprecated_samples),
        )

    display_name = f"{session.experiment_id}_{safe_label}" if session.experiment_id else safe_label
    dataset_queries = {s.query for s in dataset}

    # Preamble: when the JSP-keyed archive already covers some/all of this
    # dataset's queries, announce the split so the operator sees inline
    # whether the upcoming per-sample lines are cache replays vs fresh
    # measurements. Suppressed when no priors match the current dataset.
    cached_in_dataset = sum(
        1 for q in cached_sample_results if q in dataset_queries and q not in deprecated_samples
    )
    if cached_in_dataset:
        total = len(dataset)
        fresh = total - cached_in_dataset
        logger.debug(
            "%s cache: %d/%d already measured for this JSP — will replay %d, measure %d fresh.",
            label,
            cached_in_dataset,
            total,
            cached_in_dataset,
            fresh,
        )

    def _merged_view(results: list[QueryMeasurement]) -> list[QueryMeasurement]:
        return merge_with_unprocessed_priors(
            results,
            cached_sample_results=cached_sample_results,
            dataset_queries=dataset_queries,
            deprecated_samples=deprecated_samples,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
        )

    def _save_run(results: list[QueryMeasurement], scores: dict[str, Any]) -> None:
        if not (store and backend_id):
            return
        merged = _merged_view(results)
        run_data = build_dataset_run_data(
            run_id,
            display_name,
            content_hash,
            search_point,
            scores,
            merged,
            dataset_name=session.dataset_name,
            source=source,
            experiment_id=session.experiment_id,
            pipeline_schema=pipeline_schema,
        )
        archive_views.record_measurement_run(store, backend_id, run_id, run_data)

    def _persist_fresh(results: list[QueryMeasurement]) -> None:
        if not (store and backend_id):
            return
        merged = _merged_view(results)
        scores = compute_composite_fitness(
            merged,
            pipeline_schema,
            round_scorer=session.scoring.round_scorer,
            l1_diversity=l1_diversity,
        )
        _save_run(results, scores)

    # Pre-register Samples so the SampleIndex carries primitives for any
    # query that lands. ``Sample.run_ids`` accumulates later, when
    # ``AxisIndex.refresh`` ingests this run from the archive.
    if session.scoring.sample_index is not None:
        session.scoring.sample_index.register_many(dataset)

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
        next_sample=next_sample,
    )
    results = batch.results
    escalation_signal = batch.escalation_signal

    if not batch.completed and not escalation_signal:
        # Graceful/force stop — partial state is already on disk via
        # per-fresh-sample persist; just unwind.
        if batch.stop_reason in {"graceful", "force"}:
            raise KeyboardInterrupt()
        # Per-candidate scoring-error abort (consecutive 5xx, client 4xx,
        # pipeline ERROR). Synthesize a candidate-scoped escalation so the
        # caller can attach a RuntimeFailure and continue with the
        # next candidate — never kill the round.
        escalation_signal = _build_scoring_error_signal(
            results=results,
            stop_reason=batch.stop_reason or "",
            candidate_idx=candidate_idx,
            n_total_candidates=n_total_candidates,
        )

    scores = compute_composite_fitness(
        results,
        pipeline_schema,
        round_scorer=session.scoring.round_scorer,
        l1_diversity=l1_diversity,
    )

    _save_run(results, scores)
    if store and backend_id:
        from promptpotter.infrastructure.tracing import DatasetRun, ObservabilityBridge
        from promptpotter.shared.errors import graceful

        with graceful("DatasetRun emit failed"):
            obs = session.state.obs or ObservabilityBridge.file_only(store.base_dir, backend_id)
            obs.emit(
                DatasetRun(
                    campaign_id="",
                    round_num=-1,
                    run_id=run_id,
                    content_hash=content_hash,
                    prompt_fields_id=search_point.sp_hash(pipeline_schema),
                    accuracy=scores["accuracy"],
                    hits=scores["hits"],
                    total=scores["total"],
                )
            )

    return results, scores, False, escalation_signal
