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

from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.embedded_run import (
    mint_and_score_origin,
    open_session,
    run_campaign,
)
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.connectors.dspy_module import (
    PROGRAM_NODE,
    RESULT_KEY,
    SCORE_KEY,
    DspyProgram,
    publish_dspy_program,
    reset_dspy_program,
)
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
from promptpotter.infrastructure.store.io import write_text, write_yaml
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.identity import default_identity

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from promptpotter.domain.export import PromptExport

__all__ = ["Loop", "Node", "PromptPotterOpt"]


@dataclass(frozen=True)
class Loop:
    """Loop control. Every field has a default, so ``Loop()`` is a complete configuration."""

    max_rounds: int = 5
    n_variants: int = 6
    samples_per_round: int = 20
    """How many trainset rows each candidate is scored on per round — the adaptive queue picks
    the informative ones out of the whole set. The cost knob: a round costs roughly
    ``n_variants x samples_per_round`` calls before PoBB starts cutting."""

    l1_patience: int = 0
    l2_patience: int = 2
    l3_patience: int = 1
    degradation_threshold: float = 0.4
    elimination_n_min: int = 4
    pobb_epsilon: float = 0.2
    spend_budget_usd: float | None = None
    # Rides the run-scoped seam rather than ``_optimization`` below, so ``None`` keeps the
    # campaign's armed default instead of disarming the ceiling the way its USD neighbour does.
    token_budget: int | None = None

    def _optimization(self) -> dict[str, Any]:
        return {
            "max_rounds": self.max_rounds,
            "n_variants": self.n_variants,
            "l1_patience": self.l1_patience,
            "l2_patience": self.l2_patience,
            "l3_patience": self.l3_patience,
            "degradation_threshold": self.degradation_threshold,
            "elimination_n_min": self.elimination_n_min,
            "pobb_epsilon": self.pobb_epsilon,
            "spend_budget_usd": self.spend_budget_usd,
        }


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
        loop: Loop | None = None,
        node: Node | None = None,
        task_description: str = "",
        scoring: str = SCORE_KEY,
    ) -> None:
        super().__init__()
        self.metric = metric
        self.dataset_name = dataset_name
        self.loop = loop or Loop()
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

        ``valset`` is accepted for contract parity and deliberately unused: PromptPotter holds out
        no set of its own — PoBB prunes on the training rows and the caller evaluates the returned
        program however they already do. Taking it and ignoring it silently would be the wrong
        shape, so it is named here instead."""
        rows = samples_from_dicts([{"query": _query_of(ex), "ground_truth": ""} for ex in trainset])
        program = DspyProgram(
            student=student,
            metric=self.metric,
            examples={_query_of(ex): ex for ex in trainset},
        )
        if len(program.examples) != len(trainset):
            raise ValueError(
                f"trainset has {len(trainset)} examples but only {len(program.examples)} distinct "
                "inputs — a duplicate row cannot be told from its twin at scoring time."
            )

        self._write_dataset_dir()
        session = await open_session(self.dataset_name)
        token = publish_dspy_program(program)
        try:
            # No overrides: the file this compile just wrote IS the projection of `loop` and
            # `nodes`, so passing them again would be a second path to the same values.
            config = load_dataset_campaign_config(self._campaign_path())
            pipeline_params = configure_and_apply_pipeline(session, config)
            observers, dataset, origin = await mint_and_score_origin(
                session, rows, config, pipeline_params=pipeline_params
            )
            result = await run_campaign(
                observers,
                dataset,
                origin,
                config,
                session=session,
                spend_budget_usd=self.loop.spend_budget_usd,
                token_budget=self.loop.token_budget,
            )
        finally:
            reset_dspy_program(token)
            await session.backend_client.aclose()

        if stop_reason_outcome(result.stop_reason) is not StopOutcome.SUCCESS:
            return student
        # The winner comes off the ARTIFACT, never off `CycleResult.winner_prompt_fields`: that is
        # the wire-side projection and flattens few-shot examples into a rendered block a
        # `PromptTemplate` rejects outright — a crash that waits for the first winner carrying
        # demonstrations and lands after the whole campaign is paid for.
        self.export = session.store.campaigns.read_export(session.hop)
        if self.export is None:
            return student
        return _with_instructions(student, self.export.template().render())

    # -- the dataset the campaign is keyed by -------------------------------------------------

    def _dataset_dir(self) -> Path:
        stores = build_stores(default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
        return stores.tenant_datasets.dataset_dir(self.dataset_name)

    def _campaign_path(self) -> Path:
        return dataset_campaign_path(self._dataset_dir())

    def _write_dataset_dir(self) -> None:
        """Materialize the files a campaign resolves by name. Rewritten every compile, because they
        are a projection of the arguments just passed — not operator-authored config that a second
        compile would be clobbering."""
        write_text(self._dataset_dir() / "task_description.md", self.task_description)
        node: dict[str, Any] = {
            "type": "llm",
            "runtime": "in_process",
            "node_role": "ranker",
            "description": "The caller's dspy.Module, scored by the caller's metric.",
            "prompt_info": {"template_variables": []},
            "config": {
                "model": self.node.model,
                "temperature": self.node.temperature,
                **self.node.extra,
            },
            "optimizer": {
                "param_keys": list(self.node.tune),
                "param_allowed_values": dict(self.node.allowed),
                "observation_name": PROGRAM_NODE,
                # `is_llm` on the prediction mapping is what makes a per-node `model` REQUIRED,
                # which is the check that stops a measurement being attributed to whichever LM
                # happened to be configured.
                "observation_mappings": [
                    {"pipeline_key": RESULT_KEY, "is_llm": True},
                    {"pipeline_key": SCORE_KEY},
                ],
                "langfuse_type": "generation",
            },
        }
        write_yaml(
            dataset_pipeline_path(self._dataset_dir()),
            {
                "name": "DSPy",
                "backend_name": "DSPy",
                "backend_type": "dspy",
                "available_models": sorted({self.node.model, *self.node.allowed.get("model", [])}),
                "nodes": {PROGRAM_NODE: node},
                "pipelines": {"default": [PROGRAM_NODE]},
            },
        )
        write_yaml(
            self._campaign_path(),
            {
                "campaign_config": {
                    "dataset_name": self.dataset_name,
                    # The caller's metric already graded the sample; the formula only carries its
                    # number through. Overriding `scoring` composes evaluators on top of it.
                    "scoring": self.scoring,
                    "headline_metric": "accuracy",
                    "sp_budget_ttest": self.loop.samples_per_round,
                    "optimization": self.loop._optimization(),
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
