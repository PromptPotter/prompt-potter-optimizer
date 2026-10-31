from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.application.bench.llm_call import InjectionBreakdown
from promptpotter.application.bench.node_context import Bounds, Frontier, Lineage, Ruler, Target
from promptpotter.application.optimizers.nodes import AccessKnob, CountKnob
from promptpotter.application.optimizers.potter.dispatch import compose
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    CycleSlice,
    InjectionBundle,
    Item,
    PoolParent,
    RoundDigest,
    l1_target_schema,
)
from promptpotter.application.optimizers.potter.dispatch.compose import (
    SECTION_SEP,
    PanelCoverage,
    select,
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
from promptpotter.application.optimizers.potter.knobs import (
    EscalationKnobs,
    L1GenerateKnobs,
)
from promptpotter.application.optimizers.potter.records import (
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
)
from promptpotter.application.optimizers.variation.members import crossable
from promptpotter.domain import ruler
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, OptimizerPromptTemplate
from promptpotter.domain.optimizer_state import Critique
from promptpotter.domain.pool import BlockPool, DemoPool
from promptpotter.domain.run_standing import rounds_without_advance
from promptpotter.infrastructure.llm.telemetry import (
    emit_round_warning,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.shared.errors import PromptCompositionError
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.nodes import CycleRead, Draw
    from promptpotter.domain.optimizer_state import StatePart
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.pool import Pool, PoolEntry
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

    template: OptimizerPromptTemplate
    injection_vars: dict[str, str]
    rendered: dict[str, str]
    coverage: dict[str, PanelCoverage]

    @property
    def breakdown(self) -> InjectionBreakdown:
        shown = {**self.rendered, **self.injection_vars}
        return InjectionBreakdown(
            chars={name: len(text) for name, text in shown.items() if text},
            dropped={name: c.dropped for name, c in self.coverage.items() if c.dropped > 0},
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
        text, _ = select({name: items}, [name], _NO_CEILING)
        return text[name]

    @staticmethod
    def fill(
        template: OptimizerPromptTemplate,
        bundle: InjectionBundle,
        *,
        node: str,
    ) -> FilledPrompt:
        """The layout is RESOLVED here, never passed in, so no caller can hand a node another node's panel set."""
        table = injection_table()
        layout = node_layout(node, bundle.l2)
        order = layout.all_placeholders()
        items = {name: DispatchHub.render_items(name, bundle) for name in order}
        budget = NODE_LAYOUTS[node].discretionary_chars if node in NODE_LAYOUTS else _NO_CEILING
        whole = frozenset(n for n in order if (sig := table.get(n)) and not sig.kind.divisible)
        mandatory = NODE_LAYOUTS[node].mandatory
        rendered, coverage = select(items, order, budget, exempt=whole, mandatory=mandatory)
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


def _sample_keys(round_result: RoundResult) -> frozenset[str]:
    return frozenset(cell.key for cell in round_result.results)


# What `build_bundle` reads, sees and draws, so every node composing a bundle declares each.
BUNDLE_READS: frozenset[type[StatePart]] = frozenset(
    {L2Memory, L3Memory, Ladder, L1Readout, Critique}
)
# Every bundle shows the pools as `l1_generate` takes them, so L2 lays out what L1 is shown.
BUNDLE_DRAWS: Mapping[type[Pool], Draw] = MappingProxyType(
    {
        BlockPool: AccessKnob(L1GenerateKnobs, "block_access"),
        DemoPool: CountKnob(L1GenerateKnobs, "k_max"),
    }
)
BUNDLE_SEES: frozenset[type[CycleRead]] = frozenset({Lineage, Frontier, Ruler, Bounds, Target})


def _by_field(entries: tuple[PoolEntry, ...]) -> dict[str, tuple[PoolEntry, ...]]:
    out: dict[str, list[PoolEntry]] = {}
    for entry in entries:
        out.setdefault(BlockPool.part(entry), []).append(entry)
    return {field: tuple(held) for field, held in out.items()}


def _recombination(
    lineage: Lineage, schema: PipelineSchema, generate: L1GenerateKnobs
) -> dict[str, Any]:
    """The population's highest-θ individuals as mains and the campaign's other highest-θ measured
    ones, each to `pool_window`, an unread θ below every read one, and the loci a main can take
    from another entry; none where no pair can trade."""
    if not generate.recombine:
        return {}
    ranked = sorted(
        ((i, lineage.theta(i.id)) for i in lineage.measured),
        key=lambda it: (it[1] is not None, it[1] or 0.0),
        reverse=True,
    )
    population = {i.id for i in lineage.population}
    window = generate.pool_window
    mains = [i.id for i, _ in ranked if i.id in population][:window]
    kept = {*mains, *[i.id for i, _ in ranked if i.id not in population][:window]}
    pool = tuple(PoolParent(i, t, main=i.id in population) for i, t in ranked if i.id in kept)
    loci = dict.fromkeys(
        locus
        for main in pool
        if main.main
        for donor in pool
        if donor is not main
        for locus in crossable([main.individual, donor.individual], schema)
    )
    if not loci:
        return {}
    return {
        "recombine": generate.recombine,
        "recombination_host": generate.recombination_host,
        "recombination_pool": pool,
        "recombination_loci": tuple(loci),
    }


def build_bundle(ctx: NodeContext, ladder: Ladder) -> InjectionBundle:
    """The round under render is the cycle's last on every node: the bench folds a round in before any node reads it."""
    lineage, frontier, ruler_read, bounds, target = (
        ctx.read(Lineage),
        ctx.read(Frontier),
        ctx.read(Ruler),
        ctx.read(Bounds),
        ctx.read(Target),
    )
    difficulty = ruler_read.difficulty
    rounds = lineage.rounds
    origin_round = rounds[0]
    *prior, latest_round = rounds
    readout = ctx.recall(L1Readout, latest_round.round)
    health, latest_fold = latest_round.health, latest_round.fold

    escalation = ctx.knobs(EscalationKnobs)
    generate = ctx.knobs(L1GenerateKnobs)
    stall_depth = rounds_without_advance(rounds)
    cs = CycleSlice(
        round_num=latest_round.round + 1,
        l1_stall_depth=stall_depth,
        ladder=ladder,
        exploration_budget=exploration_budget(stall_depth, escalation.l1_patience).value,
        pipeline_params=dict(frontier.parent_point.pipeline_params),
        composite_formula=target.cell_formula,
        matcher=target.matcher,
        budget_rounds=bounds.budget_rounds,
        # Both this cycle's: its share of the campaign's ceiling, and what it has metered of it.
        spend_budget_usd=bounds.spend_cap_usd(),
        spend_used_usd=bounds.spend_used_usd(),
    )

    origin_per_sample = origin_round.results
    trajectory_results = tuple(frontier.frontier_rows)

    schema = l1_target_schema(target.schema)
    bundle = InjectionBundle(
        parent=lineage.parent,
        l2=ctx.slice(L2Memory),
        l3=ctx.slice(L3Memory),
        framing=target.framing,
        pipeline_schema=schema,
        cycle_slice=cs,
        digest=RoundDigest(
            diagnostics=latest_round.diagnostics,
            critique=ctx.recall(Critique, latest_round.round).readout,
            l1_yield=readout.l1_yield,
            l1_parse_failure=readout.l1_parse_failure,
            node_failure_rates=health.node_failure_rates,
            latest_sample_keys=_sample_keys(latest_round),
            prev_sample_keys=_sample_keys(prior[-1]) if prior else frozenset(),
            composite_fitness=None if latest_fold is None else latest_fold.composite_fitness,
            evaluators={} if latest_fold is None else dict(latest_fold.evaluators),
            ability=difficulty.scale.frontier(trajectory_results),
        ),
        axes=ruler_read.axes,
        origin_per_sample=origin_per_sample,
        trajectory_results=trajectory_results,
        ruler=difficulty.ruler,
        measured_rounds=list(rounds),
        block_access=ctx.access(BlockPool),
        blocks=_by_field(ctx.drawn(BlockPool)),
        runtime_failures=tuple(target.runtime_failures()),
        rebase_capability=escalation.rebase_capability,
        terminate_capability=escalation.terminate_capability,
        schema_field_rename=generate.schema_field_rename,
        measured_unit=target.measured_unit,
        prompt_delivery=target.prompt_delivery,
        demo_pool=ctx.drawn(DemoPool),
        demo_split=target.demo_split,
        shot_k_max=generate.k_max,
        slot_of=ruler_read.slot_of,
        pool_window=generate.pool_window,
        inner_cells=target.inner_cells,
        **_recombination(lineage, schema, generate),
    )
    return replace(bundle, silent_l1_panels=_silent_l1_panels(bundle))


def fingerprinted_modules() -> tuple[ModuleType, ...]:
    """In digest order; held here because this module imports every other member and the registry cannot import it."""
    return (compose, sys.modules[__name__], ruler, *renderer_modules())
