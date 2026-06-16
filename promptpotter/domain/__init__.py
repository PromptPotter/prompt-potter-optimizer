"""domain/ — the pure layer: frozen Pydantic models + pure types, no I/O.

Backbone primitives: search_point.py (JobSearchPoint), opt_search_point.py
(OptSearchPoint, PromptTemplate), run_records.py (ForkSpec / CycleSeed /
ResumeCheckpointKind), pipeline_schema.py + pipeline_parsing.py
(PipelineSchema from GET /pipeline), campaign.py (Campaign manifest),
cycle_paths.py (CycleDir / WorkspaceDir newtypes), results.py (RoundResult).
Shared pure logic: scoring.py, escalation_signals.py, validators.py,
phases.py, sample.py, l1_layout.py, connector.py, backend.py.

No mutation (lineage via derive()), no infrastructure imports.
full contract: domain/CLAUDE.md
"""
