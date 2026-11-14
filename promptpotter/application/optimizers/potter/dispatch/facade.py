from __future__ import annotations

import contextlib
import functools
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from promptpotter.application.bench.llm_call import FilledPrompt, InjectionBreakdown
from promptpotter.application.bench.node_context import (
    Bounds,
    Calls,
    Frontier,
    Lineage,
    NodeContext,
    Ruler,
    Target,
)
from promptpotter.application.intelligence.indexes.axis import AxisIndex
from promptpotter.application.optimizers.nodes import CycleRead
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    Item,
    PoolParent,
    RecombinationOffer,
    l1_target_schema,
    reelection_roster,
)
from promptpotter.application.optimizers.potter.dispatch.compose import (
    SECTION_SEP,
    select,
)
from promptpotter.application.optimizers.potter.dispatch.injections.registry import (
    injection_table,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    L1_LAYOUT_SLOTS,
    NODE_LAYOUTS,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import node_layout
from promptpotter.application.optimizers.potter.knobs import (
    EscalationKnobs,
    L1GenerateKnobs,
)
from promptpotter.application.optimizers.potter.records import (
    ExplorationBudget,
    L1Readout,
    L2Memory,
    L3Memory,
    Ladder,
    exploration_budget,
)
from promptpotter.application.optimizers.variation.members import Cross, crossable
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.l4.cells import InnerCells
from promptpotter.domain.opt_search_point import Locus, OptSearchPoint
from promptpotter.domain.optimizer_state import Critique, CritiqueReadout, StatePart
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.paired_reading import RoundArm
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
from promptpotter.domain.pool import EARNED_SOURCE, Access, BlockPool, DemoPool, PoolEntry
from promptpotter.domain.records.run_records import RoundWarningRecord
from promptpotter.domain.results import RoundResult
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.ruler import AbilityReading, DeltaRuler
from promptpotter.domain.run_standing import rounds_without_advance
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet, GradedCell
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.value_tree import Delivery
from promptpotter.domain.wounds import RuntimeFailure
from promptpotter.infrastructure.llm.telemetry import append_record, current_round
from promptpotter.infrastructure.run_scope import RunScope, scoped
from promptpotter.shared.errors import PromptCompositionError

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
    append_record(
        RoundWarningRecord(
            kind="injection_budget_overrun",
            message=(
                f"Optimizer prompt section {name!r} ran {total - cap} chars over its {cap}-char "
                f"runaway backstop — {len(items) - len(kept)} item(s) did not reach the LLM. This "
                "is a state panel, so the budget could not thin it; the size itself is the anomaly."
            ),
            round=current_round(),
            detail={
                "injection": name,
                "rendered_chars": total,
                "cap": cap,
                "items_dropped": len(items) - len(kept),
            },
        )
    )
    return kept


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
    def render_items(name: str, view: PotterView) -> list[Item]:
        sig = injection_table().get(name)
        if sig is None:
            raise KeyError(f"Unknown signal: {name}")
        try:
            items = [i for i in sig.render(view) if i.text]
        except Exception as exc:
            raise InjectionRenderError(name, exc) from exc
        if items and sig.char_cap is not None and not sig.kind.divisible:
            items = _cap_runaway(name, items, sig.char_cap)
        return items

    @staticmethod
    def render(name: str, view: PotterView) -> str:
        """Routed through selection, never joined here, so fencing and blank-line boundaries have one implementation."""
        items = DispatchHub.render_items(name, view)
        text, _ = select({name: items}, [name], _NO_CEILING)
        return text[name]

    @staticmethod
    def fill(
        template: OptimizerPromptTemplate,
        view: PotterView,
        *,
        node: str,
    ) -> FilledPrompt:
        """The layout is RESOLVED here, never passed in, so no caller can hand a node another node's panel set."""
        table = injection_table()
        layout = node_layout(node, view.l2, view.ctx.read(Calls).levers(node))
        order = layout.all_placeholders()
        items = {name: DispatchHub.render_items(name, view) for name in order}
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
        remaining = template.model_copy(update=update).slots()
        injection_vars = {
            name: DispatchHub.render(name, view) for name in remaining if name in table
        }
        shown = {**rendered, **injection_vars}
        injections = InjectionBreakdown(
            chars={name: len(text) for name, text in shown.items() if text},
            dropped={name: c.dropped for name, c in coverage.items() if c.dropped > 0},
        )
        return FilledPrompt(update, injection_vars, rendered, injections)


def _no_round_warnings() -> contextlib.AbstractContextManager[RunScope]:
    """A probe render must not emit: the operator would read a fresh warning about a truncation from rounds ago."""
    return scoped(ledger=None)


def _sample_keys(round_result: RoundResult) -> frozenset[str]:
    return frozenset(cell.key for cell in round_result.results)


# What `PotterView` reads, sees and draws, so every node rendering through one declares each.
VIEW_READS: frozenset[type[StatePart]] = frozenset(
    {L2Memory, L3Memory, Ladder, L1Readout, Critique}
)
VIEW_SEES: frozenset[type[CycleRead]] = frozenset({Lineage, Frontier, Ruler, Bounds, Target})


def _by_field(entries: tuple[PoolEntry, ...]) -> dict[str, tuple[PoolEntry, ...]]:
    out: dict[str, list[PoolEntry]] = {}
    for entry in entries:
        out.setdefault(BlockPool.part(entry), []).append(entry)
    return {field: tuple(held) for field, held in out.items()}


class _Recombination(NamedTuple):
    pool: tuple[PoolParent, ...]
    loci: tuple[Locus, ...]


def _recombination(
    lineage: Lineage, schema: PipelineSchema, generate: L1GenerateKnobs
) -> _Recombination:
    """The population's highest-θ individuals as mains and the campaign's other highest-θ measured
    ones, each to `pool_window`, an unread θ below every read one, and the loci a main can take
    from another entry; none where no pair can trade."""
    if not generate.recombine:
        return _Recombination((), ())
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
        for locus in crossable(Cross(main.individual, donor.individual), schema)
    )
    return _Recombination(pool, tuple(loci)) if loci else _Recombination((), ())


class PotterView:
    """What every potter prompt renders from: its node's own reads (`NodeContext`), each taken where
    a renderer first asks, so no snapshot stands between the run's state and the prompt. The round
    under render is the cycle's last on every node: the bench folds a round in before any node reads it."""

    def __init__(self, ctx: NodeContext, ladder: Ladder) -> None:
        self.ctx = ctx
        # The controller's ladder as this fire reads it (`escalation.py::as_read_by`).
        self.ladder = ladder

    # -- the line
    @functools.cached_property
    def parent(self) -> OptSearchPoint:
        return self.ctx.read(Lineage).parent

    @functools.cached_property
    def measured_rounds(self) -> Sequence[RoundResult]:
        """The round under render LAST."""
        return self.ctx.read(Lineage).rounds

    @property
    def _latest(self) -> RoundResult:
        return self.measured_rounds[-1]

    @functools.cached_property
    def round_num(self) -> int:
        return self._latest.round + 1

    @functools.cached_property
    def l1_stall_depth(self) -> int:
        """Closed rounds since the last advance, which no fire resets — never the ladder's pacing count."""
        return rounds_without_advance(self.measured_rounds)

    @functools.cached_property
    def exploration_budget(self) -> str:
        patience = self.ctx.knobs(EscalationKnobs).l1_patience
        return exploration_budget(self.l1_stall_depth, patience).value

    @functools.cached_property
    def l2(self) -> L2Memory:
        return self.ctx.slice(L2Memory)

    @functools.cached_property
    def l3(self) -> L3Memory:
        return self.ctx.slice(L3Memory)

    # -- the round under render
    @property
    def diagnostics(self) -> RoundDiagnostics | None:
        return self._latest.diagnostics

    @functools.cached_property
    def critique(self) -> CritiqueReadout | None:
        return self.ctx.recall(Critique, self._latest.round).readout

    @functools.cached_property
    def _l1_readout(self) -> L1Readout:
        return self.ctx.recall(L1Readout, self._latest.round)

    @property
    def l1_yield(self) -> float | None:
        """`L1Readout.l1_yield`: `None` where nothing was proposed."""
        return self._l1_readout.l1_yield

    @property
    def l1_parse_failure(self) -> str | None:
        """`None` where the round's generation parsed, or made no call."""
        return self._l1_readout.l1_parse_failure

    @property
    def node_failure_rates(self) -> dict[str, float]:
        return self._latest.health.node_failure_rates

    @functools.cached_property
    def latest_sample_keys(self) -> frozenset[str]:
        return _sample_keys(self._latest)

    @functools.cached_property
    def prev_sample_keys(self) -> frozenset[str]:
        prior = self.measured_rounds[:-1]
        return _sample_keys(prior[-1]) if prior else frozenset()

    @property
    def composite_fitness(self) -> float | None:
        fold = self._latest.fold
        return None if fold is None else fold.composite_fitness

    @property
    def evaluators(self) -> dict[str, float]:
        fold = self._latest.fold
        return {} if fold is None else dict(fold.evaluators)

    @functools.cached_property
    def ability(self) -> AbilityReading | None:
        return self.ctx.read(Ruler).difficulty.scale.frontier(self.trajectory_results)

    @functools.cached_property
    def parent_ability(self) -> AbilityReading | None:
        """θ on the parent's own rows: the side of every contrast an edit is judged against."""
        return self.ctx.read(Ruler).difficulty.scale.frontier(
            self.ctx.read(Frontier).parent_own_rows
        )

    # -- the evidence
    @functools.cached_property
    def origin_per_sample(self) -> CellSheet:
        return self.measured_rounds[0].results

    @functools.cached_property
    def trajectory_results(self) -> tuple[GradedCell, ...]:
        """Hits included: a pipeline collapsed onto one label shows only against the labels it is NOT emitting."""
        return tuple(self.ctx.read(Frontier).frontier_rows)

    @functools.cached_property
    def pipeline_params(self) -> PipelineParams:
        return self.ctx.read(Frontier).parent_point.pipeline_params

    @functools.cached_property
    def ruler(self) -> DeltaRuler | None:
        """`None` while the cycle's locked ruler is cold."""
        return self.ctx.read(Ruler).difficulty.ruler

    @functools.cached_property
    def axes(self) -> AxisIndex | None:
        return self.ctx.read(Ruler).axes

    @property
    def slot_of(self) -> Mapping[str, int]:
        """Each sample's slot in the cycle's bank: the `[#id]` a panel marks a row by."""
        return self.ctx.read(Ruler).slot_of

    @functools.cached_property
    def runtime_failures(self) -> tuple[RuntimeFailure, ...]:
        """Sibling forks' and this cycle's, each distinct config once (`Cycle.runtime_failures`)."""
        return tuple(self.ctx.read(Target).runtime_failures())

    # -- the target
    @functools.cached_property
    def framing(self) -> TaskDecomposition:
        return self.ctx.read(Target).framing

    @functools.cached_property
    def pipeline_schema(self) -> PipelineSchema:
        """`l1_target_schema`'s: what any potter node may say L1 can write."""
        target = self.ctx.read(Target)
        return l1_target_schema(target.schema)

    @property
    def prompt_nodes(self) -> list[PipelineNode]:
        return self.pipeline_schema.prompt_nodes()

    @functools.cached_property
    def composite_formula(self) -> str:
        return self.ctx.read(Target).cell_formula

    @functools.cached_property
    def matcher(self) -> str | None:
        return self.ctx.read(Target).matcher

    @functools.cached_property
    def measured_unit(self) -> MeasuredUnit:
        return self.ctx.read(Target).measured_unit

    @functools.cached_property
    def prompt_delivery(self) -> Delivery:
        return self.ctx.read(Target).prompt_delivery

    @functools.cached_property
    def demo_split(self) -> tuple[Sample, ...]:
        """Resolves the shots an individual already holds."""
        return self.ctx.read(Target).demo_split

    @functools.cached_property
    def inner_cells(self) -> InnerCells | None:
        """`None` off the L4 recursion; each outer node's manifest is `InnerCells.owners`'."""
        return self.ctx.read(Target).inner_cells

    # -- the bounds
    @property
    def admission(self) -> float | None:
        """The selector's own promotion bar (`Bounds.admission`); ``None``: it promotes on no θ bar."""
        return self.ctx.read(Bounds).admission

    @property
    def budget_rounds(self) -> int | None:
        return self.ctx.read(Bounds).budget_rounds

    @functools.cached_property
    def spend_budget_usd(self) -> float | None:
        """This cycle's share of the campaign's ceiling."""
        return self.ctx.read(Bounds).spend_cap_usd()

    @functools.cached_property
    def spend_used_usd(self) -> float | None:
        """What the cap has counted — a FLOOR while unpriced tokens are outstanding, never "spent"."""
        return self.ctx.read(Bounds).spend_used_usd()

    # -- the pools
    @functools.cached_property
    def block_access(self) -> Access:
        return self.ctx.access(BlockPool)

    @functools.cached_property
    def blocks(self) -> Mapping[str, tuple[PoolEntry, ...]]:
        """The block pool's entries by field, in its draw order; none under `off`."""
        return _by_field(self.ctx.drawn(BlockPool))

    @functools.cached_property
    def demo_pool(self) -> tuple[Sample, ...]:
        """What a variant's shots may be drawn from."""
        return self.ctx.drawn(DemoPool)

    # -- the knobs
    @property
    def schema_field_rename(self) -> bool:
        return self.ctx.knobs(L1GenerateKnobs).schema_field_rename

    @property
    def shot_k_max(self) -> int:
        """0 silences the shot menu as an empty `demo_pool` does, withdrawing the `shots` slot."""
        return self.ctx.knobs(L1GenerateKnobs).k_max

    @property
    def pool_window(self) -> int:
        """The most entries one call shows of each pool it reads (`L1GenerateKnobs.pool_window`)."""
        return self.ctx.knobs(L1GenerateKnobs).pool_window

    @functools.cached_property
    def _recombination(self) -> _Recombination:
        return _recombination(
            self.ctx.read(Lineage), self.pipeline_schema, self.ctx.knobs(L1GenerateKnobs)
        )

    @property
    def recombine(self) -> int:
        """The children a host call may name; 0 where no pair can trade."""
        return self.ctx.knobs(L1GenerateKnobs).recombine if self.recombination_pool else 0

    @property
    def recombination_pool(self) -> tuple[PoolParent, ...]:
        return self._recombination.pool

    @property
    def recombination_loci(self) -> tuple[Locus, ...]:
        return self._recombination.loci

    def recombines(self, host: str) -> bool:
        """Whether *host*'s call is shown the pool and offered the slot."""
        generate = self.ctx.knobs(L1GenerateKnobs)
        return bool(self.recombination_pool) and generate.recombination_host == host

    def recombination_offer(self, host: str) -> RecombinationOffer | None:
        """``None`` withdraws the slot."""
        if not self.recombines(host):
            return None
        return RecombinationOffer(
            self.recombine,
            tuple(p.id for p in self.recombination_pool if p.main),
            tuple(p.id for p in self.recombination_pool),
            tuple(locus.name for locus in self.recombination_loci),
        )

    @property
    def rebase_offered(self) -> bool:
        """Whether the fork directive renders: a TIGHT budget builds on the parent, never forks."""
        return (
            self.ctx.knobs(EscalationKnobs).rebase_capability
            and self.exploration_budget != ExplorationBudget.TIGHT
        )

    @property
    def reelect_offer(self) -> dict[str, RoundArm]:
        """What `fork_proposal.stand_on` may name, by label; empty withdraws the panel and the field."""
        if not (self.rebase_offered and self.ctx.knobs(EscalationKnobs).reelect_capability):
            return {}
        return reelection_roster(self.measured_rounds, parent=self.parent.id)

    @property
    def terminate_offer(self) -> str | None:
        """The evidence-starved node a stop must name; ``None`` withdraws the directive and the field."""
        if not self.ctx.knobs(EscalationKnobs).terminate_capability:
            return None
        return evidence_starved_node(self.node_failure_rates)

    @property
    def offers_shots(self) -> bool:
        return bool(self.demo_pool and self.prompt_nodes) and self.shot_k_max > 0

    def block_window(self) -> dict[str, tuple[PoolEntry, ...]]:
        """By field a prompt node opens, what the panel shows: under ``closed`` the active subset whole, its wire's enum; under ``open`` earned blocks, then a window rotating by round."""
        opened = {name for node in self.prompt_nodes for name in node.open_prompt_fields}
        if self.block_access == "closed":
            return {name: held for name, held in self.blocks.items() if name in opened}
        out: dict[str, tuple[PoolEntry, ...]] = {}
        for name, blocks in self.blocks.items():
            earned = [b for b in blocks if b.group[1] == EARNED_SOURCE]
            rest = [b for b in blocks if b.group[1] != EARNED_SOURCE]
            k = min(max(self.pool_window - len(earned), 0), len(rest))
            start = (self.round_num - 1) * k % len(rest) if rest else 0
            if name in opened and (window := (*earned, *(rest[start:] + rest[:start])[:k])):
                out[name] = window
        return out

    @functools.cached_property
    def silent_l1_panels(self) -> frozenset[str]:
        """L1 panels rendering nothing here: L2's layout menu and wire enum leave them out."""
        with _no_round_warnings():
            return frozenset(
                name
                for name in NODE_LAYOUTS["l1_generate"].possible
                if not DispatchHub.render_items(name, self)
            )
