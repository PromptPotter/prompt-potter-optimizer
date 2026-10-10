"""Needs the ``dspy`` extra, so nothing else in the package may import this module."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

try:
    from dspy.teleprompt import Teleprompter
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "promptpotter.presentation.teleprompter needs DSPy — `pip install promptpotter[dspy]`"
    ) from exc
else:
    # DSPy parks lazy stand-ins in `sys.modules`; importing a SUBMODULE of an unloaded one fails.
    for _parked in list(sys.modules.values()):
        if type(_parked).__module__ == "dspy.utils.lazy_import":
            with contextlib.suppress(ImportError):
                getattr(_parked, "__file__", None)

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


_COMPILE_DEFAULTS: dict[str, Any] = {
    "max_rounds": 5,
    "degradation_threshold": 0.4,
    "elimination_n_min": 4,
}


def compile_loop(**knobs: Any) -> OptimizationConfig:
    optimization = OptimizationConfig.model_validate({**_COMPILE_DEFAULTS, **knobs})
    select_optimizer(optimization)
    return optimization


@dataclass(frozen=True)
class Node:
    """ONE node: the winning prompt and tuned settings reach EVERY predictor of the program."""

    model: str
    temperature: float = 0.0
    tune: tuple[str, ...] = ("instruction", "persona", "answer_format")
    allowed: dict[str, list[str]] = field(default_factory=dict)
    # Reaches the predictor's ``lm`` only when named in ``tune``; frozen otherwise.
    extra: dict[str, Any] = field(default_factory=dict)


class PromptPotterOpt(Teleprompter):  # type: ignore[misc]  # dspy is follow_imports=skip
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
        # ``None`` until a compile finishes, and after one that did not.
        self.export: PromptExport | None = None

    def compile(
        self,
        student: Any,
        *,
        trainset: list[Any],
        valset: list[Any] | None = None,
    ) -> Any:
        coro = self.acompile(student, trainset=trainset, valset=valset)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # SIGINT never reaches this thread: under a running loop, Ctrl+C no longer pauses the run.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def acompile(
        self,
        student: Any,
        *,
        trainset: list[Any],
        valset: list[Any] | None = None,
    ) -> Any:
        """``valset`` is contract parity only: with no ``dataset_split``, the trainset is the pool."""
        rows = samples_from_dicts([{"query": _query_of(ex), "ground_truth": ""} for ex in trainset])
        if (distinct := len({row.key for row in rows})) != len(trainset):
            raise ValueError(
                f"trainset has {len(trainset)} examples but only {distinct} distinct inputs — "
                "twin rows share one measurement, so each replays the other's score."
            )
        program = DspyProgram(student=student, metric=self.metric, examples=list(trainset))

        stores = build_stores(registered_or_default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
        dataset_dir = stores.tenant_datasets.dataset_dir(self.dataset_name)
        self._write_dataset_dir(dataset_dir)
        stores.tenant_datasets.save_benchmark_rows(self.dataset_name, rows)
        session = await open_session(self.dataset_name, stores=stores, program=program)
        try:
            # No overrides: the file this compile just wrote IS the projection of `loop` and `node`.
            config = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))
            configure_and_apply_pipeline(session, config)
            result = await run_campaign(
                session,
                list(session.samples),
                config,
                limits=LaunchLimits(),
                mode=RunMode(),
            )
        finally:
            await session.backend_client.aclose()

        if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
            return student
        # Off the ARTIFACT, never `CycleResult.result_prompt_fields`, which no `PromptTemplate` takes.
        self.export = session.store.campaigns.read_export(session.hop)
        if self.export is None:
            return student
        return _with_instructions(student, self.export.render())

    def _write_dataset_dir(self, dataset_dir: Path) -> None:
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
                    # The caller's metric already graded the sample; the formula carries it through.
                    "scoring": self.scoring,
                    "display_metric": "accuracy",
                    "optimization": self.loop.model_dump(mode="json", exclude_unset=True),
                }
            },
        )


def _query_of(example: Any) -> str:
    """Stable across re-runs (sorted keys): what lets a second compile reuse measurements."""
    inputs = example.inputs().toDict()
    return "\n".join(f"{k}: {inputs[k]}" for k in sorted(inputs))


def _with_instructions(student: Any, prompt: str) -> Any:
    copy = student.deepcopy()
    for predictor in copy.predictors():
        predictor.signature = predictor.signature.with_instructions(prompt)
    return copy
