"""Cross-cycle hard-sample artifact — archive-sourced peer of :mod:`hard_sample_sorter`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import (
    ORIGIN_ABILITY_ID,
    Observation,
    dedup_observations,
)
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact_from_observations,
)
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.measurement_provenance import entry_grade, meets_grade
from promptpotter.domain.scoring import is_graded
from promptpotter.infrastructure.store import archive_queries
from promptpotter.infrastructure.store.read_model import derived

if TYPE_CHECKING:
    from promptpotter.domain.scoring import CellScorer
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "build_archive_hard_samples_artifact",
    "build_archive_observations",
]

# The one provenance grade every δ fit is built from — deliberate, full-LLM-path
# measurements, not connector noise. The ruler PoBB kills candidates with and the
# heatmap the operator reads must be the same scale, so this is not a knob.
_RULER_GRADE = "A"


def _run_cells(
    stores: Stores,
    run_id: str,
    sig: tuple[int, int] | None,
    *,
    scorer: CellScorer,
    scorer_id: str,
) -> tuple[tuple[int, float], ...]:
    """Keyed by archive dir and scorer beside the run: the archive is tenant-scoped, the grade is
    the READER's formula, and a run's detail GROWS under the scoring walk."""

    def grade() -> tuple[tuple[int, float], ...]:
        detail = archive_queries.load_run(stores, run_id)
        if detail is None:
            return ()
        rows = rescore_results([dict(item) for item in detail.get("measurements", [])], scorer)
        return tuple(
            (int(sid), float(row["objective"]))
            for row in rows
            if (sid := row.get("sample_id")) is not None and is_graded(row)
        )

    key = ("run_cells", stores.archive.base_dir, run_id, scorer_id)
    return derived(key, sig=sig, compute=grade) or ()


def build_archive_observations(
    stores: Stores,
    *,
    dataset_name: str | None,
    scorer: CellScorer,
    scorer_id: str,
    sample_ids: frozenset[int] | None,
    origin_sp_hash: str | None = None,
) -> list[Observation]:
    """Measurement store → ``Observation`` triples. **The candidate is the SEARCHPOINT, not the run** — keying on
    ``content_hash`` turns one prompt re-scored on N subsets into N candidates. Grade A only, by construction.

    **The archive stores MEASUREMENTS; the grade is the READING campaign's** — *scorer* grades every
    row, so one ruler is one formula, and a cell that formula cannot grade reaches no δ.

    *sample_ids* is the reading campaign's search pool: the archive is filed by dataset, so a row of
    that campaign's bench set sits here too and must reach no ruler it selects on. ``None`` reads all."""
    obs: list[Observation] = []
    sigs = archive_queries.run_signatures(stores)
    entries = archive_queries.list_runs(stores, dataset_name=dataset_name)
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
            for sample_id, response in _run_cells(
                stores, run_id, sigs.get(run_id), scorer=scorer, scorer_id=scorer_id
            )
            if sample_ids is None or sample_id in sample_ids
        )
    return dedup_observations(obs)


def build_archive_hard_samples_artifact(
    stores: Stores,
    *,
    dataset_name: str | None,
    scorer: CellScorer,
    scorer_id: str,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
) -> dict[str, Any]:
    return build_hard_samples_artifact_from_observations(
        build_archive_observations(
            stores,
            dataset_name=dataset_name,
            scorer=scorer,
            scorer_id=scorer_id,
            sample_ids=None,
        ),
        cycle_id=None,
        top_k_candidates=top_k_candidates,
        top_k_samples=top_k_samples,
    )
