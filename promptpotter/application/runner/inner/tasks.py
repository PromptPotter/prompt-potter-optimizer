"""``inner_tasks.yaml`` — the panel an outer dataset declares, and the spec one cell resolves to.

The file IS the declaration: which inner benchmark the panel measures on, how much evidence each
cell may buy, and one entry per cell. A dataset that owns this file IS an outer dataset — no name
test recognises one.

**The type is the validator** — ``extra="forbid"``, so a key nobody reads is
unrepresentable rather than merely tidy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field, ValidationError

from promptpotter.application.config import CampaignConfig, LivesConfig
from promptpotter.config.settings import DEFAULT_ORIGIN_BUDGET
from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import read_yaml_optional

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.runner.inner.cycle import InnerSpawnContext


class InnerBenchmarkConfig(StrictModel):
    """What every cell of the panel may SPEND — never what it is expected to REACH.

    There is no target score here, and deliberately so: declaring one asserts up front how much
    room the inner benchmark has, and a task the inner model looks bad at is a task it has not
    been tuned for yet, not a task with no headroom. The proxies normalize by the room to the
    real ceiling instead (``domain/l4/proxies.py``).

    The round cap and sample count set what the inner cycle is even ALLOWED to discover, so there
    is no default ladder: defaulting them in code silently rescales every optimizer prompt candidate's
    fitness against a benchmark nobody declared. Every required field is required.
    """

    model_config = ConfigDict(frozen=True)

    n_samples_per_inner_round: int = Field(ge=1)
    # Origin (inner round 0) eval breadth. Explicit `null` ⇒ same as the per-round count.
    # Defaults to DEFAULT_ORIGIN_BUDGET — the SAME constant `CampaignConfig.sp_budget_origin`
    # defaults to, so the outer recursion and the inner instrument measure their origins on
    # one ruler unless a panel says otherwise. Above the per-round count buys a tighter
    # inner-origin θ — the term every outer delta subtracts — while candidates keep the
    # per-round budget; the extra origin rows are content-addressed cache shared across every
    # inner campaign on the same seed, so the cost is paid once per cell.
    #
    # Unlike `n_samples_per_inner_round` / `max_inner_rounds` above, defaulting this does NOT
    # silently rescale a candidate's fitness: it widens the term BOTH arms of every paired
    # delta subtract, which is a precision gain, not a change of ruler.
    n_samples_origin: int | None = Field(default=DEFAULT_ORIGIN_BUDGET, ge=1)
    max_inner_rounds: int = Field(ge=1)
    # None ⇒ keep the inner dataset's own value.
    inner_n_variants: int | None = Field(default=None, ge=1)
    # The improvement-banked round budget. ``max_inner_rounds`` is the CEILING a compounding inner
    # campaign may reach; lives are what make a stalling one stop early. Owned by the outer panel
    # for the same reason the round cap is: it is an L4 decision about how much evidence to buy,
    # not the inner dataset's standalone default.
    inner_lives: LivesConfig | None = None
    # Determinism clamp on the inner OPTIMIZER's sampling temperature. Each inner campaign is a
    # fitness measurement of one optimizer prompt; at the file default, identical optimizer prompts generate
    # different candidates and the run-to-run swing swamps the outer proxy. None ⇒ feature off.
    inner_optimizer_temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class InnerTask(StrictModel):
    """One panel cell. ``id`` is the outer query (e.g. ``"justlogic-d67/seed-0"``).

    A cell that omits the override fields inherits the top-level benchmark and the dataset's own
    model, so a single-benchmark panel needs no per-cell overrides. The model/provider overrides
    are the panel's ENVIRONMENT axis and are operator-set — never chosen by the loop — so a
    (model, dataset) cell is a fixed in-band resource, not a fuzzy pick.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    inner_dataset_seed: int = Field(default=0, ge=0)
    n_inner_rounds: int | None = Field(default=None, ge=1)
    inner_dataset: str | None = None
    inner_model: str | None = None
    inner_provider: str | None = None


class InnerTasks(StrictModel):
    """The whole file. ``extra="forbid"`` at every level — a key nobody reads cannot be written."""

    model_config = ConfigDict(frozen=True)

    inner_benchmark: str = Field(min_length=1)
    inner_benchmark_config: InnerBenchmarkConfig
    tasks: list[InnerTask] = Field(min_length=1)


class InnerTaskSpec(StrictModel):
    """One outer query resolved against the panel → the inner campaign to run for it."""

    model_config = ConfigDict(frozen=True)

    inner_dataset: str
    seed: int
    n_samples: int
    n_samples_origin: int | None = None
    n_rounds: int
    n_variants: int | None
    lives: LivesConfig | None = None
    inner_model: str | None = None
    inner_provider: str | None = None
    inner_optimizer_temperature: float | None = None


def inner_tasks_path(dataset_dir: Path) -> Path:
    """The dataset's inner-task panel, named by the connector that reads it.

    One spelling, so the is-this-L4 probe and the loader cannot drift apart — a drift
    that skips the observation contract rather than raising.
    """
    from promptpotter.connectors import CONNECTORS

    return dataset_dir / CONNECTORS["promptpotter"].experiment_file


def load_inner_tasks(path: Path) -> InnerTasks:
    """Read + validate the inner-task panel. The panel is the source of truth; an unreadable or
    non-conforming one is unscoreable, never defaulted."""
    raw = read_yaml_optional(path)
    if raw is None:
        raise InnerCycleUnscoreableError(
            f"{path} is missing — the inner benchmark, its sample count and its round cap are "
            "all declared there. There is no default to run."
        )
    try:
        return InnerTasks.model_validate(raw)
    except ValidationError as exc:
        raise InnerCycleUnscoreableError(
            f"{path} does not declare a runnable panel: {exc}"
        ) from exc


def resolve_inner_task(ctx: InnerSpawnContext, query: str) -> InnerTaskSpec:
    """Map an outer query to its inner-campaign spec: the top-level benchmark + budget, overlaid
    by the matching cell's overrides. A query with no matching cell runs the panel's default."""
    panel = load_inner_tasks(inner_tasks_path(ctx.dataset_config_dir))
    cfg = panel.inner_benchmark_config
    cell = next((t for t in panel.tasks if t.id == query), None)
    return InnerTaskSpec(
        inner_dataset=(
            cell.inner_dataset if cell and cell.inner_dataset else panel.inner_benchmark
        ),
        seed=cell.inner_dataset_seed if cell else 0,
        n_samples=cfg.n_samples_per_inner_round,
        n_samples_origin=cfg.n_samples_origin,
        n_rounds=(cell.n_inner_rounds if cell and cell.n_inner_rounds else cfg.max_inner_rounds),
        n_variants=cfg.inner_n_variants,
        lives=cfg.inner_lives,
        inner_model=cell.inner_model if cell else None,
        inner_provider=cell.inner_provider if cell else None,
        inner_optimizer_temperature=cfg.inner_optimizer_temperature,
    )


def inner_instrument_config(
    spec: InnerTaskSpec,
    base: CampaignConfig,
    *,
    llm_node: str,
    n_scored: int,
) -> CampaignConfig:
    """The ``CampaignConfig`` an inner instrument runs under — a pure derivation of the panel
    spec over the inner dataset's own committed config. The declared counterpart of the L4 law
    in ``domain/l4/``; session-free, so ``llm_node`` (the prompt-bearing node) is passed in.

    Two derivations are load-bearing:

    - **Budget is the ROUND budget.** ``max_rounds`` caps the cell (the proxies are defined over
      exactly that many rounds), and ``spend_budget_usd`` / ``token_budget`` are cleared: a cap
      that trips on measured tokens truncates the trajectory nondeterministically. The ORIGIN is
      scored on the whole drawn bank (``sp_budget_origin``) — its θ is the term every outer delta
      subtracts, and the extra rows are shared cache across the cell's campaigns. Candidates are
      scored on the panel's declared per-round count (``sp_budget_ttest``): with ``n_samples_origin``
      unset the bank IS that count and every arm runs the whole bank; declaring it larger widens
      only the origin — the operator's cost/precision trade, since candidate spend is per-candidate
      while a wider candidate budget would also widen every θ-LCB comparison basis for no shared
      cache payback.
    - **CRN seed on the prompt-bearing node.** ``spec.seed`` is the per-cell data-draw seed, fixed
      per cell and identical across every outer arm, so one seed makes the inner's run-to-run noise
      common and cancel in the (variant − origin) paired diff — at zero extra spend. The node is
      ASKED (``llm_node``), never assumed: a hardcoded name wrote the seed under a nonexistent key
      on any dataset that named its node otherwise, silently dropping the cancellation. The
      cancellation is conditional on the inner routing: a ``:nitro`` model draws freely regardless
      of seed, so it is carried but buys nothing until ``:nitro`` is dropped from the inner dataset.
    """
    opt_update: dict[str, Any] = {
        "max_rounds": spec.n_rounds,
        "spend_budget_usd": None,
        "token_budget": None,
        # An instrument cannot ask a human anything. The origin gate halts round 0 on a
        # non-healthy origin and waits for an operator decision — the package's one unbounded
        # await — which inside an outer sample is a deadlock nothing but the sample wall clock
        # can end, and the operator is never shown a prompt because the gate belongs to a cycle
        # buried in `.inner/`. A bad inner origin is not lost either way: it lands as a poor
        # trajectory or an `InnerCycleUnscoreableError`, which is exactly the measurement the
        # outer loop is there to take.
        "origin_gate": "off",
        # ONE RULER UNIT ACROSS THE PANEL. Under 1PL the ruler is δ alone and the unit is pinned
        # by the logistic link, so every cell's θ — and therefore every `mean_round_delta` — is
        # in the same logits. Under 2PL the ruler also carries discrimination `a`, and θ is then
        # in units of 1/a: a cell that graduates measures on a different scale from one that did
        # not, and the panel averages the mixture and t-tests it. Each inner cycle decides
        # graduation from its OWN held-out CV, so which cells graduate is a property of the draw,
        # not of the optimizer prompt under test — noise entering as a units change. Off here
        # only; a top-level campaign keeps the graduation, which is where it earns its keep.
        "enable_2pl_graduation": False,
    }
    if spec.n_variants is not None:
        opt_update["n_variants"] = spec.n_variants
    if spec.lives is not None:
        opt_update["lives"] = spec.lives
    po: dict[str, Any] = {k: dict(v) for k, v in (base.pipeline_overrides or {}).items()}
    node = dict(po.get(llm_node, {}))
    node["seed"] = spec.seed
    if spec.inner_model:
        node["model"] = spec.inner_model
        if spec.inner_provider:
            node["provider"] = spec.inner_provider
    po[llm_node] = node
    return base.model_copy(
        update={
            "sp_budget_ttest": min(spec.n_samples, n_scored),
            "sp_budget_origin": n_scored,
            "optimization": base.optimization.model_copy(update=opt_update),
            "pipeline_overrides": po,
        }
    )


__all__ = [
    "InnerBenchmarkConfig",
    "InnerTask",
    "InnerTaskSpec",
    "InnerTasks",
    "inner_instrument_config",
    "load_inner_tasks",
    "resolve_inner_task",
]
