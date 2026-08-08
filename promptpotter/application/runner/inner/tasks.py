"""``inner_tasks.yaml`` — the panel an outer dataset declares. A dataset that OWNS this file IS an
outer dataset; no name test recognises one. ``extra="forbid"`` throughout: the type is the validator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field, ValidationError

from promptpotter.application.campaign_config import CampaignConfig, LivesConfig
from promptpotter.config.settings import DEFAULT_ORIGIN_BUDGET
from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import read_yaml_optional

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.runner.inner.spawn import InnerSpawnContext


class InnerBenchmarkConfig(StrictModel):
    """What every cell may SPEND — never what it is expected to REACH. No target score and no default
    ladder: both would rescale every candidate's fitness against a benchmark nobody declared."""

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
    """One panel cell; ``id`` is the outer query. Omitted overrides inherit the top-level benchmark and
    model — and the model/provider pair is the panel's ENVIRONMENT axis, operator-set, never searched."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    inner_dataset_seed: int = Field(default=0, ge=0)
    n_inner_rounds: int | None = Field(default=None, ge=1)
    inner_dataset: str | None = None
    inner_model: str | None = None
    inner_provider: str | None = None


class InnerTasks(StrictModel):
    model_config = ConfigDict(frozen=True)

    inner_benchmark: str = Field(min_length=1)
    inner_benchmark_config: InnerBenchmarkConfig
    tasks: list[InnerTask] = Field(min_length=1)


class InnerTaskSpec(StrictModel):
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
    """The dataset's inner-task panel. ONE spelling, so the is-this-L4 probe and the loader cannot drift
    apart — a drift that skips the observation contract rather than raising."""
    from promptpotter.connectors import CONNECTORS

    return dataset_dir / CONNECTORS["promptpotter"].experiment_file


def load_inner_tasks(path: Path) -> InnerTasks:
    """Read + validate the panel. It is the source of truth: an unreadable one is unscoreable, never defaulted."""
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
    """Map an outer query to its inner-campaign spec — the top-level benchmark + budget, overlaid by the
    matching cell. A query with no matching cell runs the panel's default."""
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
    """The ``CampaignConfig`` an inner cell runs under. Budget is the ROUND budget — token and spend caps
    are CLEARED, since one tripping on measured tokens truncates the trajectory nondeterministically."""
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
