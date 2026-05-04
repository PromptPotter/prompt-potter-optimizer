"""SampleIndex / ConfigIndex / AxisIndex — derived views over the MeasurementArchive.

Three collaborating views, kept as a package so they share the
incremental-refresh contract (``_seen_runs`` cursors) and the archive's
``load_since`` / ``list_all`` ingestion seam:

1. **SampleIndex** — per-sample state keyed by ``sample.id: int``.
   Owns Sample primitives + per-sample aggregate tables (hits, failure
   modes, degradation counts, flips). Pure derivation; no on-disk artifact.

2. **ConfigIndex** — per-config view caching ``node_configs → set[run_id]``
   so ``measurements_for_config(predicate)`` skips the O(N) full scan and
   becomes O(unique_configs).

3. **AxisIndex** — derived axis-keyed view; folds new index entries into
   ``_axis_values`` via an in-process ``_axis_seen_runs`` cursor; hosts
   the digest API consumed by L1/L2/L3 prompts (``digest_for_l1_generate``
   / ``_l1_critique`` / ``_l2`` / ``_l3``). Holds a SampleIndex + ConfigIndex
   internally so refresh updates all three in one walk.

Failure-group × axis correlations are recomputed on every refresh — cheap
at current scale and avoids drift from a throttle.

**Cursor pattern, not a base class.** All three carry a ``_seen_runs`` set
for incremental refresh, but the shapes around that set diverge: SampleIndex
and ConfigIndex consume run *details* from ``archive.load_since`` (full
``measurements`` list per run), while AxisIndex walks ``archive.list_all``
*index entries* (pipeline_params + scores only) under a separate
``_axis_seen_runs`` cursor and tracks ``touched_axes`` for per-axis cache
invalidation. A shared ``IncrementalIndexer`` base would have to abstract
which archive call drives the walk, the per-entry payload schema, cursor
ownership, and cache-invalidation hooks — four divergent shapes for three
implementations. Deliberately not unified.
"""

from promptpotter.application.intelligence.indexes.axis import (
    NOISE_THRESHOLD,
    AxisImpact,
    AxisIndex,
    ValueRecord,
)
from promptpotter.application.intelligence.indexes.config import ConfigIndex
from promptpotter.application.intelligence.indexes.sample import (
    FailureCluster,
    HardnessRecord,
    QueryRecord,
    SampleIndex,
)

__all__ = [
    "NOISE_THRESHOLD",
    "AxisImpact",
    "AxisIndex",
    "ConfigIndex",
    "FailureCluster",
    "HardnessRecord",
    "QueryRecord",
    "SampleIndex",
    "ValueRecord",
]
