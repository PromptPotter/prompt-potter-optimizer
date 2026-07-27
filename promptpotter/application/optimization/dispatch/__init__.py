"""Optimizer LLM info-flow ingress — the single path every optimizer prompt is
composed and dispatched through:

    build_bundle(cycle) → DispatchHub.fill → compile_prompt → LLM

Two prohibitions define this package. No prompt site may read state without going through
:func:`build_bundle` + :class:`DispatchHub` — a sidecar fill path or a per-template
renderer is drift, so extend the registry in place. And every renderer is
layer-agnostic: per-layer specialisation is exactly the complexity this package exists
to remove. To add an input to any prompt, add an injection in :mod:`injections`.
Channels and signal routing: ``docs/developer/dispatch-hub.md``.
"""
