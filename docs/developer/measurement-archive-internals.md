# Measurement Archive — internals

Concept overview in [`../concepts/measurement-archive.md`](../concepts/measurement-archive.md). This file covers the implementation surface: API, predicate semantics, matcher modes, file shapes.

---

## Class — `MeasurementArchive`

`promptpotter/infrastructure/store/measurement_archive.py`. Constructed inside `Stores` (frozen composite) — call sites use `stores.archive.X`.

### Path constants

- `library/measurements/{run_id}.json` — per-batch detail file.
- `library/measurements.json` — index file (entries keyed implicitly by content_hash via upsert).

`backend_id` is preserved on public methods for call-site stability but is **ignored** for path construction. The archive is tenant-global; identity comes from `content_hash`.

### Public methods

| Method | Purpose |
|---|---|
| `save(backend_id, run_id, data)` | Write a batch detail file + upsert the index entry (filelocked). Idempotent on repeat content_hash. |
| `load_by_id(backend_id, run_id) -> dict \| None` | Direct file load by `run_id`, no index scan. |
| `list_all(backend_id) -> list[dict]` | Index entries (summaries, no items). |
| `load_since(backend_id, seen_ids) -> Iterator[(run_id, detail)]` | Yields `(run_id, detail)` for every batch whose `run_id` is not in `seen_ids`. Used by `AxisIndex.refresh` (sample side). |
| `find_by_node_configs(backend_id, spec) -> list[(entry, match_len)]` | Cache-reuse lookup — positional prefix-exact, sorted by match length desc. |
| `load_reusable_results(backend_id, spec, is_fatal=None) -> dict[query, item]` | Built on top of `find_by_node_configs`. Returns per-query item dict for prior batches sharing a config prefix. |
| `measurements_for_sample(backend_id, sample_id, *, run_ids=None) -> list[Measurement]` | Sample-keyed retrieval. When `run_ids` is given (typically `Sample.run_ids`), skips the index scan. |
| `measurements_for_config(backend_id, predicate) -> list[Measurement]` | Config-keyed retrieval. Subset predicate, see below. Empty predicate returns `[]`. |
| `register_alias`, `register_prompt_alias`, `resolve_aliases` | Prompt-rendering equivalence groups (`library/prompt_aliases.json`). |

---

## The `Measurement` row

`promptpotter/domain/sample.py`:

```python
@dataclass(frozen=True, slots=True)
class Measurement:
    run_id: str
    content_hash: str
    sample_id: int
    query: str
    ground_truth: str
    predicted: str
    hit: bool
    score: float | None
    node_configs: list[tuple[str, dict[str, Any]]]
    pipeline_data: dict[str, Any]
    created_at: str
    run_scores: dict[str, Any]
```

Frozen + slots. Built by the module-private `_to_measurement(run_id, detail, item)` projector. Both retrieval methods funnel through this projector — one canonical row shape.

---

## Shared matcher — `_node_config_matches`

Module-private function in `measurement_archive.py`. One source of truth for "does this stored chain match this spec":

```python
def _node_config_matches(
    run_node_configs: list[Any],
    spec: list[tuple[str, dict]] | dict[str, dict],
    *,
    mode: Literal["prefix_exact", "subset"],
) -> int
```

### `mode="prefix_exact"`

- `spec` is an ordered `list[tuple[name, cfg]]`.
- Walks both sequences in lock-step; matches each pair by `name == name and cfg == cfg` (full dict equality, not subset).
- Returns the **match length** (number of consecutive matching pairs from the start). 0 = no match. Used by cache reuse to gate "we can reuse outputs up to and including node K."

### `mode="subset"`

- `spec` is a `dict[node_name, required_subdict]`.
- Builds a name-keyed view over the stored chain, then for each `(node, subdict)` in spec checks `subdict.items() <= cfg.items()`.
- Empty subdict = node presence test. Empty spec = `0` (callers must reject empty before calling, e.g. `measurements_for_config`).
- Returns `1` on full subset match, `0` otherwise. Used by discovery retrieval.

---

## Predicate shape (config-keyed)

`dict[str, dict[str, Any]]`. Examples:

```python
{"web_search": {"max_sites": 5}}                 # exact value match
{"llm_only": {"model": "openai/gpt-oss-120b"}}
{"web_search": {}, "llm_rerank": {}}             # both nodes present
{"llm_only": {}}                                  # llm_only present, any config
```

**Empty predicate (`{}`) returns `[]`.** This is intentional — the caller must opt in. To list every measurement, use `list_all` + `load_by_id` per entry.

---

## File layout

### `library/measurements/{run_id}.json`

```json
{
  "run_id": "baseline_0ca78d83",
  "name": "baseline",
  "content_hash": "0ca78d83...",
  "prompt_fields_id": "1c8d9b1d6b2de9e0",
  "rendered_prompt_hash": "...",
  "item_count": 20,
  "scores": {"accuracy": 0.85, "total": 20, ...},
  "node_configs": [["llm_only", {"model": "...", "temperature": 0.0}]],
  "pipeline_params": {"llm_only": {"model": "...", "temperature": 0.0}},
  "source": "baseline",
  "connector_type": "default",
  "created_at": "2026-04-27T18:21:33Z",
  "measurements": [
    {
      "sample_id": 0,
      "query": "...",
      "ground_truth": "...",
      "predicted": "...",
      "hit": true,
      "score": 1.0,
      "pipeline_data": {"terminated_at": "llm_only", ...},
      "scored": {"<scorer_id>": {"score": 1.0, "hit": true, "formula": "..."}}
    }
  ]
}
```

`scored` carries the rescore-on-load multi-scorer ledger (see [`../concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md)).

### `library/measurements.json`

```json
{
  "measurements": [
    {
      "run_id": "...",
      "name": "...",
      "content_hash": "...",
      "item_count": 20,
      "scores": {...},
      "node_configs": [["llm_only", {...}]],
      "pipeline_params": {...},
      "source": "...",
      "created_at": "..."
    }
  ],
  "total": 71
}
```

The index is a denormalized read-side projection — `node_configs` is duplicated here so `find_by_node_configs` and `measurements_for_config` can filter without opening every batch file.

---

## Scaling notes

- ~70 batches in the default project today. Per-round growth is ~10-50.
- `measurements_for_config` is scan-based: O(n_batches) over the in-memory index, then `load_by_id` only on matched runs.
- No reverse index (`axis_value_index.json` etc.). Adding one buys negligible perf and adds a sync-bug surface; revisit only if profiling shows the scan is hot.
- `measurements_for_sample` defaults to `Sample.run_ids` (passed by `SampleIndex.measurements`), so it loads only the relevant batches.
- If `load_by_id` becomes hot, add an LRU there — not at the retrieval layer.

---

## See also

- [`../concepts/measurement-archive.md`](../concepts/measurement-archive.md) — concept overview.
- [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) — full tenant-tree reference.
- [`../concepts/scoring-and-traces.md`](../concepts/scoring-and-traces.md) — write path.
- [`axis-index-internals.md`](axis-index-internals.md) — the digest layer that consumes the archive (in-memory derived view).
