"""Cross-cycle hard-sample artifact — archive-sourced peer of :mod:`hard_sample_sorter`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import Observation, graded_response
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact_from_observations,
)
from promptpotter.domain.measurement_provenance import entry_grade, meets_grade
from promptpotter.infrastructure.store import archive_views
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores

__all__ = [
    "build_archive_hard_samples_artifact",
    "build_archive_observations",
]

CANDIDATE_HASH_LEN = 12

# The one provenance grade every δ fit is built from — deliberate, full-LLM-path
# measurements, not connector noise. The ruler PoBB kills candidates with and the
# heatmap the operator reads must be the same scale, so this is not a knob.
_RULER_GRADE = "A"


def build_archive_observations(
    stores: Stores,
    *,
    dataset_name: str | None,
) -> list[Observation]:
    """Walk the measurement store → ``Observation(content_hash[:12], sample_id, response)`` triples.

    ``dataset_name=None`` is admin/forensic only — prevents cross-dataset ``sample_id`` pollution.

    Grade A only, always. This used to be an optional ``min_grade``: the δ-ruler passed
    ``"A"`` while the two display callers omitted it and got A+B+C, so one dataset carried
    TWO difficulty scales — the grade-A δ that decides which candidates PoBB kills, and an
    A+B+C δ the operator reads off the heatmap. One substrate, by construction.
    """
    obs: list[Observation] = []
    for entry in archive_views.list_runs(stores, dataset_name=dataset_name):
        if not meets_grade(entry_grade(entry), _RULER_GRADE):
            continue
        content_hash = (entry.get("content_hash") or "").strip()
        if not content_hash:
            continue
        candidate_id = content_hash[:CANDIDATE_HASH_LEN]
        run_id = entry.get("run_id")
        if not run_id:
            continue
        detail = archive_views.load_run(stores, run_id)
        if detail is None:
            continue
        for item in detail.get("measurements", []):
            sid = item.get("sample_id")
            if sid is None:
                continue
            if is_error_result(item):
                continue
            obs.append(
                Observation(
                    candidate_id=candidate_id,
                    sample_id=int(sid),
                    response=graded_response(item),
                )
            )
    return obs


def build_archive_hard_samples_artifact(
    stores: Stores,
    *,
    dataset_name: str | None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
) -> dict[str, Any]:
    """Per-dataset hard-samples artifact fit on every archive measurement (``cycle_id=None``)."""
    return build_hard_samples_artifact_from_observations(
        build_archive_observations(
            stores,
            dataset_name=dataset_name,
        ),
        cycle_id=None,
        top_k_candidates=top_k_candidates,
        top_k_samples=top_k_samples,
    )
