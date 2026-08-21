"""`DispatchHub` façade + ``build_bundle`` + load-time template validation. ``bundle.py`` stays
``Cycle``-free, so the ``Cycle`` knot lives here in the snapshot path."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.application.knobs import check_couplings
from promptpotter.application.optimization.dispatch.bundle import (
    ArmReading,
    CycleSlice,
    InjectionBundle,
    Item,
    RoundDigest,
)
from promptpotter.application.optimization.dispatch.compose import (
    SECTION_SEP,
)
from promptpotter.application.optimization.dispatch.compose import (
    PanelCoverage as ComposeCoverage,
)
from promptpotter.application.optimization.dispatch.compose import (
    select as compose_select,
)
from promptpotter.application.optimization.dispatch.injections.registry import INJECTIONS
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    load_optimizer_prompt,
    resolve_node_layout,
)
from promptpotter.application.scoring.evaluators import resolve_round_formula
from promptpotter.config.settings import OPTIMIZER_PROMPT_BUDGET_CHARS
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

# No node ceiling applies — the notebook/preview path. Selection still runs, so there is ONE
# composition path rather than a second "place everything" branch that could drift from it.
_NO_CEILING = 1 << 30


def _cap_runaway(name: str, items: list[Item], cap: int) -> list[Item]:
    """The ``char_cap`` backstop, for the panels the composition places WHOLE.

    A divisible panel needs none — the composition thins it to whatever the ceiling affords. An
    indivisible one is placed whole or dropped whole, so a runaway takes the second branch and
    vanishes silently. Drops trailing items until it fits, slicing the first only if even that
    overruns. Nothing reachable here is fenced: dataset content rides MEASUREMENT panels, and
    every one of those is divisible.
    """
    total = sum(len(i.text) + len(SECTION_SEP) for i in items)
    if total <= cap:
        return items
    kept: list[Item] = []
    used = 0
    for item in items:
        step = len(item.text) + len(SECTION_SEP)
        if used + step > cap:
            break
        kept.append(item)
        used += step
    if not kept:
        kept = [Item(items[0].text[:cap] + "…", items[0].trusted)]
    logger.warning(
        "injection %r rendered %d chars over its %d-char backstop — %d item(s) dropped",
        name,
        total - cap,
        cap,
        len(items) - len(kept),
    )
    emit_round_warning(
        kind="injection_budget_overrun",
        severity="warning",
        message=(
            f"Optimizer prompt section {name!r} ran {total - cap} chars over its {cap}-char "
            f"runaway backstop — {len(items) - len(kept)} item(s) did not reach the LLM. This is "
            "a state panel, so the budget could not thin it; the size itself is the anomaly."
        ),
        detail={
            "injection": name,
            "rendered_chars": total,
            "cap": cap,
            "items_dropped": len(items) - len(kept),
        },
    )
    return kept


class FilledPrompt(NamedTuple):
    """What one node's composition produced. ``rendered`` is what the node was actually SHOWN —
    after selection, not before — so the ledger's breakdown and the prompt cannot disagree."""

    template: PromptTemplate
    injection_vars: dict[str, str]
    rendered: dict[str, str]
    coverage: dict[str, ComposeCoverage]


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
    def render_items(name: str, bundle: InjectionBundle) -> list[Item]:
        """One injection's placeable items. The ``char_cap`` backstop applies only where the
        composition cannot thin — raises become ``InjectionRenderError`` (halts with
        ``StopReason.RENDER_ERROR``)."""
        sig = INJECTIONS.get(name)
        if sig is None:
            raise KeyError(f"Unknown signal: {name}")
        try:
            items = [i for i in sig.render(bundle) if i.text]
        except Exception as exc:
            raise InjectionRenderError(name, exc) from exc
        if items and sig.char_cap is not None and not sig.kind.divisible:
            items = _cap_runaway(name, items, sig.char_cap)
        return items

    @staticmethod
    def render(name: str, bundle: InjectionBundle) -> str:
        """The same items, composed to text under no ceiling — for the prose-token channel and the
        node previews. Routed through selection rather than joined here, so a panel's fencing and
        its blank-line boundaries have one implementation however it is reached."""
        items = DispatchHub.render_items(name, bundle)
        text, _ = compose_select({name: items}, [name], _NO_CEILING)
        return text[name]

    @staticmethod
    def fill(
        template: PromptTemplate,
        layout: L1Layout,
        bundle: InjectionBundle,
        *,
        node: str | None = None,
    ) -> FilledPrompt:
        """Fill a node's layout, then resolve any injection token left in non-layout prose — two
        channels, one call. ``rendered`` is what the node was actually SHOWN, which is the smaller set.

        *node* names the ceiling this composition must fit; without one the preview budget applies
        and every item is placed. Selection runs either way, so a panel's fencing, its boundaries
        and its "showed N of M" line have one implementation however the prompt was reached."""
        order = layout.all_placeholders()
        items = {name: DispatchHub.render_items(name, bundle) for name in order}
        # The ceiling is on the COMPOSED prompt, so the static template is already spent before a
        # single item is placed.
        ceiling = OPTIMIZER_PROMPT_BUDGET_CHARS.get(node or "")
        budget = _NO_CEILING if ceiling is None else max(0, ceiling - len(template.render()))
        # Which panels may be thinned is a property of what they CARRY, so it is asked of the kind
        # each signal already declares rather than kept as a second list here.
        whole = frozenset(n for n in order if (sig := INJECTIONS.get(n)) and not sig.kind.divisible)
        rendered, coverage = compose_select(items, order, budget, exempt=whole)

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
        return FilledPrompt(filled, injection_vars, rendered, coverage)


def injection_char_counts(
    rendered: dict[str, str], injection_vars: dict[str, str]
) -> dict[str, int]:
    """Per-signal rendered size, for the ledger's start record — the composition behind
    ``prompt_chars``. Reads BOTH of ``fill``'s channels (the layout walk and the prose tokens)."""
    return {name: len(text) for name, text in {**rendered, **injection_vars}.items() if text}


def injection_coverage_counts(coverage: dict[str, ComposeCoverage]) -> dict[str, int]:
    """Sections the budget REFUSED, per panel. The other half of ``injection_chars``, which can
    only ever report what survived: a panel reading 300 chars says nothing about whether that was
    all it had or the tail of it the ceiling could afford."""
    return {name: c.dropped for name, c in coverage.items() if c.dropped > 0}


def injection_silent_panels(coverage: dict[str, ComposeCoverage]) -> list[str]:
    """Layout panels that produced NOTHING this call. `injection_chars` omits them by construction,
    so without this a panel silent in every call of a campaign reads identically to one nobody put
    in the layout."""
    return sorted(name for name, c in coverage.items() if c.produced == 0)


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
            filled, injection_vars, _, _ = DispatchHub.fill(
                load_optimizer_prompt(node), layout, bundle, node=node
            )
            payload = json.dumps(
                [filled.render(), sorted(injection_vars.items())], ensure_ascii=False
            )
            out[node] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return out


def _arm_readings(latest_round: RoundResult | None) -> tuple[ArmReading, ...]:
    """This round's arms, narrowed. Ranked as the round ranked them, so a panel quoting "the
    leader" and the election never disagree about which arm that was."""
    if latest_round is None:
        return ()
    return tuple(
        ArmReading(
            label=c.label,
            theta=c.theta,
            theta_se=c.theta_se,
            mean_fitness_ci_lo=c.mean_fitness_ci_lo,
            mean_fitness_ci_hi=c.mean_fitness_ci_hi,
            scored_samples=c.scored_samples,
            expected_samples=c.expected_samples,
            elimination_stopped=c.elimination_stopped,
            gate=c.elimination_context.get("gate"),
        )
        for c in latest_round.candidate_scores
    )


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
    opt = cycle.config.optimization
    formula, formula_short = resolve_round_formula(
        cycle.session.scoring.scorer_round_formula, cycle.session.pipeline_schema
    )
    spend_used = cycle.session.spend_used
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
            cycle.escalation.l1_stall_count, opt.l1_patience
        ).value,
        pipeline_params=dict(current_pp) if current_pp else {},
        composite_formula=formula,
        composite_formula_short=formula_short,
        # The SAME predicate `l1/execute.py` branches on, evaluated once. A cold ruler forces the
        # frozen prefix however the knob is set, so the knob alone would misreport round 0.
        subset_mode=(
            "adaptive"
            if opt.mechanisms.selection.per_round_resubset and cycle.ruler is not None
            else "frozen"
        ),
        elimination_n_min=opt.elimination_n_min,
        sp_budget_ttest=cycle.config.sp_budget_ttest,
        max_rounds=opt.max_rounds,
        spend_budget_usd=opt.spend_budget_usd,
        spend_used_usd=spend_used() if spend_used is not None else None,
        couplings=tuple((c.name, c.severity, c.consequence) for c in check_couplings(cycle.config)),
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
    # The frontier absorb is about to fit, fit here over the same merge — so the ability the
    # prompt states and the ability the round document banks are one computation.
    theta = cycle.cumulative_theta(trajectory_results)
    # The round before *latest_round*, whichever path we are on: `cycle.rounds[-1]` IS
    # `latest_round` on the generate/L2/L3 path and the round before it on critique. Resolved
    # once here so "did the subset move?" cannot be right on one path and wrong on the other.
    prior = [r for r in cycle.rounds if r is not latest_round]
    prev_sample_ids = frozenset(
        sid for r in (prior[-1].results if prior else []) if (sid := r.get("sample_id")) is not None
    )

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
            prev_sample_ids=prev_sample_ids,
            composite_fitness=latest_round.composite_fitness if latest_round else None,
            evaluators=dict(latest_round.evaluators) if latest_round else {},
            # From the CYCLE, never from *latest_round*: `absorb_round` stamps these four onto the
            # round document only AFTER the critique call, so on that path the round still reads
            # cold. The cycle owns the ruler and absorb copies from it, so this is the same number
            # one step earlier and cannot be right on one path and wrong on the other.
            cumulative_theta=theta[0] if theta else None,
            cumulative_theta_se=theta[1] if theta else None,
            ruler_id=cycle.ruler_id,
            ruler_n=cycle.ruler_n,
            calibration_model=cycle.calibration_model,
            arms=_arm_readings(latest_round),
        ),
        axes=cycle.axes,
        origin_per_sample=origin_per_sample,
        trajectory_results=trajectory_results,
        ruler=cycle.ruler,
        prior_rounds=list(cycle.rounds),
        prompt_block_catalogue=cycle.config.optimization.prompt_block_catalogue,
        earned_blocks=cycle.earned_blocks,
        rebase_capability=cycle.config.optimization.rebase_capability,
        terminate_capability=cycle.config.optimization.terminate_capability,
        schema_field_rename=cycle.config.optimization.schema_field_rename,
        is_origin_round=latest_round is cycle.origin_round,
    )


__all__ = [
    "DispatchHub",
    "InjectionRenderError",
    "build_bundle",
    "injection_char_counts",
    "node_packages",
    "validate_template",
]
