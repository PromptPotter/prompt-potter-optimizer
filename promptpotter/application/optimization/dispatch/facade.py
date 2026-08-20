"""`DispatchHub` façade + ``build_bundle`` + load-time template validation. ``bundle.py`` stays
``Cycle``-free, so the ``Cycle`` knot lives here in the snapshot path."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.bundle import (
    FENCE_CLOSE,
    FENCE_OPEN_PREFIX,
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
from promptpotter.domain.results import merge_known_outcomes
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


def _close_open_fence(body: str) -> str:
    """Re-close a dataset-content fence the cut left open. Renderers fence per section so the
    section-drop path never opens one, but the last-resort mid-text slice below can — and an
    unterminated fence lets untrusted sample text run loose to the end of the prompt, which is
    the one failure here that is a SECURITY hole rather than a lost paragraph. Belongs in the
    backstop, not in each renderer: the slice is what breaks the tag, so the slice repairs it."""
    if body.count(FENCE_OPEN_PREFIX) > body.count(FENCE_CLOSE):
        return body + "\n" + FENCE_CLOSE
    return body


def _truncate_to_cap(text: str, cap: int) -> tuple[str, int]:
    """Section-aware truncation for an over-budget injection: renderers join sections highest-priority
    first, so whole sections drop from the TAIL and nothing is sliced mid-section."""
    sections = text.split(_SECTION_SEP)
    for keep in range(len(sections) - 1, 0, -1):
        dropped = len(sections) - keep
        marker = f"{_SECTION_SEP}[…{dropped} section(s) dropped]"
        body = _SECTION_SEP.join(sections[:keep])
        if len(body) + len(marker) <= cap:
            return _close_open_fence(body) + marker, dropped
    return _close_open_fence(sections[0][:cap]) + "…", len(sections) - 1


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
            # `_truncate_to_cap` has two modes and the operator reads them very differently:
            # whole sections leave the tail, or — when the render is ONE section — it is sliced
            # mid-text. Reporting the second as "0 section(s) dropped" beside "context didn't
            # reach the LLM" states a loss and a no-loss in one sentence, and the reader believes
            # the half that is easier to read.
            lost = (
                f"{dropped} whole section(s) left the tail"
                if dropped
                else "it is one section, so the tail was sliced mid-text"
            )
            emit_round_warning(
                kind="injection_budget_overrun",
                severity="warning",
                message=(
                    f"Optimizer prompt section {name!r} ran {overrun} chars over its "
                    f"{cap}-char budget — {lost}, and that much did not reach the LLM."
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
        """Fill a node's layout, then resolve any injection token left in non-layout prose — two
        channels, one call. ``rendered`` is what the node was actually SHOWN, which is the smaller set."""
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


def injection_char_counts(
    rendered: dict[str, str], injection_vars: dict[str, str]
) -> dict[str, int]:
    """Per-signal rendered size, for the ledger's start record — the composition behind
    ``prompt_chars``. Reads BOTH of ``fill``'s channels (the layout walk and the prose tokens);
    silent panels are omitted, since a zero names nothing a reader can act on."""
    return {name: len(text) for name, text in {**rendered, **injection_vars}.items() if text}


@contextlib.contextmanager
def _no_round_warnings() -> Iterator[None]:
    """Unbind the cycle ledger for the duration — a probe render must not emit. Otherwise the
    operator reads a fresh overrun warning about a truncation that happened rounds ago."""
    token = set_cycle_ledger(None)
    try:
        yield
    finally:
        reset_cycle_ledger(token)


def node_packages(bundle: InjectionBundle) -> dict[str, str]:
    """Fingerprint the information package every optimizer node would be handed. **Compare two of
    these, never one against a stored value** — an absolute fingerprint cannot be reproduced later."""
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
    # `absorb_round` folds a round into `tracking.current_results` only AFTER the critique call,
    # so a node whose prompt opens "Read the measurements above" was handed the pool as of the
    # PREVIOUS round and never its own. Same merge absorb will apply, over a local snapshot —
    # L2/L3 pass no round and re-merge one already absorbed, which replaces rows with themselves.
    latest_results = list(latest_round.results) if latest_round else []
    trajectory_results = merge_known_outcomes(list(cycle.tracking.current_results), latest_results)

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
            latest_sample_ids=frozenset(
                sid for r in latest_results if (sid := r.get("sample_id")) is not None
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
    "injection_char_counts",
    "node_packages",
    "validate_template",
]
