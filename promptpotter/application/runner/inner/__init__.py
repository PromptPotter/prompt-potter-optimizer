"""L4 recursion — one outer sample is one whole inner PromptPotter campaign.

- :mod:`tasks` — the panel an outer dataset DECLARES (``inner_tasks.json``) and the spec one
  cell resolves to. The type is the validator.
- :mod:`cycle` — how that spec is RUN: the spawn context, the sandboxed re-entrant task, and
  the narrative the outer loop reads back.

The law that scores the result lives one layer down, in :mod:`promptpotter.domain.l4`.
"""

from promptpotter.application.runner.inner.cycle import (
    INNER_RESULT_KEY,
    InnerSpawnContext,
    publish_inner_spawn_context,
    run_inner_cycle,
)
from promptpotter.application.runner.inner.tasks import (
    InnerBenchmarkConfig,
    InnerTask,
    InnerTasks,
    InnerTaskSpec,
    load_inner_tasks,
    resolve_inner_task,
)

__all__ = [
    "INNER_RESULT_KEY",
    "InnerBenchmarkConfig",
    "InnerSpawnContext",
    "InnerTask",
    "InnerTaskSpec",
    "InnerTasks",
    "load_inner_tasks",
    "publish_inner_spawn_context",
    "resolve_inner_task",
    "run_inner_cycle",
]
