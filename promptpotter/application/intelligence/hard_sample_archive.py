"""Cross-cycle hard-sample artifact — archive-sourced peer of :mod:`hard_sample_sorter`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import (
    ORIGIN_ABILITY_ID,
    Observation,
    dedup_observations,
    graded_response,
)
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

# The one provenance grade every δ fit is built from — deliberate, full-LLM-path
# measurements, not connector noise. The ruler PoBB kills candidates with and the
# heatmap the operator reads must be the same scale, so this is not a knob.
_RULER_GRADE = "A"

# Derived observations per run, revalidated against the detail file's signature.
#
# The round-close writers rebuild the dataset-scope artifact EVERY round, and each rebuild
# re-parsed all 676 grade-A detail files — 90 MB of json.loads, ~1.2 s, for an archive that
# gained one run. Caching the DERIVATION (8834 triples, 1.3 MB) instead of the details
# themselves keeps this bounded; caching the details would not.
#
# Keyed by (archive dir, run_id) — run_ids are content-addressed, but the archive is
# tenant-scoped and an L4 inner cycle runs in-process over a sandboxed one, so the dir has
# to be in the key. The signature is what makes it correct: the scoring walk re-saves the
# same run_id after every sample, so a run's detail GROWS under us.
#
# Cells, not Observations: the candidate a run belongs to is decided by the caller (the
# origin alias below), so baking it into the memo would serve one caller's naming to another.
_CELLS: dict[tuple[str, str], tuple[tuple[int, int], tuple[tuple[int, float], ...]]] = {}
_CELLS_MAX = 4096


def _run_cells(
    stores: Stores,
    run_id: str,
    sig: tuple[int, int] | None,
) -> tuple[tuple[int, float], ...]:
    """This run's ``(sample_id, response)`` pairs, re-derived only when its detail changed."""
    if sig is None:
        return ()
    key = (str(stores.archive.base_dir), run_id)
    hit = _CELLS.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    detail = archive_views.load_run(stores, run_id)
    if detail is None:
        return ()
    cells = tuple(
        (int(sid), graded_response(item))
        for item in detail.get("measurements", [])
        if (sid := item.get("sample_id")) is not None and not is_error_result(item)
    )
    if len(_CELLS) >= _CELLS_MAX:
        _CELLS.clear()
    _CELLS[key] = (sig, cells)
    return cells


def build_archive_observations(
    stores: Stores,
    *,
    dataset_name: str | None,
    origin_sp_hash: str | None = None,
) -> list[Observation]:
    """Walk the measurement store → ``Observation(prompt_fields_id, sample_id, response)`` triples.

    ``dataset_name=None`` is admin/forensic only — prevents cross-dataset ``sample_id`` pollution.

    **The candidate is the searchpoint, not the run.** ``prompt_fields_id`` (= ``sp_hash``) is
    prompt-inclusive and dataset-independent; ``content_hash`` folds in the sample subset, so
    keying on it made one prompt re-scored against N round subsets into N "candidates" — up to
    29 phantom copies of a single origin, each weighting its samples again in δ.

    ``origin_sp_hash`` renames that candidate to ``ORIGIN_ABILITY_ID`` so the archive's copy of
    the origin and the caller's own ``origin_obs`` are one candidate with one θ, not two.

    Grade A only, always. This used to be an optional ``min_grade``: the δ-ruler passed
    ``"A"`` while the two display callers omitted it and got A+B+C, so one dataset carried
    TWO difficulty scales — the grade-A δ that decides which candidates PoBB kills, and an
    A+B+C δ the operator reads off the heatmap. One substrate, by construction.
    """
    obs: list[Observation] = []
    sigs = archive_views.run_signatures(stores)
    entries = archive_views.list_runs(stores, dataset_name=dataset_name)
    for entry in sorted(entries, key=lambda e: (e.get("created_at") or "", e.get("run_id") or "")):
        if not meets_grade(entry_grade(entry), _RULER_GRADE):
            continue
        candidate_id = (entry.get("prompt_fields_id") or "").strip()
        run_id = entry.get("run_id")
        if not candidate_id or not run_id:
            continue
        if origin_sp_hash and candidate_id == origin_sp_hash:
            candidate_id = ORIGIN_ABILITY_ID
        obs.extend(
            Observation(candidate_id, sample_id, response)
            for sample_id, response in _run_cells(stores, run_id, sigs.get(run_id))
        )
    return dedup_observations(obs)


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
