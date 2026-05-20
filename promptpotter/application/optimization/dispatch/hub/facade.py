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

import logging
import re

from promptpotter.application.optimization.dispatch.hub.bundle import (
    OPTIMIZER_PROMPT_CHAR_BUDGET,
    InjectionBundle,
    InjectionTier,
)
from promptpotter.application.optimization.dispatch.hub.injections import INJECTIONS
from promptpotter.domain.l1_layout import L1_LAYOUT_SLOTS, L1Layout
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.phases import StopLoop, StopReason

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class InjectionRenderError(Exception):
    """An injection renderer raised — code drift surfaced mid-run.

    A renderer is a pure ``(InjectionBundle) -> str`` function; it raising
    (e.g. ``AttributeError`` because a ``RoundDiagnostics`` field was
    renamed) is a programmer mistake, not an LLM mistake. The round loop
    catches this before its generic ``except Exception`` and halts with
    :attr:`StopReason.RENDER_ERROR` — distinct from ``CRASHED`` so the
    operator sees *a renderer broke*. Carries the injection ``name`` and
    chains the original exception via ``raise … from``.
    """

    def __init__(self, name: str, cause: BaseException) -> None:
        self.injection_name = name
        self.cause = cause
        super().__init__(f"injection {name!r} renderer raised {type(cause).__name__}: {cause}")


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


def _apply_budget(static_chars: int, rendered: dict[str, str]) -> dict[str, str]:
    """Shed whole injections until the composed prompt fits the budget.

    ``static_chars`` is the template's static body length;
    ``rendered`` maps injection name → rendered text. When
    ``static_chars + Σ rendered`` exceeds
    :data:`OPTIMIZER_PROMPT_CHAR_BUDGET`, drop whole injections
    lowest-:class:`InjectionTier`-first (``OPTIONAL`` before ``CORE``),
    largest-first within a tier so the fewest are lost. ``MANDATORY``
    injections are never shed.

    Deterministic and resume-stable — no LLM, no escalation. If the prompt
    is still over budget once every ``OPTIONAL`` and ``CORE`` injection is
    gone, the residual is content L2 cannot heal: this raises
    :class:`StopLoop` with :attr:`StopReason.PROMPT_BUDGET` so the loop
    halts for operator review. Dropped injections become ``""`` — the same
    shape ``fill_l1`` / ``fill_fixed`` already produce for empty signals.
    """
    total = static_chars + sum(len(v) for v in rendered.values())
    if total <= OPTIMIZER_PROMPT_CHAR_BUDGET:
        return rendered

    out = dict(rendered)
    sheddable = sorted(
        (
            name
            for name, text in out.items()
            if text and INJECTIONS[name].tier is not InjectionTier.MANDATORY
        ),
        key=lambda name: (INJECTIONS[name].tier, -len(out[name])),
    )
    dropped: list[str] = []
    for name in sheddable:
        if total <= OPTIMIZER_PROMPT_CHAR_BUDGET:
            break
        total -= len(out[name])
        out[name] = ""
        dropped.append(name)

    if dropped:
        logger.info(
            "dispatch budget: shed %s to fit the %d-char optimizer-prompt budget",
            dropped,
            OPTIMIZER_PROMPT_CHAR_BUDGET,
        )
    if total > OPTIMIZER_PROMPT_CHAR_BUDGET:
        # Case 4 — still over budget with only MANDATORY injections left.
        # The residual is the static template + the parent prompt + the
        # other mandatory injections: content L2 cannot heal. Halt for
        # operator review rather than fire an oversized prompt at the LLM.
        residual = ", ".join(f"{name}={len(text)}" for name, text in sorted(out.items()) if text)
        logger.error(
            "dispatch budget: composed prompt still %d chars after shedding every "
            "OPTIONAL and CORE injection (budget %d) — the residual is content L2 "
            "cannot heal (static template %d + mandatory injections: %s). Halting; "
            "compact the parent prompt (resume --from N) or trim the meta-prompt "
            "template, then resume.",
            total,
            OPTIMIZER_PROMPT_CHAR_BUDGET,
            static_chars,
            residual or "(none)",
        )
        raise StopLoop(StopReason.PROMPT_BUDGET)
    return out


class DispatchHub:
    """Static façade around :data:`INJECTIONS`.

    All three entry points are pure: they read the registry and the
    bundle, produce text or a kwargs dict. The hub itself has no state.
    """

    @staticmethod
    def render(name: str, bundle: InjectionBundle) -> str:
        """Render one injection, enforcing its per-injection ``char_cap``.

        When an LLM-authored injection overruns its ``char_cap`` the text
        is truncated and a warning fires — self-healing for an authoring
        LLM that ignored its stated output budget. Derived injections
        (``char_cap is None``) pass through unchanged; their
        ``*_RENDER_CAP`` row limits already bound them.

        A renderer that *raises* is code drift, not an LLM mistake: it is
        re-raised as :class:`InjectionRenderError` so the round loop can
        halt with :attr:`StopReason.RENDER_ERROR`. The operator escape
        hatch ``bundle.ignore_render_errors`` (``resume
        --ignore-render-errors``) downgrades a raising renderer to a
        warning + empty render so the run continues without it.
        """
        sig = INJECTIONS.get(name)
        if sig is None:
            raise KeyError(f"Unknown signal: {name}")
        try:
            text = sig.render(bundle)
        except Exception as exc:
            if bundle.ignore_render_errors:
                logger.warning(
                    "injection %r renderer raised %s — ignoring per "
                    "--ignore-render-errors; rendering empty",
                    name,
                    type(exc).__name__,
                    exc_info=True,
                )
                return ""
            raise InjectionRenderError(name, exc) from exc
        cap = sig.char_cap
        if cap is not None and len(text) > cap:
            logger.warning(
                "injection %r rendered %d chars (cap %d) — the authoring LLM "
                "overran its output budget; truncating",
                name,
                len(text),
                cap,
            )
            text = text[:cap] + "…"
        return text

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

        Every placeholder is rendered once, then :func:`_apply_budget`
        sheds whole low-tier injections if the composed prompt exceeds
        :data:`OPTIMIZER_PROMPT_CHAR_BUDGET`, before the slots are rebuilt.
        """
        rendered = {name: DispatchHub.render(name, bundle) for name in layout.all_placeholders()}
        static_chars = len(_PLACEHOLDER_RE.sub("", template.render()))
        rendered = _apply_budget(static_chars, rendered)

        update: dict[str, str] = {}
        for slot in L1_LAYOUT_SLOTS:
            static = getattr(template, slot) or ""
            non_empty = [text for p in layout.slot(slot) if (text := rendered[p])]
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

        :func:`_apply_budget` then sheds whole low-tier injections if the
        composed prompt exceeds :data:`OPTIMIZER_PROMPT_CHAR_BUDGET`.
        """
        text = template.render()
        expected = set(_PLACEHOLDER_RE.findall(text))
        rendered = {
            name: DispatchHub.render(name, bundle) for name in expected if name in INJECTIONS
        }
        static_chars = len(_PLACEHOLDER_RE.sub("", text))
        return _apply_budget(static_chars, rendered)


__all__ = ["DispatchHub", "InjectionRenderError", "validate_template"]
