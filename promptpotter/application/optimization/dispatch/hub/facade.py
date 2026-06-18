"""`DispatchHub` façade + ``build_bundle`` + load-time template validation.

* `build_bundle` — snapshot live ``Cycle`` state into a frozen ``InjectionBundle``.
* `fill_l1` — resolve L2-authored `opt_sp.memory.l1_layout` for L1_GENERATE, return modified PromptTemplate.
* `fill_fixed` — walk a fixed template body (L1_CRITIQUE / L2 / L3) → `{name: rendered}` dict.

`validate_template` raises at load time on typos so they don't silently render to empty.

``bundle.py`` stays ``Cycle``-free (frozen types only — renderer tests
construct bundles directly); the ``Cycle`` knot lives here in the
``build_bundle`` snapshot path.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.hub.bundle import (
    CycleSlice,
    InjectionBundle,
    RoundDigest,
)
from promptpotter.application.optimization.dispatch.hub.injections.registry import INJECTIONS
from promptpotter.domain.escalation_signals import exploration_budget
from promptpotter.domain.l1_layout import L1_LAYOUT_SLOTS, L1Layout
from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.domain.results_health import compute_node_failure_rates
from promptpotter.infrastructure.llm.models import emit_round_warning

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

_SECTION_SEP = "\n\n"


def _truncate_to_cap(text: str, cap: int) -> tuple[str, int]:
    """Section-aware truncation for an over-budget injection. Renderers join sections with
    ``\\n\\n`` highest-priority first, so dropping whole sections from the TAIL keeps the head
    and never slices mid-section; a ``[…N section(s) dropped]`` marker records what was cut.
    A lone section still over cap is hard-sliced as the last resort. Returns
    ``(truncated_text, sections_dropped)``."""
    sections = text.split(_SECTION_SEP)
    for keep in range(len(sections) - 1, 0, -1):
        dropped = len(sections) - keep
        marker = f"{_SECTION_SEP}[…{dropped} section(s) dropped]"
        body = _SECTION_SEP.join(sections[:keep])
        if len(body) + len(marker) <= cap:
            return body + marker, dropped
    return sections[0][:cap] + "…", len(sections) - 1


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


class DispatchHub:
    """Static façade around INJECTIONS — pure, stateless."""

    @staticmethod
    def render(name: str, bundle: InjectionBundle) -> str:
        """Render one injection with ``char_cap`` enforcement. Overruns truncate + warn;
        raises become ``InjectionRenderError`` (halts with ``StopReason.RENDER_ERROR``)."""
        sig = INJECTIONS.get(name)
        if sig is None:
            raise KeyError(f"Unknown signal: {name}")
        try:
            text = sig.render(bundle)
        except Exception as exc:
            raise InjectionRenderError(name, exc) from exc
        cap = sig.char_cap
        if cap is not None and len(text) > cap:
            overrun = len(text) - cap
            truncated, dropped = _truncate_to_cap(text, cap)
            logger.warning(
                "injection %r rendered %d chars (cap %d, %d over) — truncating, %d section(s) dropped",
                name,
                len(text),
                cap,
                overrun,
                dropped,
            )
            emit_round_warning(
                kind="injection_budget_overrun",
                severity="warning",
                message=(
                    f"Optimizer prompt section {name!r} ran {overrun} chars over its "
                    f"{cap}-char budget; {dropped} section(s) dropped to fit — some context "
                    "didn't reach the LLM."
                ),
                detail={
                    "injection": name,
                    "rendered_chars": len(text),
                    "cap": cap,
                    "sections_dropped": dropped,
                },
            )
            text = truncated
        return text

    @staticmethod
    def fill_l1(
        template: PromptTemplate,
        layout: L1Layout,
        bundle: InjectionBundle,
    ) -> PromptTemplate:
        """Append layout-driven content to L1's per-slot text. Slots outside L1_LAYOUT_SLOTS pass through;
        remaining ``{{var}}`` are caller extras."""
        rendered = {name: DispatchHub.render(name, bundle) for name in layout.all_placeholders()}

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
        Names not in INJECTIONS skipped (caller fills as extras)."""
        text = template.render()
        expected = set(_PLACEHOLDER_RE.findall(text))
        return {name: DispatchHub.render(name, bundle) for name in expected if name in INJECTIONS}


def build_bundle(
    cycle: Cycle,
    *,
    latest_round: RoundResult | None = None,
) -> InjectionBundle:
    """Snapshot cycle state for one optimizer LLM call. Pass *latest_round* explicitly for L1_CRITIQUE
    (the just-completed round isn't folded into ``cycle.rounds`` until critique fires); L2/L3 omit it."""
    if latest_round is None and cycle.rounds:
        latest_round = cycle.rounds[-1]
    latest_diag = latest_round.diagnostics if latest_round else None
    latest_crit = latest_round.critique if latest_round else None
    # Round 1 has no prior L1 round (origin stays out of ``cycle.rounds``): seed L1
    # with the origin critique so it opens on the real material-vs-process failure
    # signal rather than the round-1 "no critique" path. ``origin_critique`` is None
    # while the origin critique is itself being generated (latest_round passed
    # explicitly there) — so this is a no-op on that call, not a feedback loop.
    if latest_crit is None and not cycle.rounds:
        latest_crit = cycle.origin_critique
    round_num = latest_round.round + 1 if latest_round else 1

    current_sp = cycle.tracking.current_sp
    current_pp = current_sp.pipeline_params if current_sp is not None else None
    cs = CycleSlice(
        round_num=round_num,
        current_accuracy=cycle.tracking.current_accuracy,
        best_accuracy=cycle.tracking.best_accuracy,
        best_round=cycle.tracking.best_round,
        l1_stall_count=cycle.escalation.l1_stall_count,
        l2_round=cycle.escalation.l2_round,
        l2_stall_count=cycle.escalation.l2_stall_count,
        l3_round=cycle.escalation.l3_round,
        l3_stall_count=cycle.escalation.l3_stall_count,
        exploration_budget=exploration_budget(
            cycle.escalation.l1_stall_count, cycle.config.optimization.l1_patience
        ).value,
        pipeline_params=dict(current_pp) if current_pp else {},
    )

    # Trajectory pair: frozen origin hits + live cumulative misses (the cluster nothing has solved).
    origin_per_sample = list(cycle.tracking.origin_per_sample_results)
    trajectory_misses = [r for r in cycle.tracking.current_results if not r.get("hit")]

    return InjectionBundle(
        opt_sp=cycle.opt_sp,
        pipeline_schema=cycle.session.pipeline_schema,
        cycle_slice=cs,
        digest=RoundDigest(
            diagnostics=latest_diag,
            critique=latest_crit,
            node_failure_rates=(
                compute_node_failure_rates(latest_round.results, latest_round.total)
                if latest_round
                else {}
            ),
        ),
        axes=cycle.axes,
        origin_per_sample=origin_per_sample,
        trajectory_misses=trajectory_misses,
        forbidden_axes_strict=cycle.config.optimization.forbidden_axes_strict,
        rebase_capability=cycle.config.optimization.rebase_capability,
        terminate_capability=cycle.config.optimization.terminate_capability,
    )


__all__ = ["DispatchHub", "InjectionRenderError", "build_bundle", "validate_template"]
