"""`DispatchHub` façade + ``build_bundle`` + load-time template validation.

* `build_bundle` — snapshot live ``Cycle`` state into a frozen ``InjectionBundle``.
* `fill` — fill a node's layout (`NODE_LAYOUTS[node]` floor, or L2-authored for `l1_generate`)
  and resolve any injection tokens left in non-layout prose → `(filled_template, injection_vars)`.
  One path for every optimizer node (was two: `fill_l1` for L1 + `fill_fixed` for the rest).

`validate_template` raises at load time on typos so they don't silently render to empty.

``bundle.py`` stays ``Cycle``-free (frozen types only — renderer tests
construct bundles directly); the ``Cycle`` knot lives here in the
``build_bundle`` snapshot path.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.bundle import (
    CycleSlice,
    InjectionBundle,
    RoundDigest,
)
from promptpotter.application.optimization.dispatch.injections.registry import INJECTIONS
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    load_optimizer_prompt,
    resolve_node_layout,
)
from promptpotter.domain.escalation_signals import exploration_budget
from promptpotter.domain.l1_layout import L1_LAYOUT_SLOTS, NODE_LAYOUTS, L1Layout
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, PromptTemplate
from promptpotter.domain.results_health import compute_node_failure_rates
from promptpotter.infrastructure.llm.telemetry import (
    emit_round_warning,
    reset_cycle_ledger,
    set_cycle_ledger,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult

logger = logging.getLogger(__name__)

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
    "l1_generate": {"n_variants", "citable_fields"},
    "l1_critique": set(),
    "l2_context": set(),
    "l3_plan": set(),
    "checkin": {"consultation_instruction"},
}


def validate_template(name: str, template: PromptTemplate) -> None:
    """Raise KeyError if any ``{{slot}}`` isn't a signal or known extra (typo → silent empty render)."""
    extras = _TEMPLATE_EXTRAS.get(name, set())
    text = template.render()
    referenced = set(TEMPLATE_TOKEN_RE.findall(text))
    unknown = referenced - INJECTIONS.keys() - extras
    if unknown:
        raise KeyError(
            f"Template {name!r} references unknown slot(s): {sorted(unknown)}. "
            f"Add to INJECTIONS (dispatch/injections/registry.py) or to "
            f"_TEMPLATE_EXTRAS[{name!r}] if the slot is a caller-supplied extra."
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
    def fill(
        template: PromptTemplate,
        layout: L1Layout,
        bundle: InjectionBundle,
    ) -> tuple[PromptTemplate, dict[str, str], dict[str, str]]:
        """Fill a node's layout + resolve any injection tokens left in non-layout slots.

        Two rendering channels, one call — every optimizer node routes through here:

        1. **Layout slots** (``L1_LAYOUT_SLOTS``): append the ``layout``-driven injection
           content to each slot's static text (empty static ⇒ the joined content *is*
           the slot). This is the searchable information-flow axis.
        2. **Non-layout slots** (``instruction`` / ``answer_format``): scan the filled body
           and render any remaining ``INJECTIONS`` token into a kwargs dict for
           ``compile_prompt``. The base manifest embeds no such tokens anymore (the capability
           directives ride layout); this is the safety net for an override SET whose prose
           still embeds one — without it the token would render literally. Tokens not in
           ``INJECTIONS`` (caller extras like ``n_variants``; a backend's own ``{{query}}``
           echoed inside ``rendered_prompt``) are left for the caller / backend.

        Returns ``(filled_template, injection_vars, rendered)``; the caller merges its own
        extras onto ``injection_vars`` and passes both to ``run_optimizer_node``. ``rendered``
        is each layout placeholder's text — **what the node was actually shown**, which is a
        different set from what its layout NAMES: a panel with nothing to say renders empty
        and is dropped from the slot. `l1_generate` derives its citation menu from this, so
        it cannot offer a name whose panel is blank; rendering here rather than re-rendering
        at the menu keeps one render per panel per round (a second pass would re-emit any
        `injection_budget_overrun` wound).
        """
        rendered = {name: DispatchHub.render(name, bundle) for name in layout.all_placeholders()}

        update: dict[str, str] = {}
        for slot in L1_LAYOUT_SLOTS:
            static = getattr(template, slot)
            non_empty = [text for p in layout.slot(slot) if (text := rendered[p])]
            if non_empty:
                joined = "\n\n".join(non_empty)
                update[slot] = (static + "\n\n" + joined) if static else joined
            else:
                update[slot] = static
        filled = template.model_copy(update=update)

        remaining = set(TEMPLATE_TOKEN_RE.findall(filled.render()))
        injection_vars = {
            name: DispatchHub.render(name, bundle) for name in remaining if name in INJECTIONS
        }
        return filled, injection_vars, rendered


@contextlib.contextmanager
def _no_round_warnings() -> Iterator[None]:
    """Unbind the cycle ledger for the duration — a probe render must not emit.

    ``DispatchHub.render`` raises an ``injection_budget_overrun`` warning onto the ledger
    when a panel outgrows its cap. That is right when the render is the one the LLM sees
    and wrong when it is a fingerprint probe: the operator would read a fresh warning about
    a truncation that happened rounds ago, once per probed round per pass.
    """
    token = set_cycle_ledger(None)
    try:
        yield
    finally:
        reset_cycle_ledger(token)


def node_packages(bundle: InjectionBundle) -> dict[str, str]:
    """Fingerprint the information package EVERY optimizer node would be handed from *bundle*.

    One hash per node over what :meth:`DispatchHub.fill` produces — the filled template body
    plus the injections it resolved. That is the package: what the node reads to decide with,
    minus the caller's own extras (``n_variants``, the citation menu), which are config rather
    than evidence about the round.

    **Node-agnostic by construction.** It walks :data:`NODE_LAYOUTS`, so a node is covered the
    moment it is registered and nothing here names one. Which layout applies is asked of the
    node's declared ``editor`` — L1's rides the L2-authored ``opt_sp.memory.l1_layout``, every
    other node's resolves through the L4 override channel — the same split
    :func:`resolve_node_layout` enforces. ``checkin`` runs around the loop rather than through
    the injection path, so it is absent from the registry and from this walk.

    **Compare two of these, never one against a stored value.** An absolute fingerprint cannot
    be reproduced in a later process: the AxisIndex digest behind ``axis_memory`` and the
    escalation fold behind ``escalation_panel`` are not reconstructible to a past round, so a
    re-render always differs somewhere and every resume would read as drift. Build both from
    ONE cycle with only the round content changed between them and all of that cancels exactly,
    leaving the difference that was actually asked about.
    """
    out: dict[str, str] = {}
    with _no_round_warnings():
        for node, spec in NODE_LAYOUTS.items():
            layout = (
                bundle.opt_sp.memory.l1_layout if spec.editor != "l4" else resolve_node_layout(node)
            )
            filled, injection_vars, _ = DispatchHub.fill(
                load_optimizer_prompt(node), layout, bundle
            )
            payload = json.dumps(
                [filled.render(), sorted(injection_vars.items())], ensure_ascii=False
            )
            out[node] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return out


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

    # Trajectory pair: frozen origin hits + the live cumulative frontier. The frontier ships
    # WHOLE — the failure panels take the misses out of it themselves, and `answer_distribution`
    # needs the hits to see a pipeline that has collapsed onto a single label.
    origin_per_sample = list(cycle.origin_round.results)
    trajectory_results = list(cycle.tracking.current_results)

    return InjectionBundle(
        opt_sp=cycle.opt_sp,
        pipeline_schema=cycle.session.pipeline_schema,
        cycle_slice=cs,
        digest=RoundDigest(
            diagnostics=latest_diag,
            critique=latest_crit,
            node_failure_rates=(
                compute_node_failure_rates(latest_round.results) if latest_round else {}
            ),
        ),
        axes=cycle.axes,
        origin_per_sample=origin_per_sample,
        trajectory_results=trajectory_results,
        delta_scale=cycle.delta_scale,
        prior_rounds=list(cycle.rounds),
        prompt_block_catalogue=cycle.config.optimization.prompt_block_catalogue,
        earned_blocks=cycle.earned_blocks,
        rebase_capability=cycle.config.optimization.rebase_capability,
        terminate_capability=cycle.config.optimization.terminate_capability,
        schema_field_rename=cycle.config.optimization.schema_field_rename,
    )


__all__ = [
    "DispatchHub",
    "InjectionRenderError",
    "build_bundle",
    "node_packages",
    "validate_template",
]
