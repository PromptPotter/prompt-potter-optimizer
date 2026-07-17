"""Injection renderers — uniform ``(InjectionBundle) -> str`` signature, layer-agnostic.

Per-layer specialisation is the kind of complexity the dispatch hub
exists to remove. To add an input to any prompt, add an
``_Injection`` entry to ``INJECTIONS`` (in :mod:`registry`) and write
its renderer in the matching module below. Anything else is drift.

Module map:

* :mod:`wounds` — self-healing wound channels (validation failures,
  runtime failures, L2/L3 guard breaches).
* :mod:`catalogues` — discoverability menus (pipeline-param search
  space, L1 signal placeholders).
* :mod:`panels` — per-round diagnostics + cross-cycle archive memory.
* :mod:`layer_state` — layer narrative state (plan, task context,
  critique, current prompt) + L2-authored supplemental rules.
* :mod:`registry` — the ``INJECTIONS`` dict.

Callers import the leaf they need (``injections.registry`` for
:data:`INJECTIONS` / :func:`citable_fields`); this ``__init__`` re-exports
nothing.
"""
