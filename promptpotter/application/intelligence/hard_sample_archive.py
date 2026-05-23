"""Cross-cycle hard-sample artifact — tenant-wide aggregator over the archive.

Peer of :mod:`hard_sample_sorter`. Same artifact shape, same Rasch fit;
the only difference is the observation source: the
:class:`~promptpotter.infrastructure.store.measurement_archive.MeasurementArchive`
instead of a single cycle's ``rounds``. Candidate IDs are
``content_hash[:12]`` (cross-cycle stable) instead of round-local
``cand_NNN``.

Pure read-only — every archive access goes through
:mod:`promptpotter.infrastructure.store.archive_views`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import Observation
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact_from_observations,
)
from promptpotter.infrastructure.store import archive_views

if TYPE_CHECKING:
    from promptpotter.infrastructure.store import Stores

__all__ = [
    "build_archive_hard_samples_artifact",
    "build_archive_observations",
]

CANDIDATE_HASH_LEN = 12


def build_archive_observations(
    stores: Stores,
    backend_id: str,
    *,
    dataset_name: str | None,
    include_unknown: bool = False,
) -> list[Observation]:
    """Walk every measurement detail in the archive, project triples.

    Returns one ``Observation(candidate_id=content_hash[:12], sample_id, hit)``
    per measured row. Skips items without ``sample_id``, items flagged as
    errors, and entries lacking a ``content_hash``.

    ``dataset_name`` scopes the walk to one dataset's archive slice
    (required keyword, ``None`` permitted only for admin/forensic use).
    Cross-dataset pollution on integer ``sample_id`` is the bug this
    arg exists to prevent.
    """
    obs: list[Observation] = []
    for entry in archive_views.list_runs(
        stores, backend_id, dataset_name=dataset_name, include_unknown=include_unknown
    ):
        content_hash = (entry.get("content_hash") or "").strip()
        if not content_hash:
            continue
        candidate_id = content_hash[:CANDIDATE_HASH_LEN]
        run_id = entry.get("run_id")
        if not run_id:
            continue
        detail = archive_views.load_run(stores, backend_id, run_id)
        if detail is None:
            continue
        for item in detail.get("measurements", []):
            sid = item.get("sample_id")
            if sid is None:
                continue
            if item.get("error") or item.get("predicted") == "ERROR":
                continue
            obs.append(
                Observation(
                    candidate_id=candidate_id,
                    sample_id=int(sid),
                    hit=bool(item.get("hit")),
                )
            )
    return obs


def build_archive_hard_samples_artifact(
    stores: Stores,
    backend_id: str,
    *,
    dataset_name: str | None,
    top_k_candidates: int | None = 40,
    top_k_samples: int | None = 40,
    include_unknown: bool = False,
) -> dict[str, Any]:
    """Per-dataset hard-samples artifact, fit on every archive measurement for that dataset.

    Same shape as :func:`hard_sample_sorter.build_hard_samples_artifact`.
    ``cycle_id`` is always ``None`` since the artifact spans all cycles
    *for the requested dataset*. Pass ``top_k_*=None`` for the full
    matrix.
    """
    return build_hard_samples_artifact_from_observations(
        build_archive_observations(
            stores,
            backend_id,
            dataset_name=dataset_name,
            include_unknown=include_unknown,
        ),
        cycle_id=None,
        top_k_candidates=top_k_candidates,
        top_k_samples=top_k_samples,
    )
