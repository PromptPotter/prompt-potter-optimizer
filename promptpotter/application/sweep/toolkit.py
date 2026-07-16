"""Sweep toolkit — result JSON, L1 swap, slicing, rank table.

Verbs (`time-to`/`round1`/`round2`) persist one result JSON under
``archive/sweeps/{l1_meta_prompt_hash}/{dataset}/`` so the dir groups sweeps
by the L1 meta-prompt revision that produced them. Single shape; unpopulated fields are ``None``."""

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

from promptpotter.application.optimization.dispatch.llm_call import prompts as _opt_prompts
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    compute_optimizer_prompt_hashes,
)
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.store.io import read_json, read_json_optional, write_json
from promptpotter.shared.clock import utcnow_iso

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
    "cost_usd",
    "final_accuracy",
    "notes",
)


# --- Result identity + paths ---


def mint_sweep_id() -> str:
    """``sw_YYYYMMDDTHHMMSSZ_<hex4>`` — sortable + collision-resistant."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"sw_{ts}_{secrets.token_hex(2)}"


def sweep_archive_dir(
    tenant_root: Path,
    l1_meta_prompt_hash: str,
    dataset: str,
) -> Path:
    """One dir per (L1 meta-prompt revision, dataset); `rank` recurses across hashes."""
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
    """Build a sweep-result dict in unified shape. Unknown keys in ``fields`` raise (typo guard)."""
    unknown = set(fields) - set(SWEEP_RESULT_FIELDS)
    if unknown:
        raise ValueError(f"unknown sweep-result fields: {sorted(unknown)}")

    base: dict[str, Any] = dict.fromkeys(SWEEP_RESULT_FIELDS)
    base["sweep_id"] = sweep_id or mint_sweep_id()
    base["verb"] = verb
    base["timestamp"] = timestamp or utcnow_iso()
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
    """Persist under `archive/sweeps/{hash}/{dataset}/{verb}_{target?}_{slice?}_{ts}.json`."""
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
    """Meta-prompt hash for *node* in the current snapshot; `l1_generate` is the archive dir key."""
    return compute_optimizer_prompt_hashes().get(node, "unknown")


# --- L1 prompt override (file-path-driven variant swap) ---


def load_prompt_template_from_path(path: Path) -> PromptTemplate:
    """Load an 8-field `PromptTemplate` JSON — sweep variant of `l1_generate`/`l2_context`."""
    body = read_json(path)
    return PromptTemplate(**body)


@contextlib.contextmanager
def optimizer_prompt_override(node_name: str, template: PromptTemplate | None) -> Iterator[None]:
    """A/B-swap `load_optimizer_prompt(node_name)` → *template* (None = no-op); other prompts pass through."""
    if template is None:
        yield
        return

    original = _opt_prompts.base_optimizer_template

    def _overridden(name: str) -> PromptTemplate:
        if name == node_name:
            return template
        return original(name)

    _opt_prompts.base_optimizer_template = _overridden  # type: ignore[assignment]
    try:
        yield
    finally:
        _opt_prompts.base_optimizer_template = original
        # lru_cache clear: canonical loader re-reads from disk, not a stale override.
        with contextlib.suppress(AttributeError):
            original.cache_clear()


# --- Slice modifier ---


SLICE_NAMES: tuple[str, ...] = ("all", "easy", "hard")


def _archive_hit_rates(
    stores: Any,
    sample_ids: list[int],
    *,
    dataset_name: str | None,
) -> dict[int, tuple[float, int]]:
    """`{sample_id: (hit_rate, n)}` from the archive; scoped to *dataset_name* (no cross-dataset bleed)."""
    from promptpotter.infrastructure.store import archive_views

    out: dict[int, tuple[float, int]] = {}
    for sid in sample_ids:
        try:
            ms = archive_views.measurements_for_sample(stores, sid, dataset_name=dataset_name)
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
    dataset_name: str | None = None,
) -> tuple[list[Sample], str]:
    """Filter by *slice_spec* ∈ SLICE_NAMES or ``samples=ID1,ID2,...``.

    `easy`/`hard` quartile-cut the archive's hit-rate distribution; empty archive
    falls back to ``all`` so a first-run sweep still works.
    """
    if slice_spec.startswith("samples="):
        wanted = {sid.strip() for sid in slice_spec[len("samples=") :].split(",") if sid.strip()}
        # Match int- and string-id forms so the operator can pass either.
        filtered = [s for s in samples if str(s.id) in wanted or s.query in wanted]
        return filtered, slice_spec

    name = slice_spec or "all"
    if name == "all" or not samples:
        return samples, "all"

    if name not in ("easy", "hard"):
        raise ValueError(f"unknown slice: {slice_spec!r} (expected one of {SLICE_NAMES})")

    if stores is None:
        logger.info("slice %s requested without stores — falling back to all", name)
        return samples, "all"

    rates = _archive_hit_rates(stores, [s.id for s in samples], dataset_name=dataset_name)
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


# --- Panel stats (from per-round candidate audit trail) ---


def compute_panel_stats(
    *,
    candidates_planned: int,
    candidate_accuracies: list[float],
    candidate_pipeline_params: list[dict[str, Any]],
) -> dict[str, float | None]:
    """Per-round panel summary: mean/max accuracy, `parse_fail_rate = (planned−received)/planned`,
    `pipeline_params_entropy = unique_param_jsons / received` (1.0 = all distinct mutations).

    A panel that received **no** candidate has no accuracy — `None`, rendered `incomplete`, never
    `0.0`. A variant whose L1 prompt parsed nothing and one whose five candidates all scored zero
    are different results, and the sweep rank table is what promotes the winner."""
    received = len(candidate_accuracies)
    planned = max(received, candidates_planned)
    parse_fail = max(0.0, (planned - received) / planned) if planned else 0.0
    if not received:
        return {
            "round1_accuracy": None,
            "round1_best": None,
            "parse_fail_rate": parse_fail,
            "pipeline_params_entropy": None,
        }
    unique = {json.dumps(pp, sort_keys=True, default=str) for pp in candidate_pipeline_params}
    return {
        "round1_accuracy": float(statistics.fmean(candidate_accuracies)),
        "round1_best": float(max(candidate_accuracies)),
        "parse_fail_rate": parse_fail,
        "pipeline_params_entropy": len(unique) / received,
    }


# --- Result discovery + rank table ---


def find_sweep_results(
    tenant_root: Path,
    *,
    dataset: str | None = None,
    sweep_id: str | None = None,
    verb: str | None = None,
) -> list[dict[str, Any]]:
    """Walk `archive/sweeps/**/*.json` (load-then-filter — small N); newest-first by timestamp."""
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
    """Accuracy per $ (origin assumed 0 — archive has no per-sweep origin); None sorts last.

    `final_accuracy` first, then the round-1 panel mean — chosen by *presence*, not truthiness:
    a run that genuinely finished at 0.0 accuracy is a measurement, and `or` would discard it in
    favour of the round-1 number. Unmeasured on both, or costless, and there is no rate."""
    cost = row.get("cost_usd")
    acc = row.get("final_accuracy")
    if acc is None:
        acc = row.get("round1_accuracy")
    if acc is None or not cost:
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
    """Left-aligned ASCII table sorted by *by* (raw fields + derived `cost_per_lift`); None sorts last."""
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
