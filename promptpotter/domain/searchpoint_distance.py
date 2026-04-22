"""Structural similarity between two SearchPoints — pure, no I/O, no embeddings.

Composed of three cheap structural signals:
  - lineage_hops: distance in the OptSearchPoint parent_id DAG.
  - param_hamming: normalized Hamming over PipelineSchema.node_configs().
  - prompt_jaccard: token Jaccard over PROMPT_STRING_FIELDS.

Used as the cold-start prior on candidate ability θ_c in the Rasch fitter.
Once a candidate has its own observations the likelihood dominates and
this kernel fades naturally — it only matters for cold starts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.shared.constants import PROMPT_STRING_FIELDS

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema


@dataclass(frozen=True)
class KernelWeights:
    """Convex combination weights — sum need not be 1.0 (kernel renormalizes)."""

    lineage: float = 0.3
    param: float = 0.5
    prompt: float = 0.2
    lineage_decay: float = 0.5


_DEFAULT_WEIGHTS = KernelWeights()


def lineage_hops(a: OptSearchPoint, b: OptSearchPoint, lineage: dict[str, str | None]) -> int:
    """Count parent_id hops between *a* and *b* via lowest common ancestor.

    *lineage* is ``{candidate_id: parent_id}`` (parent_id may be ``None``
    for the baseline). Hops to LCA on each side, summed. Returns a large
    sentinel when the two candidates have no common ancestor in the DAG.
    """
    if a.id == b.id:
        return 0

    def chain(start: str) -> list[str]:
        out = [start]
        seen = {start}
        cur: str | None = lineage.get(start)
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = lineage.get(cur)
        return out

    chain_a = chain(a.id)
    chain_b = chain(b.id)
    set_b = {cid: i for i, cid in enumerate(chain_b)}
    for i, cid in enumerate(chain_a):
        if cid in set_b:
            return i + set_b[cid]
    return 1_000_000


def lineage_kernel(hops: int, decay: float = 0.5) -> float:
    """``exp(-decay * hops)`` — bounded in (0, 1]."""
    if hops >= 1_000_000:
        return 0.0
    return math.exp(-decay * hops)


def param_hamming(
    a_params: dict | None,
    b_params: dict | None,
    schema: PipelineSchema | None,
) -> float:
    """Normalized Hamming distance over ``schema.node_configs(params)``.

    Compares value-by-value across all (node, key) pairs in either
    side's union. Returns a distance in ``[0, 1]``: 0 means identical,
    1 means every key differs. Empty union → 0 (no signal).
    """
    a = a_params or {}
    b = b_params or {}

    node_names = [n.name for n in schema.nodes] if schema is not None else sorted(set(a) | set(b))

    differing = 0
    total = 0
    for node in node_names:
        a_cfg = a.get(node, {}) if isinstance(a.get(node), dict) else {}
        b_cfg = b.get(node, {}) if isinstance(b.get(node), dict) else {}
        keys = set(a_cfg) | set(b_cfg)
        for k in keys:
            total += 1
            if a_cfg.get(k) != b_cfg.get(k):
                differing += 1
    if total == 0:
        return 0.0
    return differing / total


def param_kernel(distance: float) -> float:
    """``1 - distance`` — Hamming distance maps to similarity directly."""
    return max(0.0, 1.0 - distance)


def _tokenize(text: str) -> set[str]:
    """Whitespace + lowercase tokenization. Empty/None → empty set."""
    if not text:
        return set()
    return {t for t in text.lower().split() if t}


def prompt_jaccard(a: OptSearchPoint, b: OptSearchPoint) -> float:
    """Mean per-field Jaccard similarity over ``PROMPT_STRING_FIELDS``.

    Each prompt field contributes a Jaccard overlap; the mean across
    populated fields is the similarity. Both empty → 1.0 (identical).
    One empty + one non-empty → 0 for that field.
    """
    sims: list[float] = []
    for field in PROMPT_STRING_FIELDS:
        a_tokens = _tokenize(getattr(a, field, ""))
        b_tokens = _tokenize(getattr(b, field, ""))
        if not a_tokens and not b_tokens:
            continue
        union = a_tokens | b_tokens
        if not union:
            continue
        sims.append(len(a_tokens & b_tokens) / len(union))
    if not sims:
        return 1.0
    return sum(sims) / len(sims)


def kernel(
    a: OptSearchPoint,
    b: OptSearchPoint,
    *,
    a_params: dict | None,
    b_params: dict | None,
    lineage: dict[str, str | None],
    schema: PipelineSchema | None,
    weights: KernelWeights = _DEFAULT_WEIGHTS,
) -> float:
    """Composite structural similarity in ``[0, 1]``.

    Convex combination of lineage / Hamming-param / Jaccard-prompt
    kernels, normalized by the weight sum. ``a_params``/``b_params`` are
    the merged ``pipeline_params`` for each candidate (after per-candidate
    overrides have been applied).
    """
    w_total = weights.lineage + weights.param + weights.prompt
    if w_total <= 0:
        return 0.0

    k_lin = lineage_kernel(lineage_hops(a, b, lineage), weights.lineage_decay)
    k_par = param_kernel(param_hamming(a_params, b_params, schema))
    k_pr = prompt_jaccard(a, b)
    return (weights.lineage * k_lin + weights.param * k_par + weights.prompt * k_pr) / w_total
