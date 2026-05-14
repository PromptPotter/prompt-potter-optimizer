"""``DispatchHub`` façade + load-time template validation.

Two rendering paths:

* :meth:`DispatchHub.fill_l1` — resolves L2-authored ``opt_sp.l1_layout``
  for L1_GENERATE. Returns a modified ``PromptTemplate`` whose slots
  have layout-driven content appended; remaining ``{{var}}`` placeholders
  (``n_variants``) are extras filled by L1's caller via ``compile_prompt``.
* :meth:`DispatchHub.fill_fixed` — walks a fixed template's body for
  L1_CRITIQUE / L2 / L3 and produces a ``{name → rendered}`` dict suitable
  for ``compile_prompt(**hub_dict, **extras)``.

:func:`validate_template` closes the silent-drop bug: a typo in a
template body raises at load time rather than rendering to empty.
"""

from __future__ import annotations

import re

from promptpotter.application.optimization.dispatch.hub.bundle import InjectionBundle
from promptpotter.application.optimization.dispatch.hub.injections import INJECTIONS
from promptpotter.domain.l1_layout import L1_LAYOUT_SLOTS, L1Layout
from promptpotter.domain.opt_search_point import PromptTemplate

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


# Per-template names that arrive as caller-supplied ``compile_prompt`` extras
# rather than dispatch-hub signals. Anything outside ``INJECTIONS ∪ extras`` in
# a template body is a typo — :func:`validate_template` raises rather than
# letting :meth:`DispatchHub.fill_fixed` silently drop the placeholder.
_TEMPLATE_EXTRAS: dict[str, set[str]] = {
    "l1_generate": {"n_variants"},
    "l1_critique": set(),
    "l2_context": set(),
    "l3_plan": set(),
    "restructure": {"consultation_instruction"},
}


def validate_template(name: str, template: PromptTemplate) -> None:
    """Raise :class:`KeyError` if any ``{{slot}}`` isn't a signal or known extra.

    Closes the silent-drop bug: :meth:`DispatchHub.fill_fixed` only
    populates ``out[name]`` when ``name in INJECTIONS``, so a typo in a
    template body would render to empty and never surface. Called from
    :func:`promptpotter.application.optimization.dispatch.llm_call.load_optimizer_prompt`
    after every load (Langfuse or local manifest).
    """
    extras = _TEMPLATE_EXTRAS.get(name, set())
    text = template.render()
    referenced = set(_PLACEHOLDER_RE.findall(text))
    unknown = referenced - INJECTIONS.keys() - extras
    if unknown:
        raise KeyError(
            f"Template {name!r} references unknown slot(s): {sorted(unknown)}. "
            f"Add to dispatch_hub.INJECTIONS or to _TEMPLATE_EXTRAS[{name!r}] if "
            "the slot is a caller-supplied extra."
        )


class DispatchHub:
    """Static façade around :data:`INJECTIONS`.

    All three entry points are pure: they read the registry and the
    bundle, produce text or a kwargs dict. The hub itself has no state.
    """

    @staticmethod
    def render(name: str, bundle: InjectionBundle) -> str:
        sig = INJECTIONS.get(name)
        if sig is None:
            raise KeyError(f"Unknown signal: {name}")
        return sig.render(bundle)

    @staticmethod
    def fill_l1(
        template: PromptTemplate,
        layout: L1Layout,
        bundle: InjectionBundle,
    ) -> PromptTemplate:
        """Append layout-driven content to L1's per-slot static text.

        Returns a modified ``PromptTemplate`` whose layout-addressed slots
        carry the rendered placeholder content. ``answer_format`` and any
        other slot not in :data:`L1_LAYOUT_SLOTS` pass through unchanged.
        Remaining ``{{var}}`` placeholders (template-author scalars like
        ``n_variants``) are still filled by ``compile_prompt`` extras.
        """
        update: dict[str, str] = {}
        for slot in L1_LAYOUT_SLOTS:
            static = getattr(template, slot) or ""
            placeholders = layout.slot(slot)
            rendered = [DispatchHub.render(p, bundle) for p in placeholders]
            non_empty = [r for r in rendered if r]
            if non_empty:
                joined = "\n\n".join(non_empty)
                update[slot] = (static + "\n\n" + joined) if static else joined
            else:
                update[slot] = static
        return template.model_copy(update=update)

    @staticmethod
    def fill_fixed(template: PromptTemplate, bundle: InjectionBundle) -> dict[str, str]:
        """Resolve every ``{{name}}`` in the template body via the hub.

        Returns a kwargs dict ready for ``compile_prompt(**hub_dict, **extras)``.
        Names not in :data:`INJECTIONS` are skipped — caller-supplied extras
        fill them, or ``compile_prompt`` will raise on unsubstituted vars.
        """
        text = template.render()
        expected = set(_PLACEHOLDER_RE.findall(text))
        out: dict[str, str] = {}
        for name in expected:
            if name in INJECTIONS:
                out[name] = DispatchHub.render(name, bundle)
        return out


__all__ = ["DispatchHub", "validate_template"]
