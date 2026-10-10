from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.llm_call import InjectionBreakdown
from promptpotter.application.optimizer_manifest import bound_inner_optimizer
from promptpotter.application.optimizers.potter.dispatch import compose
from promptpotter.application.optimizers.potter.dispatch.bundle import (
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
from promptpotter.application.optimizers.potter.escalation.state import exploration_budget
from promptpotter.application.optimizers.potter.knobs import potter_knobs
from promptpotter.application.optimizers.potter.records import PotterRoundState
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.domain import ruler
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, PromptTemplate
from promptpotter.domain.results import rounds_without_advance
from promptpotter.infrastructure.llm.telemetry import (
    emit_round_warning,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.shared.errors import PromptCompositionError
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.potter.state import PotterState
    from promptpotter.domain.results import RoundResult

logger = logging.getLogger(__name__)

# Selection still runs under no ceiling, so the preview path is the ONE composition path.
_NO_CEILING = 1 << 30


def _cap_runaway(name: str, items: list[Item], cap: int) -> list[Item]:
    """An indivisible panel is placed whole or dropped whole, so a runaway would vanish silently."""
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
    """``rendered`` is what the node was SHOWN, after selection, so the ledger's breakdown and the prompt agree."""

    template: PromptTemplate
    injection_vars: dict[str, str]
    rendered: dict[str, str]
    coverage: dict[str, ComposeCoverage]

    @property
    def breakdown(self) -> InjectionBreakdown:
        shown = {**self.rendered, **self.injection_vars}
        return InjectionBreakdown(
            chars={name: len(text) for name, text in shown.items() if text},
            dropped={name: c.dropped for name, c in self.coverage.items() if c.dropped > 0},
            silent=tuple(sorted(name for name, c in self.coverage.items() if c.produced == 0)),
        )


class InjectionRenderError(PromptCompositionError):
    def __init__(self, name: str, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(f"injection {name!r} renderer raised {type(cause).__name__}: {cause}")


class MandatoryPanelStarvedError(PromptCompositionError):
    """Halts rather than ships: a node handed no subject still answers, confidently, and nothing downstream can tell."""

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
        """Routed through selection, never joined here, so fencing and blank-line boundaries have one implementation."""
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
        """The layout is RESOLVED here, never passed in, so no caller can hand a node another node's panel set."""
        table = injection_table()
        layout = node_layout(node, bundle.memory)
        order = layout.all_placeholders()
        items = {name: DispatchHub.render_items(name, bundle) for name in order}
        budget = NODE_LAYOUTS[node].discretionary_chars if node in NODE_LAYOUTS else _NO_CEILING
        whole = frozenset(n for n in order if (sig := table.get(n)) and not sig.kind.divisible)
        mandatory = NODE_LAYOUTS[node].mandatory
        rendered, coverage = compose_select(items, order, budget, exempt=whole, mandatory=mandatory)
        # The other half of `l1_layout_missing_mandatory`: that one guards against L2 EXCISING them.
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
    """A probe render must not emit: the operator would read a fresh warning about a truncation from rounds ago."""
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
    """Compare two of these, never one against a stored value: an absolute fingerprint cannot be reproduced later."""
    out: dict[str, str] = {}
    with _no_round_warnings():
        for node in NODE_LAYOUTS:
            filled = DispatchHub.fill(load_optimizer_prompt(node), bundle, node=node)
            out[node] = stable_hash(
                [filled.template.render(), sorted(filled.injection_vars.items())]
            )
    return out


def _arm_digests(latest_round: RoundResult) -> tuple[ArmDigest, ...]:
    """Ranked as the round ranked them, so a panel quoting "the leader" and the election never disagree."""
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


def _sample_ids(round_result: RoundResult) -> frozenset[int]:
    return frozenset(cell.sample_id for cell in round_result.results)


def build_bundle(ctx: NodeContext[Any], state: PotterState) -> InjectionBundle:
    """The round under render is the cycle's last on every node: the bench folds a round in before any node reads it."""
    session = state.session
    origin_round = ctx.rounds[0]
    *prior, latest_round = ctx.rounds
    latest_state = latest_round.optimizer_state.payload_as(PotterRoundState)
    health = latest_round.health

    current_sp = ctx.parent_point
    current_pp = current_sp.pipeline_params if current_sp is not None else None
    opt = ctx.config.optimization
    formula, _ = resolve_cell_formula(
        session.scoring.require_scorer().per_cell, session.pipeline_schema
    )
    knobs = potter_knobs(ctx.optimizer)
    stall_depth = rounds_without_advance(ctx.rounds)
    cs = CycleSlice(
        round_num=latest_round.round + 1,
        l1_stall_depth=stall_depth,
        ladder=state.escalation.ladder,
        exploration_budget=exploration_budget(stall_depth, knobs.escalation.l1_patience).value,
        pipeline_params=dict(current_pp) if current_pp else {},
        composite_formula=formula,
        # A cold ruler forces the frozen prefix however the knob is set, so the knob alone misreports round 0.
        subset_mode=(
            "adaptive"
            if knobs.adaptive_queue.per_round_resubset and ctx.difficulty.ruler is not None
            else "frozen"
        ),
        sp_budget_round=knobs.adaptive_queue.sp_budget_round,
        max_rounds=opt.max_rounds,
        spend_budget_usd=opt.ceiling.usd,
        spend_used_usd=session.control.spend_used_usd(),
    )

    origin_per_sample = origin_round.results
    trajectory_results = tuple(ctx.parent_rows)
    # Off the view, never `latest_round.ability`: no round banks the two counts beside the reading.
    frontier = ctx.difficulty.frontier(trajectory_results)

    bundle = InjectionBundle(
        opt_sp=ctx.parent,
        memory=state.memory,
        framing=ctx.framing,
        pipeline_schema=session.pipeline_schema,
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
        axes=state.axes(ctx.sample_index),
        origin_per_sample=origin_per_sample,
        trajectory_results=trajectory_results,
        ruler=ctx.difficulty.ruler,
        measured_rounds=list(ctx.rounds),
        prompt_block_catalogue=knobs.l1_generate.prompt_block_catalogue,
        earned_blocks=state.earned_blocks,
        rebase_capability=knobs.escalation.rebase_capability,
        terminate_capability=knobs.escalation.terminate_capability,
        schema_field_rename=knobs.l1_generate.schema_field_rename,
        measured_unit=session.backend_client.measured_unit,
        prompt_delivery=session.backend_client.prompt_delivery(session.pipeline_params),
        is_origin_round=latest_round is origin_round,
        demo_pool=ctx.demo_pool,
        shot_k_max=knobs.l1_generate.k_max,
        inner_optimizer=bound_inner_optimizer(),
    )
    return replace(bundle, silent_l1_panels=_silent_l1_panels(bundle))


def fingerprinted_modules() -> tuple[ModuleType, ...]:
    """In digest order; held here because this module imports every other member and the registry cannot import it."""
    return (compose, sys.modules[__name__], ruler, *renderer_modules())


__all__ = [
    "DispatchHub",
    "InjectionRenderError",
    "MandatoryPanelStarvedError",
    "build_bundle",
    "fingerprinted_modules",
    "node_packages",
]
