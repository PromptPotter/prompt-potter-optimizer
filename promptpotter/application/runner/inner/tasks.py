"""``inner_tasks.json`` — the panel an outer dataset declares, and the spec one cell resolves to.

The file IS the declaration: which inner benchmark the panel measures on, how much evidence each
cell may buy, and one entry per cell. A dataset that owns this file IS an outer dataset — no name
test recognises one.

**The type is the validator.** This file used to be hand-parsed through a ``.get()`` ladder guarded
by a hand-written required-key tuple, which is the only config in the package with no schema — and
so the only one that could silently accumulate keys nobody read. It had two: an 8k-char
``description`` (a comment field invented because JSON has no comment syntax, read by nothing) and
a ``dataset_path`` that no code has ever resolved. ``extra="forbid"`` makes both unrepresentable
rather than merely tidy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from promptpotter.application.config import LivesConfig
from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError
from promptpotter.infrastructure.store.io import read_json_optional

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.runner.inner.cycle import InnerSpawnContext


class InnerBenchmarkConfig(BaseModel):
    """What every cell of the panel may SPEND — never what it is expected to REACH.

    There is no target score here, and deliberately so: declaring one asserts up front how much
    room the inner benchmark has, and a task the inner model looks bad at is a task it has not
    been tuned for yet, not a task with no headroom. The proxies normalize by the room to the
    real ceiling instead (``domain/l4/proxies.py``).

    The round cap and sample count set what the inner cycle is even ALLOWED to discover, so there
    is no default ladder: defaulting them in code silently rescales every meta-prompt candidate's
    fitness against a benchmark nobody declared. Every required field is required.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_samples_per_inner_round: int = Field(ge=1)
    max_inner_rounds: int = Field(ge=1)
    # None ⇒ keep the inner dataset's own value.
    inner_n_variants: int | None = Field(default=None, ge=1)
    # The improvement-banked round budget. ``max_inner_rounds`` is the CEILING a compounding inner
    # campaign may reach; lives are what make a stalling one stop early. Owned by the outer panel
    # for the same reason the round cap is: it is an L4 decision about how much evidence to buy,
    # not the inner dataset's standalone default.
    inner_lives: LivesConfig | None = None
    # Determinism clamp on the inner OPTIMIZER's sampling temperature. Each inner campaign is a
    # fitness measurement of one meta-prompt; at the file default, identical meta-prompts generate
    # different candidates and the run-to-run swing swamps the outer proxy. None ⇒ feature off.
    inner_optimizer_temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class InnerTask(BaseModel):
    """One panel cell. ``id`` is the outer query (e.g. ``"justlogic-d67/seed-0"``).

    A cell that omits the override fields inherits the top-level benchmark and the dataset's own
    model, so a single-benchmark panel needs no per-cell overrides. The model/provider overrides
    are the panel's ENVIRONMENT axis and are operator-set — never chosen by the loop — so a
    (model, dataset) cell is a fixed in-band resource, not a fuzzy pick.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    inner_dataset_seed: int = Field(default=0, ge=0)
    n_inner_rounds: int | None = Field(default=None, ge=1)
    inner_dataset: str | None = None
    inner_model: str | None = None
    inner_provider: str | None = None


class InnerTasks(BaseModel):
    """The whole file. ``extra="forbid"`` at every level — a key nobody reads cannot be written."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inner_benchmark: str = Field(min_length=1)
    inner_benchmark_config: InnerBenchmarkConfig
    tasks: list[InnerTask] = Field(min_length=1)


class InnerTaskSpec(BaseModel):
    """One outer query resolved against the panel → the inner campaign to run for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inner_dataset: str
    seed: int
    n_samples: int
    n_rounds: int
    n_variants: int | None
    lives: LivesConfig | None = None
    inner_model: str | None = None
    inner_provider: str | None = None
    inner_optimizer_temperature: float | None = None


def load_inner_tasks(path: Path) -> InnerTasks:
    """Read + validate ``inner_tasks.json``. The panel is the source of truth; an unreadable or
    non-conforming one is unscoreable, never defaulted."""
    raw = read_json_optional(path)
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
    panel = load_inner_tasks(ctx.dataset_config_dir / "inner_tasks.json")
    cfg = panel.inner_benchmark_config
    cell = next((t for t in panel.tasks if t.id == query), None)
    return InnerTaskSpec(
        inner_dataset=(
            cell.inner_dataset if cell and cell.inner_dataset else panel.inner_benchmark
        ),
        seed=cell.inner_dataset_seed if cell else 0,
        n_samples=cfg.n_samples_per_inner_round,
        n_rounds=(cell.n_inner_rounds if cell and cell.n_inner_rounds else cfg.max_inner_rounds),
        n_variants=cfg.inner_n_variants,
        lives=cfg.inner_lives,
        inner_model=cell.inner_model if cell else None,
        inner_provider=cell.inner_provider if cell else None,
        inner_optimizer_temperature=cfg.inner_optimizer_temperature,
    )


__all__ = [
    "InnerBenchmarkConfig",
    "InnerTask",
    "InnerTaskSpec",
    "InnerTasks",
    "load_inner_tasks",
    "resolve_inner_task",
]
