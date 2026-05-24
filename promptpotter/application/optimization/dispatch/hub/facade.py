"""`DispatchHub` façade + load-time template validation.

* `fill_l1` — resolve L2-authored `opt_sp.l1_layout` for L1_GENERATE, return modified PromptTemplate.
* `fill_fixed` — walk a fixed template body (L1_CRITIQUE / L2 / L3) → `{name: rendered}` dict.

`validate_template` raises at load time on typos so they don't silently render to empty.
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
    """Renderer raised — programmer mistake. Halts with ``RENDER_ERROR`` (distinct from CRASHED); chains the original via ``raise … from``."""

    def __init__(self, name: str, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(f"injection {name!r} renderer raised {type(cause).__name__}: {cause}")


# Caller-supplied `compile_prompt` extras (not signals). Anything outside `INJECTIONS ∪ extras`
# in a template body is a typo — `validate_template` raises rather than silently dropping it.
_TEMPLATE_EXTRAS: dict[str, set[str]] = {
    "l1_generate": {"n_variants"},
    "l1_critique": set(),
    "l2_context": set(),
    "l3_plan": set(),
    "checkin": {"consultation_instruction"},
}


def validate_template(name: str, template: PromptTemplate) -> None:
    """Raise KeyError if any ``{{slot}}`` isn't a signal or known extra (typo → silent empty render)."""
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
    """Shed injections to fit ``OPTIMIZER_PROMPT_CHAR_BUDGET`` (OPTIONAL → CORE, largest first; MANDATORY never sheds).
    Still over after every OPTIONAL+CORE gone ⇒ ``StopReason.PROMPT_BUDGET`` (L2 can't heal it)."""
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
        # MANDATORY-only residual — halt for operator review, don't fire an oversized prompt.
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
    """Static façade around INJECTIONS — pure, stateless."""

    @staticmethod
    def render(name: str, bundle: InjectionBundle) -> str:
        """Render one injection with ``char_cap`` enforcement. LLM overruns truncate + warn;
        raises become ``InjectionRenderError`` (downgraded to warn + "" when ``bundle.ignore_render_errors``)."""
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
        """Append layout-driven content to L1's per-slot text. Slots outside L1_LAYOUT_SLOTS pass through;
        remaining ``{{var}}`` are caller extras. Budget enforced before slot rebuild."""
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
        """Resolve every ``{{name}}`` via INJECTIONS → kwargs for ``compile_prompt(**hub, **extras)``.
        Names not in INJECTIONS skipped (caller fills as extras). Budget via ``_apply_budget``."""
        text = template.render()
        expected = set(_PLACEHOLDER_RE.findall(text))
        rendered = {
            name: DispatchHub.render(name, bundle) for name in expected if name in INJECTIONS
        }
        static_chars = len(_PLACEHOLDER_RE.sub("", text))
        return _apply_budget(static_chars, rendered)


__all__ = ["DispatchHub", "InjectionRenderError", "validate_template"]
