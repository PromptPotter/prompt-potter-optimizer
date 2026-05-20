"""Dispatch hub — single ingress for every optimizer prompt's substituted text.

This package participates in **dispatch** as the single info-ingress to
prompts. Owns the injection registry (:data:`INJECTIONS`), the
:class:`InjectionKind` classification, the typed
:class:`InjectionBundle` per-call state, and the two rendering paths:

* :meth:`DispatchHub.fill_l1` — resolves L2-authored ``opt_sp.l1_layout``
  for L1_GENERATE.
* :meth:`DispatchHub.fill_fixed` — walks a fixed template's body for
  L1_CRITIQUE / L2 / L3 and produces a ``{name → rendered}`` dict.

To add an input to any prompt, add an injection in :mod:`injections`.
Anything else is drift. Every renderer is layer-agnostic — same render
for every layer that subscribes; per-layer specialisation is the kind of
complexity this package exists to remove. The four
:class:`InjectionKind` values split along *origin*, not consumer:

* ``MEASUREMENT`` — raw evidence from L1 candidate runs.
* ``DERIVED`` — computed from measurements.
* ``TRACE`` — narrative state from prior LLM calls.
* ``DIRECTIVE`` — active instructions to a downstream layer.

Out-of-bounds: no prompt site may read state directly without going
through :func:`build_bundle` + :class:`DispatchHub`. Adding a sidecar
fill path or a per-template renderer is drift; extend the registry
in place instead.

Module map:

* :mod:`bundle` — types (``InjectionBundle``, ``CycleSlice``,
  ``RoundDigest``, ``InjectionKind``, ``_Injection``) + format constants
  + the dataset-content fence helper.
* :mod:`injections` — the renderers and the :data:`INJECTIONS` registry.
* :mod:`facade` — :class:`DispatchHub` + :func:`validate_template`.
* :mod:`builder` — :func:`build_bundle` (wires live ``Cycle`` state).
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.hub.builder import build_bundle
from promptpotter.application.optimization.dispatch.hub.bundle import (
    CycleSlice,
    InjectionBundle,
    InjectionKind,
    RoundDigest,
)
from promptpotter.application.optimization.dispatch.hub.facade import (
    DispatchHub,
    InjectionRenderError,
    validate_template,
)
from promptpotter.application.optimization.dispatch.hub.injections import (
    INJECTIONS,
    format_l1_critique_for_prompt,
)

__all__ = [
    "INJECTIONS",
    "CycleSlice",
    "DispatchHub",
    "InjectionBundle",
    "InjectionKind",
    "InjectionRenderError",
    "RoundDigest",
    "build_bundle",
    "format_l1_critique_for_prompt",
    "validate_template",
]
