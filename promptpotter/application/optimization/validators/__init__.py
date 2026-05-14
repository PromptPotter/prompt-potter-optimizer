"""L1/L2/L3 candidate-output validators.

* ``l1_strict`` — STRICT schema-validation surface for ``l1_generate``
  output (used by `OptimizationConfig.forbidden_axes_strict`). Builds
  per-pipeline JSON Schemas, normalizes pipeline_params overrides, runs
  ``detect_invariants`` over yield stats. Failures route through L2 as
  ``ValidationFailure``.
* ``l1_behavior`` — SOFT behavior checks (load-bearing per
  ``promptpotter/CLAUDE.md``: ``forbidden_axes_honored``,
  ``evidence_grounding_present``, etc.). Counts attempts for the audit
  trail; surfaces in ``review.md`` and ``round_NNNN.json``.
* ``l2_l3`` — Output validators for ``l2_context`` and ``l3_plan``
  (verbatim-repeat detection, plan-length floor). Failures route to the
  next layer up via escalation rules.
"""
