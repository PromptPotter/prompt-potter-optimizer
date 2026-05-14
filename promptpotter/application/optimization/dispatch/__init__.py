"""Optimizer LLM info-flow ingress.

The single path every optimizer LLM call (l1_generate, l1_critique,
l2_context, l3_plan) composes its prompt through:

    build_bundle(cycle) → DispatchHub.fill_* → compile_prompt → LLM

Modules:

* ``hub`` — :data:`INJECTIONS` registry, :class:`DispatchHub`,
  :func:`build_bundle`, :func:`validate_template`. Load-bearing per §0.5.
* ``llm_call`` — :func:`llm_call`, :func:`run_optimizer_node`,
  :func:`load_optimizer_prompt`, optimizer-prompt manifest loading.
* ``schemas`` — Pydantic output schemas for the four optimizer nodes
  (``L1GenerateOutput``, ``L1CritiqueOutput``, ``L2ContextOutput``,
  ``L3PlanOutput``, ``VariantEvidenceGrounding``, etc.).
* ``pipeline.json`` — the optimizer pipeline definition (same shape as
  a backend ``pipeline.json``; powers self-optimization).
"""
