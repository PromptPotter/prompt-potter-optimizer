"""Hard-sample-sorter — cross-cut view over (candidate, sample, hit) observations.

Phase-1 seed for the hard-sample-sorter capability (``docs/specs/hard-sample-sorter.md``).
The Rasch posterior in ``rasch.py`` already produces per-sample difficulty
(``δ_s``) and per-candidate ability (``θ_c``); this module exposes the
companion primitive — the raw candidate×sample hit matrix that future phases
render as an ASCII heatmap (phase 2) and a webapp heatmap (phase 3).

Pure read-only view. Reuses ``build_observations`` so the error-flag and
missing-sample-id policy is defined in exactly one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.intelligence.adaptive_prefix import build_observations

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult

__all__ = ["build_candidate_sample_matrix"]


def build_candidate_sample_matrix(
    rounds: list[RoundResult],
) -> dict[tuple[str, int], bool]:
    """Flatten per-round results into a sparse ``(candidate_id, sample_id) → hit`` map.

    Unmeasured cells are absent; callers distinguish hit/miss/none via
    ``matrix.get((cid, sid))`` (returns ``None`` when unmeasured). This tri-state
    is the contract the phase-2 ASCII heatmap and phase-3 UI heatmap consume.
    """
    return {(o.candidate_id, o.sample_id): o.hit for o in build_observations(rounds)}
