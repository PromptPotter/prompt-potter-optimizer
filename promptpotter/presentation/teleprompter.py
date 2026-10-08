"""``PromptPotterOpt`` — the loop as a DSPy optimizer, and the fifth way in.

The other four entry points drive a campaign the operator watches; this one runs PromptPotter
inside someone else's program, on their rows, graded by their metric. It is a presentation
adapter for the same reason the CLI is: it parses a caller's arguments, calls
``application/embedded_run.py``, and formats what comes back.

Importing this module needs DSPy — ``pip install promptpotter[dspy]``. Nothing else in the
package imports it, so a plain install never pays for that.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

try:
    from dspy.teleprompt import Teleprompter
except ModuleNotFoundError as exc:  # lazy: an extra, and the only importer is the caller
    raise ModuleNotFoundError(
        "promptpotter.presentation.teleprompter needs DSPy — `pip install promptpotter[dspy]`"
    ) from exc

from promptpotter.application.campaign_config import OptimizationConfig
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.embedded_run import open_session, run_campaign
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.application.runner.entry import RunMode
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.connectors.dspy_module import (
    SCORE_KEY,
    DspyProgram,
    dataset_pipeline,
)
from promptpotter.domain.launch_limits import LaunchLimits
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
from promptpotter.infrastructure.store.io import write_text, write_yaml
from promptpotter.infrastructure.store.stores import build_stores

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from promptpotter.domain.export import PromptExport

__all__ = ["Node", "PromptPotterOpt", "compile_loop"]


# What a compile starts from where the caller names nothing: the one field the schema requires,
# and a round count and pruning floor sized for a trainset rather than a benchmark.
_COMPILE_DEFAULTS: dict[str, Any] = {
    "max_rounds": 5,
    "degradation_threshold": 0.4,
    "elimination_n_min": 4,
}


def compile_loop(**knobs: Any) -> OptimizationConfig:
    """Loop control for a compile: the campaign's own ``OptimizationConfig``, so every knob any entry
    point takes is reachable, and one the schema or manifest refuses is refused before ``compile``."""
    optimization = OptimizationConfig.model_validate({**_COMPILE_DEFAULTS, **knobs})
    select_optimizer(optimization)
    return optimization


@dataclass(frozen=True)
class Node:
    """The student, as one tunable node. ``tune`` is the axis list — prompt fields and model params
    evolve together; anything left off it is frozen at the value set here.

    ONE node, because a DSPy program is a single call from here: the winning prompt and the tuned
    model settings reach EVERY predictor in the scoring copy. Right for predictors that share a
    task, wrong for a program whose predictors do different jobs — those want two compiles."""

    model: str
    temperature: float = 0.0
    tune: tuple[str, ...] = ("instruction", "persona", "answer_format")
    allowed: dict[str, list[str]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    """Any further node config — ``max_tokens``, ``reasoning_effort``, whatever the caller's LM
    takes. Reaches the predictor's ``lm`` when named in ``tune``, and is frozen otherwise."""


class PromptPotterOpt(Teleprompter):  # type: ignore[misc]  # dspy is follow_imports=skip
    """PromptPotter as a ``Teleprompter``. ``compile`` obeys DSPy's contract; ``acompile`` is the
    async peer a host with a running event loop awaits instead."""

    def __init__(
        self,
        *,
        metric: Callable[[Any, Any], Any],
        dataset_name: str,
        loop: OptimizationConfig | None = None,
        node: Node | None = None,
        task_description: str = "",
        scoring: str = SCORE_KEY,
    ) -> None:
        super().__init__()
        self.metric = metric
        self.dataset_name = dataset_name
        self.loop = loop or compile_loop()
        self.node = node or Node(model="openai/gpt-4o-mini")
        self.task_description = task_description
        self.scoring = scoring
        self.export: PromptExport | None = None
        """The winner artifact of the last compile — prompt fields plus the provenance that makes
        its fitness readable. ``None`` until a compile finishes, and after one that did not."""

    def compile(
        self,
        student: Any,
        *,
        trainset: list[Any],
        valset: list[Any] | None = None,
    ) -> Any:
        """Sync entry. With no loop running this is ``asyncio.run``; inside one — a notebook, which
        DSPy's own docs single out — the run moves to a dedicated thread with its own loop.

        The thread costs exactly one thing: SIGINT never reaches it, so **Ctrl+C stops pausing the
        campaign**. `promptpotter pause` and the webapp control both still work, because they poll
        a flag rather than catch an interrupt. Await :meth:`acompile` to keep the interrupt."""
        coro = self.acompile(student, trainset=trainset, valset=valset)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def acompile(
        self,
        student: Any,
        *,
        trainset: list[Any],
        valset: list[Any] | None = None,
    ) -> Any:
        """Run one campaign over *trainset* and return *student* with the winning prompt applied.

        ``valset`` is accepted for contract parity and deliberately unused: the campaign this
        writes declares no ``dataset_split``, so the whole trainset is the search pool, no bench
        score is taken, and the caller evaluates the returned program on its own held-out rows."""
        rows = samples_from_dicts([{"query": _query_of(ex), "ground_truth": ""} for ex in trainset])
        if (distinct := len({row.key for row in rows})) != len(trainset):
            raise ValueError(
                f"trainset has {len(trainset)} examples but only {distinct} distinct inputs — "
                "twin rows share one measurement, so each replays the other's score."
            )
        program = DspyProgram(student=student, metric=self.metric, examples=list(trainset))

        # The operator's own workspace, resolved once: the dataset this writes and the campaign
        # the session mints have to land in the one tree the terminal and the webapp read.
        stores = build_stores(registered_or_default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
        dataset_dir = stores.tenant_datasets.dataset_dir(self.dataset_name)
        self._write_dataset_dir(dataset_dir)
        session = await open_session(self.dataset_name, stores=stores, program=program)
        try:
            # No overrides, budgets included: the file this compile just wrote IS the projection
            # of `loop` and `nodes`, so passing them again would be a second path to the same values.
            config = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))
            configure_and_apply_pipeline(session, config)
            result = await run_campaign(
                session,
                rows,
                config,
                limits=LaunchLimits(),
                mode=RunMode(),
            )
        finally:
            await session.backend_client.aclose()

        if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
            return student
        # The winner comes off the ARTIFACT, never off `CycleResult.result_prompt_fields`: that is
        # the wire-side projection, whose rendered shot block a `PromptTemplate` rejects outright.
        self.export = session.store.campaigns.read_export(session.hop)
        if self.export is None:
            return student
        return _with_instructions(student, self.export.render())

    # -- the dataset the campaign is keyed by -------------------------------------------------

    def _write_dataset_dir(self, dataset_dir: Path) -> None:
        """Materialize the files a campaign resolves by name. Rewritten every compile, because they
        are a projection of the arguments just passed — not operator-authored config that a second
        compile would be clobbering."""
        write_text(dataset_dir / "task_description.md", self.task_description)
        write_yaml(
            dataset_pipeline_path(dataset_dir),
            dataset_pipeline(
                model=self.node.model,
                temperature=self.node.temperature,
                extra=self.node.extra,
                tune=self.node.tune,
                allowed=self.node.allowed,
            ),
        )
        write_yaml(
            dataset_campaign_path(dataset_dir),
            {
                "campaign_config": {
                    "dataset_name": self.dataset_name,
                    # The caller's metric already graded the sample; the formula only carries its
                    # number through. Overriding `scoring` composes evaluators on top of it.
                    "scoring": self.scoring,
                    "display_metric": "accuracy",
                    "optimization": self.loop.model_dump(mode="json", exclude_unset=True),
                }
            },
        )


def _query_of(example: Any) -> str:
    """The example's inputs as the one string a :class:`Sample` can carry and the connector can
    join back on. Stable across a re-run, which is what lets a second compile reuse measurements."""
    inputs = example.inputs().toDict()
    return "\n".join(f"{k}: {inputs[k]}" for k in sorted(inputs))


def _with_instructions(student: Any, prompt: str) -> Any:
    copy = student.deepcopy()
    for predictor in copy.predictors():
        predictor.signature = predictor.signature.with_instructions(prompt)
    return copy
