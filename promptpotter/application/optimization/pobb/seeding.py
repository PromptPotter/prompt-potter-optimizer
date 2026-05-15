"""Stable RNG for PoBB Monte Carlo — record/replay produce identical draws.

`posterior_best_probabilities` falls back to an unseeded
`np.random.default_rng()` when no `rng` is passed, so live scoring
and resume replay independently draw from the MC and can disagree
near the elimination threshold. Both call sites pass `pobb_rng(...)`
with the same decision inputs to get bit-identical draws.
"""

from __future__ import annotations

from hashlib import blake2b

import numpy as np

__all__ = ["pobb_rng"]


def pobb_rng(
    round_num: int,
    candidate_id: str,
    prior_ids: list[str],
    n: int,
) -> np.random.Generator:
    key = f"{int(round_num)}|{candidate_id}|{','.join(sorted(prior_ids))}|{int(n)}".encode()
    seed = int.from_bytes(blake2b(key, digest_size=8).digest(), "big")
    return np.random.default_rng(seed)
