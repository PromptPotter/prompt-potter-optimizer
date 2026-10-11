from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.rasch import (
    ORIGIN_ABILITY_ID,
    Observation,
    dedup_observations,
)
from promptpotter.domain.measurement_provenance import meets_grade
from promptpotter.infrastructure.store import archive_queries
from promptpotter.infrastructure.store.read_model import derived

if TYPE_CHECKING:
    from promptpotter.domain.sample import ArchiveEntry
    from promptpotter.domain.scoring import Scorer
    from promptpotter.infrastructure.store.stores import Stores

__all__ = ["build_archive_observations"]

# Not a knob: the ruler PoBB cuts with and the heatmap the operator reads must be the same scale.
_RULER_GRADE = "A"


def _population_cells(
    stores: Stores,
    entry: ArchiveEntry,
    sig: list[list[Any]],
    *,
    scorer: Scorer,
) -> tuple[tuple[int, float, str], ...]:
    """``(sample_id, objective, grade)``; memoized by archive dir AND scorer: the grade is the READER's formula."""

    def grade() -> tuple[tuple[int, float, str], ...]:
        held = archive_queries.load_population(stores, entry)
        return tuple(
            (cell.sample_id, float(objective), answer.provenance)
            for answer, cell in zip(held, scorer.sheet(a.cell for a in held), strict=True)
            if cell.scored and (objective := cell.grade.objective) is not None
        )

    # A controlled line's memory moves without its files moving, so it is never memoized.
    if archive_queries.memory_scoped():
        return grade()
    key = (
        "population_cells",
        stores.archive.base_dir,
        entry.config_key,
        entry.dataset_name,
        scorer.id,
    )
    return derived(key, sig=tuple(map(tuple, sig)) or None, compute=grade) or ()


def build_archive_observations(
    stores: Stores,
    *,
    dataset_name: str | None,
    scorer: Scorer,
    sample_ids: frozenset[int] | None,
    origin_sp_hash: str | None = None,
) -> list[Observation]:
    """*sample_ids* is the SEARCH pool: the archive is filed by dataset, so bench rows sit here and must reach no ruler."""
    obs: list[Observation] = []
    sigs = archive_queries.population_signatures(stores, dataset_name=dataset_name)
    folded = {
        row.config_key: row.graded
        for row in archive_queries.sample_fold_rows(stores, dataset_name=dataset_name or "")
        if row.fk == scorer.id and sigs.get(row.config_key) == row.sig
    }
    for entry in archive_queries.list_populations(stores, dataset_name=dataset_name):
        candidate_id = entry.prompt_fields_id
        if not candidate_id:
            continue
        if origin_sp_hash and candidate_id == origin_sp_hash:
            candidate_id = ORIGIN_ABILITY_ID
        key = entry.config_key
        cells = (
            folded[key]
            if key in folded
            else _population_cells(stores, entry, sigs.get(key) or [], scorer=scorer)
        )
        obs.extend(
            Observation(candidate_id, sample_id, response)
            for sample_id, response, grade in cells
            if meets_grade(grade, _RULER_GRADE) and (sample_ids is None or sample_id in sample_ids)
        )
    return dedup_observations(obs)
