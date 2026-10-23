"""Needs the ``dspy`` extra, so nothing else in the package may import this module."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.shared.extras import Extra, require_extra

try:
    from dspy.teleprompt import Teleprompter
except ModuleNotFoundError:
    require_extra(Extra.DSPY)
    raise
else:
    # DSPy parks lazy stand-ins in `sys.modules`; importing a SUBMODULE of an unloaded one fails.
    for _parked in list(sys.modules.values()):
        if type(_parked).__module__ == "dspy.utils.lazy_import":
            with contextlib.suppress(ImportError):
                getattr(_parked, "__file__", None)

from promptpotter.application.account import local_stores
from promptpotter.application.campaign_config import OptimizationConfig
from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.embedded_run import open_session, run_campaign
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.connectors.dspy_module import (
    PROGRAM_NODE,
    SCORE_KEY,
    DspyProgram,
    dataset_pipeline,
)
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import Budget

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from promptpotter.domain.export import PromptExport
    from promptpotter.infrastructure.store.stores import Stores


_COMPILE_DEFAULTS: dict[str, Any] = {
    "budget": {"rounds": 5},
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
        items = [{"query": _query_of(ex), "ground_truth": ""} for ex in trainset]
        if (distinct := len({row.key for row in samples_from_dicts(items)})) != len(trainset):
            raise ValueError(
                f"trainset has {len(trainset)} examples but only {distinct} distinct inputs — "
                "twin rows share one measurement, so each replays the other's score."
            )
        program = DspyProgram(student=student, metric=self.metric, examples=list(trainset))

        stores = local_stores(tenant=None)
        dataset_dir = self._write_definition(stores, items)
        session = await open_session(self.dataset_name, stores=stores, program=program)
        try:
            # No overrides: the file this compile just wrote IS the projection of `loop` and `node`.
            config = load_dataset_campaign_config(dataset_dir)
            session, result = await run_campaign(
                session,
                config,
                budget=Budget(),
                mode=RunMode(),
            )
        finally:
            await session.backend_client.aclose()

        if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
            return student
        self.export = session.store.campaigns.read_export(session.hop)
        if self.export is None or PROGRAM_NODE not in self.export.prompts:
            return student
        return _with_instructions(student, self.export.render(PROGRAM_NODE))

    def _write_definition(self, stores: Stores, items: list[dict[str, str]]) -> Path:
        return stores.tenant_datasets.write_definition(
            self.dataset_name,
            # In trainset order: the connector reads a sample's example off its position.
            source={"recipe": "rows", "shuffled": False, "items": items},
            pipeline=dataset_pipeline(
                model=self.node.model,
                temperature=self.node.temperature,
                extra=self.node.extra,
                tune=self.node.tune,
                allowed=self.node.allowed,
            ),
            campaign={
                # The caller's metric already graded the sample; the formula carries it through.
                "scoring": self.scoring,
                "display_metric": "accuracy",
                "optimization": self.loop.model_dump(mode="json", exclude_unset=True),
            },
            description=self.task_description,
            framing=TaskDecomposition(),
            origin={},
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
