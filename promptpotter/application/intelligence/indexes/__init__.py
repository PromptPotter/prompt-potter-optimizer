"""SampleIndex / AxisIndex — derived views over the MeasurementArchive.

One package because the two share the incremental-refresh contract and the archive's
ingestion seam; AxisIndex holds a SampleIndex so one walk refreshes both. **A cursor
pattern, not a base class, and deliberately so** — both carry a ``_seen_runs`` set, but
one consumes run *details* and the other *index entries* under its own cursor, so a
shared base would have to abstract the archive call, the payload schema, cursor ownership
and cache invalidation all at once. Correlations recompute every refresh: cheap at this
scale, and a throttle would drift.
"""
