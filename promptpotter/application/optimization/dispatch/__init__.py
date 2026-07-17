"""Optimizer LLM info-flow ingress — the single path every optimizer prompt is
composed and dispatched through:

    build_bundle(cycle) → DispatchHub.fill → compile_prompt → LLM

Owns the injection registry (:data:`INJECTIONS`), the :class:`InjectionKind`
classification, the typed :class:`InjectionBundle` per-call state, and the one
rendering path: :meth:`DispatchHub.fill` fills a node's layout
(``NODE_LAYOUTS[node]`` floor, or L2-authored ``opt_sp.memory.l1_layout`` for
``l1_generate``) and resolves any injection tokens left in non-layout prose →
``(filled_template, injection_vars)``. One path for every optimizer node.

To add an input to any prompt, add an injection in :mod:`injections`. Anything
else is drift. Every renderer is layer-agnostic — same render for every layer
that subscribes; per-layer specialisation is the kind of complexity this package
exists to remove. The four :class:`InjectionKind` values split along *origin*,
not consumer:

* ``MEASUREMENT`` — raw evidence from L1 candidate runs.
* ``DERIVED`` — computed from measurements.
* ``TRACE`` — narrative state from prior LLM calls.
* ``DIRECTIVE`` — active instructions to a downstream layer.

Out-of-bounds: no prompt site may read state directly without going through
:func:`build_bundle` + :class:`DispatchHub`. Adding a sidecar fill path or a
per-template renderer is drift; extend the registry in place instead.

Module map:

* :mod:`bundle` — frozen types (``InjectionBundle``, ``CycleSlice``,
  ``RoundDigest``, ``InjectionKind``, ``_Injection``) + format constants + the
  dataset-content fence helper. ``Cycle``-free by contract so renderer tests can
  construct bundles directly.
* :mod:`injections` — the renderers and the :data:`INJECTIONS` registry.
* :mod:`facade` — :class:`DispatchHub`, :func:`build_bundle` (the live
  ``Cycle``-snapshot path), and :func:`validate_template`. Load-bearing per §0.5.
* :mod:`llm_call` — package: ``call`` (:func:`llm_call`,
  :func:`run_optimizer_node`) + ``prompts`` (:func:`load_optimizer_prompt`,
  :func:`get_optimizer_schema`, optimizer-prompt manifest loading).
* :mod:`schemas` — Pydantic output schemas for the five optimizer nodes
  (``L1GenerateOutput``, ``L1CritiqueOutput``, ``L2ContextOutput``,
  ``L3PlanOutput``, ``CheckinOutput``, ``VariantEvidenceGrounding``, etc.).

The optimizer pipeline definition lives at ``datasets/_optimizer/pipeline.json``
— same shape as a backend ``pipeline.json``; powers self-optimization. Loaded by
``llm_call.prompts._load_optimizer_manifest``.
"""
