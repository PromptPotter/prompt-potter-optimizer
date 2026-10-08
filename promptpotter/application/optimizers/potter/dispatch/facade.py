"""`DispatchHub` façade + ``build_bundle`` + load-time template validation. ``bundle.py`` stays
``Cycle``-free, so the ``Cycle`` knot lives here in the snapshot path."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import sys
from collections.abc import Iterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.llm_call import InjectionBreakdown
from promptpotter.application.optimizer_manifest import bound_inner_optimizer
from promptpotter.application.optimizers import other_optimizer_packages
from promptpotter.application.optimizers.potter.dispatch import compose
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    OPTIMIZER_DISCRETIONARY_CHARS,
    ArmDigest,
    CycleSlice,
    InjectionBundle,
    Item,
    RoundDigest,
)
from promptpotter.application.optimizers.potter.dispatch.compose import (
    SECTION_SEP,
)
from promptpotter.application.optimizers.potter.dispatch.compose import (
    PanelCoverage as ComposeCoverage,
)
from promptpotter.application.optimizers.potter.dispatch.compose import (
    select as compose_select,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    injection_table,
    renderer_modules,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    L1_LAYOUT_SLOTS,
    NODE_LAYOUTS,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    load_optimizer_prompt,
    node_layout,
)
from promptpotter.application.optimizers.potter.escalation.state import (
    exploration_budget,
    l1_stall_depth,
)
from promptpotter.application.optimizers.potter.knobs import potter_knobs
from promptpotter.application.optimizers.potter.records import PotterRoundState
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.domain import ruler
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, PromptTemplate
from promptpotter.infrastructure.llm.telemetry import (
    emit_round_warning,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.shared.errors import PromptCompositionError
from promptpotter.shared.hashing import module_source_digest, optimizer_prompt_shapers

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizers.potter.state import PotterState
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
    # A separator is rendered BETWEEN items, so the first carries none.
    total = sum(len(i.text) for i in items) + len(SECTION_SEP) * (len(items) - 1)
    if total <= cap:
        return items
    kept: list[Item] = []
    used = 0
    for item in items:
        step = len(item.text) + (len(SECTION_SEP) if kept else 0)
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

    @property
    def breakdown(self) -> InjectionBreakdown:
        """Both of ``fill``'s channels — the layout walk and the prose tokens — as the ledger's
        start record carries them."""
        shown = {**self.rendered, **self.injection_vars}
        return InjectionBreakdown(
            chars={name: len(text) for name, text in shown.items() if text},
            dropped={name: c.dropped for name, c in self.coverage.items() if c.dropped > 0},
            silent=tuple(sorted(name for name, c in self.coverage.items() if c.produced == 0)),
        )


class InjectionRenderError(PromptCompositionError):
    """Renderer raised — programmer mistake. Chains the original via ``raise … from``."""

    def __init__(self, name: str, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(f"injection {name!r} renderer raised {type(cause).__name__}: {cause}")


class MandatoryPanelStarvedError(PromptCompositionError):
    """A panel the node cannot operate without rendered evidence the composition did not place.

    Halts rather than shipping the prompt: a node handed no subject still answers, confidently, and
    nothing downstream can tell that apart from a node that was."""

    def __init__(self, node: str, panels: list[str]) -> None:
        self.node = node
        self.panels = panels
        super().__init__(
            f"{node}: mandatory panel(s) {panels} rendered but were not placed. A mandatory panel "
            f"is admitted whatever it costs, so reaching here means the allocator was bypassed."
        )


class DispatchHub:
    @staticmethod
    def render_items(name: str, bundle: InjectionBundle) -> list[Item]:
        """One injection's placeable items. The ``char_cap`` backstop applies only where the
        composition cannot thin — raises become ``InjectionRenderError`` (halts with
        ``StopReason.RENDER_ERROR``)."""
        sig = injection_table().get(name)
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
        bundle: InjectionBundle,
        *,
        node: str,
    ) -> FilledPrompt:
        """Fill *node*'s layout, then resolve any injection token left in non-layout prose — two
        channels, one call. ``rendered`` is what the node was actually SHOWN, which is the smaller set.

        The layout is RESOLVED here rather than passed in: it is a function of the node and the
        cycle's memory (`node_layout`), so no caller can hand a node another node's panel set.
        *node* also names the discretionary allowance this composition must fit, and its mandatory
        rail."""
        table = injection_table()
        layout = node_layout(node, bundle.memory)
        order = layout.all_placeholders()
        items = {name: DispatchHub.render_items(name, bundle) for name in order}
        budget = OPTIMIZER_DISCRETIONARY_CHARS.get(node, _NO_CEILING)
        # Which panels may be thinned is a property of what they CARRY, so it is asked of the kind
        # each signal already declares rather than kept as a second list here.
        whole = frozenset(n for n in order if (sig := table.get(n)) and not sig.kind.divisible)
        mandatory = NODE_LAYOUTS[node].mandatory
        rendered, coverage = compose_select(items, order, budget, exempt=whole, mandatory=mandatory)
        # `l1_layout_missing_mandatory` (`dispatch/layout.py`) guards these against L2 EXCISING
        # them; this is the same promise's other half, against the composition refusing them.
        if starved := sorted(
            n for n in mandatory if coverage[n].produced and not coverage[n].placed
        ):
            raise MandatoryPanelStarvedError(node, starved)

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
            name: DispatchHub.render(name, bundle) for name in remaining if name in table
        }
        return FilledPrompt(filled, injection_vars, rendered, coverage)


@contextlib.contextmanager
def _no_round_warnings() -> Iterator[None]:
    """Unbind the cycle ledger for the duration — a probe render must not emit. Otherwise the
    operator reads a fresh overrun warning about a truncation that happened rounds ago."""
    token = set_cycle_ledger(None)
    try:
        yield
    finally:
        reset_cycle_ledger(token)


def _silent_l1_panels(bundle: InjectionBundle) -> frozenset[str]:
    with _no_round_warnings():
        return frozenset(
            name
            for name in NODE_LAYOUTS["l1_generate"].possible
            if not DispatchHub.render_items(name, bundle)
        )


def node_packages(bundle: InjectionBundle) -> dict[str, str]:
    """Fingerprint the information package every optimizer node would be handed. **Compare two of
    these, never one against a stored value** — an absolute fingerprint cannot be reproduced later."""
    out: dict[str, str] = {}
    with _no_round_warnings():
        for node in NODE_LAYOUTS:
            filled = DispatchHub.fill(load_optimizer_prompt(node), bundle, node=node)
            payload = json.dumps(
                [filled.template.render(), sorted(filled.injection_vars.items())],
                ensure_ascii=False,
            )
            out[node] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return out


def _arm_digests(latest_round: RoundResult) -> tuple[ArmDigest, ...]:
    """This round's arms, narrowed. Ranked as the round ranked them, so a panel quoting "the
    leader" and the election never disagree about which arm that was."""
    return tuple(
        ArmDigest(
            label=c.label,
            mean_fitness_ci_lo=c.mean_fitness_ci_lo,
            mean_fitness_ci_hi=c.mean_fitness_ci_hi,
            scored_samples=c.scored_samples,
            expected_samples=c.expected_samples,
            outcome=c.outcome,
            gate=c.elimination_context.get("gate"),
        )
        for c in latest_round.candidate_scores
    )


def _sample_ids(round_result: RoundResult) -> frozenset[Any]:
    return frozenset(sid for r in round_result.results if (sid := r.get("sample_id")) is not None)


def build_bundle(cycle: Cycle, state: PotterState) -> InjectionBundle:
    """Snapshot cycle state for one optimizer LLM call. The round under render is the cycle's last
    on every node: the bench folds a round in before any node reads it."""
    *prior, latest_round = cycle.rounds
    latest_state = latest_round.optimizer_state.payload_as(PotterRoundState)
    health = latest_round.health

    current_sp = cycle.tracking.current_sp
    current_pp = current_sp.pipeline_params if current_sp is not None else None
    opt = cycle.config.optimization
    formula, _ = resolve_cell_formula(
        cycle.session.scoring.scorer_cell_formula, cycle.session.pipeline_schema
    )
    knobs = potter_knobs(cycle.optimizer)
    esc = state.escalation
    stall_depth = l1_stall_depth(cycle.rounds)
    cs = CycleSlice(
        round_num=latest_round.round + 1,
        l1_stall_depth=stall_depth,
        l2_round=esc.l2_round,
        l2_stall_count=esc.l2_stall_count,
        l3_round=esc.l3_round,
        l3_stall_count=esc.l3_stall_count,
        exploration_budget=exploration_budget(stall_depth, knobs.escalation.l1_patience).value,
        pipeline_params=dict(current_pp) if current_pp else {},
        composite_formula=formula,
        # The SAME predicate the sampler branches on, evaluated once. A cold ruler forces the
        # frozen prefix however the knob is set, so the knob alone would misreport round 0.
        subset_mode=(
            "adaptive"
            if knobs.adaptive_queue.per_round_resubset and cycle.difficulty.ruler is not None
            else "frozen"
        ),
        sp_budget_round=knobs.adaptive_queue.sp_budget_round,
        max_rounds=opt.max_rounds,
        spend_budget_usd=opt.spend_budget_usd,
        spend_used_usd=cycle.session.control.spend_used_usd(),
    )

    # Trajectory pair: frozen origin hits + the live cumulative frontier. The frontier ships
    # WHOLE — the failure panels take the misses out of it themselves, and `answer_distribution`
    # needs the hits to see a pipeline that has collapsed onto a single label.
    origin_per_sample = list(cycle.origin_round.results)
    trajectory_results = list(cycle.tracking.current_results)
    # Read off the view rather than `latest_round.ability`: the two counts beside the reading are
    # the stamp's own operands, and no round banks them.
    frontier = cycle.difficulty.frontier(trajectory_results)

    bundle = InjectionBundle(
        opt_sp=cycle.opt_sp,
        memory=state.memory,
        framing=cycle.framing,
        pipeline_schema=cycle.session.pipeline_schema,
        cycle_slice=cs,
        digest=RoundDigest(
            diagnostics=latest_round.diagnostics,
            critique=latest_state.critique,
            l1_yield=latest_state.l1_yield,
            node_failure_rates=health.node_failure_rates if health else {},
            latest_sample_ids=_sample_ids(latest_round),
            prev_sample_ids=_sample_ids(prior[-1]) if prior else frozenset(),
            composite_fitness=latest_round.composite_fitness,
            evaluators=dict(latest_round.evaluators),
            ability=frontier.ability,
            unlinked=frontier.unlinked,
            pinned_share=frontier.pinned_share,
            arms=_arm_digests(latest_round),
        ),
        axes=state.axes(cycle),
        origin_per_sample=origin_per_sample,
        trajectory_results=trajectory_results,
        ruler=cycle.difficulty.ruler,
        measured_rounds=list(cycle.rounds),
        prompt_block_catalogue=knobs.l1_generate.prompt_block_catalogue,
        earned_blocks=state.earned_blocks,
        rebase_capability=knobs.escalation.rebase_capability,
        terminate_capability=knobs.escalation.terminate_capability,
        schema_field_rename=knobs.l1_generate.schema_field_rename,
        measured_unit=cycle.session.backend_client.measured_unit,
        prompt_delivery=cycle.session.backend_client.prompt_delivery(cycle.session.pipeline_params),
        is_origin_round=latest_round is cycle.origin_round,
        demo_pool=cycle.session.scoring.require_partition().demo,
        shot_k_max=knobs.l1_generate.k_max,
        inner_optimizer=bound_inner_optimizer(),
    )
    return replace(bundle, silent_l1_panels=_silent_l1_panels(bundle))


def fingerprinted_modules() -> tuple[ModuleType, ...]:
    """The dispatch modules whose source shapes an optimizer prompt, in digest order; outside them
    a definition says so itself (``shapes_optimizer_prompt``). The panels' text is code, so it sits
    outside the manifest half of potter's ``Treatment``; its estimator-side twin is
    ``connectors/promptpotter.py::measurement_modules``.

    ``compose`` is hashed beside the renderers because it decides which panels a prompt receives AT
    ALL, and this module because it picks the allowance and derives the mandatory/exempt sets
    ``compose`` is handed; ``bundle``, whose constants decide how much of a panel a prompt
    receives, marks itself. A module that shapes the
    prompt and is not hashed here pools corpora the fingerprint exists to keep apart — which is why
    the renderer half is WALKED rather than listed, and why what a move costs is counted at the mint
    (``jobs/mint.py::_warn_on_novel_instrument``) rather than pinned as a name census.

    ``domain.ruler`` because ``theta_caveat`` and the two collapse thresholds decide whether the
    ``confounds`` panel says a round's θ is ability at all — a verdict the served reading and the
    panel share, so it shapes the prompt from outside this package.

    Held here because this module imports every other member, and the registry cannot import it.
    """
    return (compose, sys.modules[__name__], ruler, *renderer_modules())


@functools.cache
def injection_source_digest(*measured: ModuleType) -> str:
    """*measured* are the modules the estimator digest hashes beside this one: the scan counts
    their names as hashed, and leaves their own reads to that digest."""
    modules = fingerprinted_modules()
    shapers = optimizer_prompt_shapers(
        modules, covered=measured, foreign=other_optimizer_packages(__name__)
    )
    return module_source_digest(*modules, *shapers)


__all__ = [
    "DispatchHub",
    "InjectionRenderError",
    "MandatoryPanelStarvedError",
    "build_bundle",
    "fingerprinted_modules",
    "injection_source_digest",
    "node_packages",
]
