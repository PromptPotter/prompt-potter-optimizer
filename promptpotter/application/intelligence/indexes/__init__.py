"""SampleIndex / AxisIndex — derived views over the MeasurementArchive.

Two collaborating views, kept as a package so they share the
incremental-refresh contract (``_seen_runs`` cursors) and the archive's
``load_since`` / ``list_all`` ingestion seam:

1. **SampleIndex** — per-sample state keyed by ``sample.id: int``.
   Owns Sample primitives + per-sample aggregate tables (hits, failure
   modes, degradation counts, flips). Pure derivation; no on-disk artifact.

2. **AxisIndex** — derived axis-keyed view; folds new index entries into
   ``_axis_values`` via an in-process ``_axis_seen_runs`` cursor; exposes
   a ``digest()`` API summarising parameter impact / query patterns /
   failure modes for the zero-signal filter, scoring-set evolution, and
   ranking heuristics. Holds a SampleIndex internally so refresh updates
   both in one walk.

Failure-group × axis correlations are recomputed on every refresh — cheap
at current scale and avoids drift from a throttle.

**Cursor pattern, not a base class.** Both carry a ``_seen_runs`` set
for incremental refresh, but the shapes around that set diverge: SampleIndex
consumes run *details* from ``archive.load_since`` (full ``measurements``
list per run), while AxisIndex walks ``archive.list_all`` *index entries*
(pipeline_params + scores only) under a separate ``_axis_seen_runs`` cursor
and tracks ``touched_axes`` for per-axis cache invalidation. A shared
``IncrementalIndexer`` base would have to abstract which archive call drives
the walk, the per-entry payload schema, cursor ownership, and
cache-invalidation hooks — divergent shapes for each implementation.
Deliberately not unified.
"""

from promptpotter.application.intelligence.indexes.axis import (
    NOISE_THRESHOLD,
    AxisImpact,
    AxisIndex,
    ValueRecord,
)
from promptpotter.application.intelligence.indexes.sample import (
    FailureCluster,
    SampleIndex,
    SampleRecord,
)

__all__ = [
    "NOISE_THRESHOLD",
    "AxisImpact",
    "AxisIndex",
    "FailureCluster",
    "SampleIndex",
    "SampleRecord",
    "ValueRecord",
]
