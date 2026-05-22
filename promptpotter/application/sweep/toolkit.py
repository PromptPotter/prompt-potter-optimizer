"""Sweep-toolkit — result JSON, L1 swap, slicing, rank table.

The toolkit verbs (`time-to`, `round1`, `round2`) each persist one result
JSON under ``{tenant_root}/archive/sweeps/{l1_meta_prompt_hash}/{dataset}/``.
The path scheme groups every sweep against the L1 meta-prompt revision
that produced it, so an operator (or `potter-l1-meta-campaign`) can read
the directory directly to see "what does this L1 edit measure to?"
without grepping campaign artifacts.

Single result shape across verbs; only some fields populated per verb.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import statistics
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.application.optimization.dispatch.llm_call import (
    compute_optimizer_prompt_hashes,
)
from promptpotter.application.optimization.dispatch.llm_call import prompts as _opt_prompts
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.store.base import read_json_optional, write_json

logger = logging.getLogger(__name__)

__all__ = [
    "SWEEP_RESULT_FIELDS",
    "build_sweep_result",
    "compute_panel_stats",
    "current_optimizer_prompt_hash",
    "find_sweep_results",
    "load_prompt_template_from_path",
    "mint_sweep_id",
    "optimizer_prompt_override",
    "rank_sweep_results",
    "slice_samples",
    "sweep_archive_dir",
    "write_sweep_result",
]


# Single shape, all verbs. Unpopulated fields land as ``None`` so disk
# readers can sort/filter without per-verb schema knowledge.
SWEEP_RESULT_FIELDS: tuple[str, ...] = (
    "sweep_id",
    "verb",
    "timestamp",
    "l1_meta_prompt_hash",
    "l1_meta_prompt_label",
    "l1_prompt_path",
    "l2_meta_prompt_label",
    "l2_prompt_path",
    "cycle_id",
    "dataset",
    "slice",
    "panel_size",
    "round1_accuracy",
    "round1_best",
    "round2_accuracy",
    "round2_lift",
    "rounds_to_target",
    "early_exit_reason",
    "parse_fail_rate",
    "pipeline_params_entropy",
    "diversity_l2_score",
    "cost_usd",
    "final_accuracy",
    "notes",
)


# ---------------------------------------------------------------------------
# Result identity + paths
# ---------------------------------------------------------------------------


def mint_sweep_id() -> str:
    """``sw_YYYYMMDDTHHMMSSZ_<hex4>`` — sortable + collision-resistant."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"sw_{ts}_{secrets.token_hex(2)}"


def sweep_archive_dir(
    tenant_root: Path,
    l1_meta_prompt_hash: str,
    dataset: str,
) -> Path:
    """``{tenant_root}/archive/sweeps/{hash}/{dataset}/`` — one dir per
    (L1 meta-prompt revision, dataset) pair. ``rank`` reads recursively
    under ``archive/sweeps/`` to compare across hashes."""
    return tenant_root / "archive" / "sweeps" / l1_meta_prompt_hash / dataset


def build_sweep_result(
    *,
    verb: str,
    dataset: str,
    l1_meta_prompt_hash: str,
    l1_meta_prompt_label: str | None = None,
    sweep_id: str | None = None,
    timestamp: str | None = None,
    slice_name: str = "all",
    **fields: Any,
) -> dict[str, Any]:
    """Build a sweep-result dict in the unified shape — every field
    present, unpopulated ones as ``None``. ``fields`` overrides any
    populated entries; unknown keys raise so a typo in a verb's call
    site fails loudly rather than landing as silent ``null``."""
    unknown = set(fields) - set(SWEEP_RESULT_FIELDS)
    if unknown:
        raise ValueError(f"unknown sweep-result fields: {sorted(unknown)}")

    base: dict[str, Any] = dict.fromkeys(SWEEP_RESULT_FIELDS)
    base["sweep_id"] = sweep_id or mint_sweep_id()
    base["verb"] = verb
    base["timestamp"] = timestamp or datetime.now(UTC).isoformat()
    base["l1_meta_prompt_hash"] = l1_meta_prompt_hash
    base["l1_meta_prompt_label"] = l1_meta_prompt_label
    base["dataset"] = dataset
    base["slice"] = slice_name
    base["notes"] = ""
    base.update(fields)
    return base


def write_sweep_result(
    tenant_root: Path,
    result: dict[str, Any],
    *,
    target: int | None = None,
) -> Path:
    """Persist ``result`` under ``archive/sweeps/{hash}/{dataset}/``.

    Filename: ``{verb}_{target?}_{slice?}_{timestamp}.json``. ``target``
    is the integer threshold N for ``time-to N`` (omitted otherwise).
    ``_slice_{name}`` suffix when ``slice`` is non-default. The shape is
    stable across verbs so ``rank`` can scan one dir.
    """
    verb = result["verb"]
    dataset = result["dataset"]
    h = result["l1_meta_prompt_hash"]
    slice_name = result.get("slice") or "all"
    ts = result["timestamp"].replace(":", "").replace("-", "").replace(".", "").replace("+", "z")
    parts = [verb]
    if target is not None:
        parts.append(str(target))
    if slice_name != "all":
        parts.append(f"slice_{slice_name}")
    parts.append(ts)
    filename = "_".join(parts) + ".json"
    out_dir = sweep_archive_dir(tenant_root, h, dataset)
    out_path = out_dir / filename
    write_json(out_path, result)
    return out_path


def current_optimizer_prompt_hash(node: str = "l1_generate") -> str:
    """The meta-prompt hash for optimizer ``node`` in the current pipeline
    snapshot. Same value that lands on ``index.json::final.prompt_hashes.
    {node}``. The ``l1_generate`` hash is the sweep-archive directory key,
    so every sweep result groups under the L1 revision that produced it."""
    return compute_optimizer_prompt_hashes().get(node, "unknown")


# ---------------------------------------------------------------------------
# L1 prompt override — file-path-driven variant swap for round1/round2
# ---------------------------------------------------------------------------


def load_prompt_template_from_path(path: Path) -> PromptTemplate:
    """Load a ``PromptTemplate`` from a JSON file on disk.

    Accepts the 8-field shape stored at ``datasets/{name}/prompts/{node}.json``
    and at ``optimizer_pipeline.json::resolved_prompts['{node}/1']`` — used
    to swap in an ``l1_generate`` or ``l2_context`` variant for a sweep.
    """
    body = json.loads(path.read_text(encoding="utf-8"))
    return PromptTemplate(**body)


@contextlib.contextmanager
def optimizer_prompt_override(node_name: str, template: PromptTemplate | None) -> Iterator[None]:
    """Temporarily redirect ``load_optimizer_prompt(node_name)`` to
    ``template``. Pass ``template=None`` to leave the loader untouched
    (no-op).

    Used by ``round1``/``round2`` to A/B a candidate optimizer meta-prompt
    in one invocation — ``node_name`` is ``l1_generate`` or ``l2_context``.
    Restores the cached loader on exit; the LRU cache is cleared so
    subsequent loads see the canonical registry again. Only ``node_name``
    is overridden; the other optimizer prompts pass through.
    """
    if template is None:
        yield
        return

    original = _opt_prompts._load_local

    def _overridden(name: str) -> PromptTemplate:
        if name == node_name:
            return template
        return original(name)

    _opt_prompts._load_local = _overridden  # type: ignore[assignment]
    try:
        yield
    finally:
        _opt_prompts._load_local = original
        # Drop the lru_cache so the canonical loader's first call re-reads
        # from disk rather than returning a stale override.
        with contextlib.suppress(AttributeError):
            original.cache_clear()


# ---------------------------------------------------------------------------
# Slice modifier — sample-population filter shared across verbs
# ---------------------------------------------------------------------------


SLICE_NAMES: tuple[str, ...] = ("all", "easy", "hard")


def _archive_hit_rates(
    stores: Any,
    backend_id: str,
    sample_ids: list[int],
    *,
    dataset_name: str | None,
) -> dict[int, tuple[float, int]]:
    """``{sample_id: (hit_rate, n_measurements)}`` from the cross-cycle
    archive (routed through ``archive_views`` facade). Missing samples
    (never measured) are omitted. Scoped to ``dataset_name`` so an AIME
    sample_id=14 hit rate doesn't bleed into a JustLogic slice decision."""
    from promptpotter.infrastructure.store import archive_views

    out: dict[int, tuple[float, int]] = {}
    for sid in sample_ids:
        try:
            ms = archive_views.measurements_for_sample(
                stores, backend_id, sid, dataset_name=dataset_name
            )
        except Exception:
            logger.debug("archive lookup failed for sample %d", sid, exc_info=True)
            continue
        if not ms:
            continue
        hits = [1.0 if m.hit else 0.0 for m in ms]
        out[sid] = (sum(hits) / len(hits), len(hits))
    return out


def slice_samples(
    samples: list[Sample],
    slice_spec: str,
    *,
    stores: Any | None = None,
    backend_id: str | None = None,
    dataset_name: str | None = None,
) -> tuple[list[Sample], str]:
    """Filter ``samples`` per ``slice_spec`` (one of ``SLICE_NAMES`` or
    ``samples=ID1,ID2,...``). Returns ``(filtered, resolved_name)``.

    ``easy``/``hard`` need archive measurements to classify by hit rate;
    when the archive is empty (fresh dataset, no prior cycle) the slice
    silently falls back to ``all`` so a first-run sweep still works.
    Archive access routes through ``archive_views`` per §3.7, scoped to
    ``dataset_name`` so cross-dataset hit rates don't drive the cut.
    """
    if slice_spec.startswith("samples="):
        wanted = {sid.strip() for sid in slice_spec[len("samples=") :].split(",") if sid.strip()}
        # Match against int id and string id form so the operator can pass
        # either without surprises.
        filtered = [s for s in samples if str(s.id) in wanted or s.query in wanted]
        return filtered, slice_spec

    name = slice_spec or "all"
    if name == "all" or not samples:
        return samples, "all"

    if name not in ("easy", "hard"):
        raise ValueError(f"unknown slice: {slice_spec!r} (expected one of {SLICE_NAMES})")

    if stores is None or backend_id is None:
        logger.info("slice %s requested without stores — falling back to all", name)
        return samples, "all"

    rates = _archive_hit_rates(
        stores, backend_id, [s.id for s in samples], dataset_name=dataset_name
    )
    if not rates:
        logger.info("slice %s requested but archive is empty — falling back to all", name)
        return samples, "all"

    # Quartile cut: bottom 25% by hit_rate ⇒ hard; top 25% ⇒ easy.
    sorted_hits = sorted(rates.values())
    n = len(sorted_hits)
    if n < 4:
        logger.info("slice %s: too few measured samples (%d) — falling back to all", name, n)
        return samples, "all"
    q1 = sorted_hits[n // 4][0]
    q3 = sorted_hits[(3 * n) // 4][0]
    if name == "hard":
        keep = {sid for sid, (rate, _n) in rates.items() if rate <= q1}
    else:
        keep = {sid for sid, (rate, _n) in rates.items() if rate >= q3}
    filtered = [s for s in samples if s.id in keep]
    if not filtered:
        logger.info("slice %s resolved to zero samples — falling back to all", name)
        return samples, "all"
    return filtered, name


# ---------------------------------------------------------------------------
# Panel stats — derived from per-round candidate audit trail
# ---------------------------------------------------------------------------


def compute_panel_stats(
    *,
    candidates_planned: int,
    candidate_accuracies: list[float],
    candidate_pipeline_params: list[dict[str, Any]],
) -> dict[str, float]:
    """Per-round panel summary: accuracy mean/max, parse-fail, entropy.

    - ``round1_accuracy`` — mean of the per-candidate accuracies.
    - ``round1_best`` — max accuracy in the panel.
    - ``parse_fail_rate`` — ``(planned − received) / planned``; counts
      candidates that L1 failed to produce (JSON parse failures, empty
      responses, validation rejects).
    - ``pipeline_params_entropy`` — unique pipeline_params JSON / received.
      1.0 ⇒ every candidate is a distinct mutation; 0.0 ⇒ all identical.
    """
    received = len(candidate_accuracies)
    planned = max(received, candidates_planned)
    parse_fail = max(0.0, (planned - received) / planned) if planned else 0.0
    if not received:
        return {
            "round1_accuracy": 0.0,
            "round1_best": 0.0,
            "parse_fail_rate": parse_fail,
            "pipeline_params_entropy": 0.0,
        }
    unique = {json.dumps(pp, sort_keys=True, default=str) for pp in candidate_pipeline_params}
    return {
        "round1_accuracy": float(statistics.fmean(candidate_accuracies)),
        "round1_best": float(max(candidate_accuracies)),
        "parse_fail_rate": parse_fail,
        "pipeline_params_entropy": len(unique) / received,
    }


# ---------------------------------------------------------------------------
# Result discovery + rank table
# ---------------------------------------------------------------------------


def find_sweep_results(
    tenant_root: Path,
    *,
    dataset: str | None = None,
    sweep_id: str | None = None,
    verb: str | None = None,
) -> list[dict[str, Any]]:
    """Walk ``archive/sweeps/**/*.json`` and load matching results.

    Filters narrow the walk *after* JSON parse — file count under the
    archive is tiny enough that a load-then-filter scan stays cheap.
    Returned newest-first by ``timestamp``.
    """
    root = tenant_root / "archive" / "sweeps"
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in root.rglob("*.json"):
        data = read_json_optional(path)
        if not isinstance(data, dict):
            continue
        if dataset and data.get("dataset") != dataset:
            continue
        if sweep_id and data.get("sweep_id") != sweep_id:
            continue
        if verb and data.get("verb") != verb:
            continue
        data["_path"] = str(path)
        out.append(data)
    out.sort(key=lambda d: d.get("timestamp") or "", reverse=True)
    return out


_DERIVED_COLUMNS: tuple[str, ...] = ("cost_per_lift",)


def _derive_cost_per_lift(row: dict[str, Any]) -> float | None:
    """Lift per $ spent.

    Uses ``final_accuracy`` as the lift signal — the archive doesn't
    track a per-sweep origin directly, so ``cost_per_lift`` is
    interpretable as "accuracy gained per $" with origin assumed 0.
    Returns ``None`` when cost is missing/zero so unsortable rows sink
    to the bottom of a ``rank --by cost_per_lift`` view.
    """
    cost = row.get("cost_usd") or 0.0
    acc = row.get("final_accuracy") or row.get("round1_accuracy") or 0.0
    if not cost:
        return None
    return float(acc) / float(cost)


def _row_value(row: dict[str, Any], column: str) -> Any:
    if column == "cost_per_lift":
        return _derive_cost_per_lift(row)
    return row.get(column)


def rank_sweep_results(
    results: list[dict[str, Any]],
    *,
    by: str,
    last: int | None = None,
    ascending: bool = False,
) -> str:
    """Render ``results`` as a left-aligned ASCII table sorted by ``by``.

    Recognized derived columns: ``cost_per_lift``. All raw fields in
    ``SWEEP_RESULT_FIELDS`` are sortable. ``None`` values sort last
    regardless of direction.
    """
    valid_columns = set(SWEEP_RESULT_FIELDS) | set(_DERIVED_COLUMNS)
    if by not in valid_columns:
        raise ValueError(f"--by must be one of {sorted(valid_columns)}, got {by!r}")

    def sort_key(row: dict[str, Any]) -> tuple[int, Any]:
        v = _row_value(row, by)
        if v is None:
            return (1, 0)
        return (0, -v if isinstance(v, int | float) and not ascending else v)

    ordered = sorted(results, key=sort_key)
    if last is not None:
        ordered = ordered[:last]

    columns = (
        "timestamp",
        "verb",
        "l1_meta_prompt_label",
        "l1_meta_prompt_hash",
        "dataset",
        "slice",
        by,
    )
    seen: set[str] = set()
    headers: list[str] = []
    for c in columns:
        if c in seen:
            continue
        seen.add(c)
        headers.append(c)

    def fmt(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    rows = [[fmt(_row_value(r, h)) for h in headers] for r in ordered]
    widths = [
        max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)
    ]
    sep = "  "
    head_line = sep.join(h.ljust(w) for h, w in zip(headers, widths, strict=False))
    out_lines = [head_line, sep.join("-" * w for w in widths)]
    out_lines.extend(
        sep.join(c.ljust(w) for c, w in zip(row, widths, strict=False)) for row in rows
    )
    return "\n".join(out_lines)
