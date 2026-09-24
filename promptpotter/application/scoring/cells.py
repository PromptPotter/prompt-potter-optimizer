"""One cell opened: an archive row assembled into its trace — `GET /datasets/{name}/cells/{run_id}/{sample_id}`.

The spans are READ-TIME assembly over what the row already banked, never a second record of it.
A node's input is re-rendered from the run's own node config over the sample fields a row keeps
(`query`, `ground_truth`, `question`), so a template reading any other `Sample` field renders it
blank; outputs are attributed through the dataset's CURRENT pipeline schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.sample_measurement import interpolate_prompt
from promptpotter.domain.cells import Cell, CellSpan
from promptpotter.domain.dashboard_rows import sample_status
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.scoring import recorded_cost_s
from promptpotter.infrastructure.store.archive_queries import load_run, read_cold_payload
from promptpotter.infrastructure.store.dataset_access import (
    dataset_pipeline_path,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.stores import Stores

__all__ = ["assemble_cell", "measured_row", "open_cell"]

# Folded into the spans, so not repeated beside them.
_SPAN_KEYS = frozenset({"step_tokens", "step_timings", "terminal_node", "total_time"})


def measured_row(
    detail: dict[str, Any], sample_id: int, cold: list[dict[str, Any]] | None
) -> dict[str, Any] | None:
    """The run's row for *sample_id* — the last one, as the archive folds — with what
    `compact-archive` moved to the cold store put back. The cold entry only FILLS absent keys: it
    is aligned by line index, and a row appended after compaction must not be overwritten by it."""
    row = next(
        (
            r
            for r in reversed(detail.get("measurements") or [])
            if isinstance(r, dict) and r.get("sample_id") == sample_id
        ),
        None,
    )
    if row is None:
        return None
    entries = [e for e in cold or [] if e.get("k") == f"m:{sample_id}"]
    if not entries:
        return row
    entry = max(entries, key=lambda e: e.get("i", -1))
    pd = row.get("pipeline_data")
    return {
        **(entry.get("row") or {}),
        **row,
        "pipeline_data": {**(entry.get("pd") or {}), **(pd if isinstance(pd, dict) else {})},
    }


def _span(
    node: str,
    cfg: dict[str, Any],
    pd: dict[str, Any],
    variables: dict[str, Any],
    output_keys: list[str],
) -> CellSpan:
    tokens = (pd.get("step_tokens") or {}).get(node)
    tokens = tokens if isinstance(tokens, dict) else {}
    seconds = (pd.get("step_timings") or {}).get(node)
    prompt = cfg.get("prompt")
    return CellSpan(
        node=node,
        model=tokens.get("model") or cfg.get("model"),
        provider=tokens.get("provider") or cfg.get("provider"),
        input=interpolate_prompt(prompt, variables) if isinstance(prompt, str) else None,
        config={k: v for k, v in cfg.items() if k != "prompt"},
        outputs={k: pd[k] for k in output_keys if k in pd},
        seconds=float(seconds) if isinstance(seconds, int | float) else None,
        input_tokens=tokens.get("input"),
        output_tokens=tokens.get("output"),
        cache_read_tokens=tokens.get("cache_read"),
        cost_usd=tokens.get("cost_usd"),
        estimated=bool(tokens.get("estimated", False)),
    )


def assemble_cell(
    detail: dict[str, Any], row: dict[str, Any], schema: PipelineSchema | None, *, run_id: str
) -> Cell:
    """*row* as its trace. *schema* attributes outputs to nodes; without one every output stays
    in ``other_outputs`` rather than being guessed onto a node."""
    pd = row.get("pipeline_data")
    pd = pd if isinstance(pd, dict) else {}
    sid = int(row["sample_id"])
    variables = {
        "id": sid,
        "query": row.get("query") or "",
        "ground_truth": row.get("ground_truth") or None,
        "question": pd.get("question"),
    }
    outputs_of = {n.name: n.output_keys for n in schema.nodes} if schema is not None else {}
    spans = [
        _span(
            str(node), cfg if isinstance(cfg, dict) else {}, pd, variables, outputs_of.get(node, [])
        )
        for node, cfg in detail.get("node_configs") or []
    ]
    attributed = {k for s in spans for k in s.outputs} | _SPAN_KEYS
    fitness = row.get("fitness")
    return Cell(
        run_id=run_id,
        sample_id=sid,
        dataset_name=detail.get("dataset_name"),
        run_name=str(detail.get("name") or ""),
        created_at=detail.get("created_at"),
        prompt_fields_id=detail.get("prompt_fields_id"),
        query=str(row.get("query") or ""),
        ground_truth=str(row.get("ground_truth") or ""),
        predicted=str(row.get("predicted") or ""),
        status=sample_status(row),
        fitness=float(fitness) if isinstance(fitness, int | float) else None,
        cached=bool(row.get("cached", False)),
        error=row.get("error"),
        terminal_node=pd.get("terminal_node"),
        seconds=recorded_cost_s(row),  # type: ignore[arg-type]
        spans=spans,
        other_outputs={k: v for k, v in pd.items() if k not in attributed},
    )


def open_cell(stores: Stores, name: str, run_id: str, sample_id: int) -> Cell:
    """The cell at ``(run_id, sample_id)``, which must be a run filed under dataset *name*."""
    # The first read keyed on a URL-supplied run id — it becomes a file name under the archive.
    if not run_id or any(ch in run_id for ch in "/\\") or ".." in run_id:
        raise NotFoundError(f"Run '{run_id}' not found")
    dataset_dir = readable_dataset_dir(stores, name)
    detail = load_run(stores, run_id)
    if detail is None or detail.get("dataset_name") != name:
        raise NotFoundError(f"Run '{run_id}' not found under dataset '{name}'")
    row = measured_row(detail, sample_id, read_cold_payload(stores, run_id))
    if row is None:
        raise NotFoundError(f"Run '{run_id}' holds no cell for sample {sample_id}")
    pipeline = dataset_pipeline_path(dataset_dir)
    schema = parse_pipeline_response(read_yaml(pipeline)) if pipeline.is_file() else None
    return assemble_cell(detail, row, schema, run_id=run_id)
