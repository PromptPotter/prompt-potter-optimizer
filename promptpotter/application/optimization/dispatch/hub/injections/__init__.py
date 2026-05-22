"""Injection renderers + the :data:`INJECTIONS` registry.

Every renderer has the uniform signature ``(InjectionBundle) -> str`` and
is layer-agnostic — same render for every layer that subscribes. Per-layer
specialisation is the kind of complexity the dispatch hub exists to remove.

To add an input to any prompt, add an :class:`_Injection` entry to
:data:`INJECTIONS` and write its renderer in the matching module.
Anything else is drift.

Module map:

* :mod:`wounds` — the self-healing wound channels (validation failures,
  runtime failures, L2/L3 guard breaches).
* :mod:`catalogues` — the discoverability menus (pipeline-param search
  space, L1 signal placeholders).
* :mod:`panels` — per-round diagnostics + cross-cycle archive memory.
* :mod:`layer_state` — layer narrative state (plan, task context,
  critique, current prompt) + L2-authored supplemental rules.
* :mod:`registry` — the :data:`INJECTIONS` dict + the registry-aware
  prompt-budget renderer.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.hub.injections.layer_state import (
    format_l1_critique_for_prompt,
)
from promptpotter.application.optimization.dispatch.hub.injections.registry import INJECTIONS

__all__ = ["INJECTIONS", "format_l1_critique_for_prompt"]
