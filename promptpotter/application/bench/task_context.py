"""Read resolves tenant-then-install; a decomposition always lands in tenant."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

from pydantic import Field, create_model, model_validator
from pydantic.json_schema import SkipJsonSchema

from promptpotter.application.bench.llm_call import (
    FilledPrompt,
    LLMCallContext,
    OptimizerResponseModel,
    answer,
)
from promptpotter.application.optimizer_manifest import checkin_manifest, running_prompt
from promptpotter.config.paths import checkin_manifest_path
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.optimizer_template import (
    PROMPT_FIELDS,
    OptimizerPromptTemplate,
)
from promptpotter.domain.pipeline_manifest import CHECKIN_NODE
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    readable_task_context,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import FileKind, FileUnreadableError, unknown_slots_said

ORIGIN_FIELD_MAX = 600


class CheckinTaskContext(OptimizerResponseModel):
    """Every field renders VERBATIM into every optimizer prompt, frozen for the run: over budget is REFUSED at mint."""

    domain: str = Field(
        "", description="One noun phrase — the task family, e.g. 'competition mathematics'."
    )
    pipeline_purpose: str = Field(
        "", description="One sentence: what this campaign produces, for an outside reader."
    )
    data_characteristics: str = Field(
        "",
        description="One sentence (<=40 words): the sample properties L1 must account for — "
        "length, modality, distribution skew, known bias.",
    )
    optimization_goals: str = Field(
        "",
        description="One sentence (<=40 words): what we optimise for, in operator vocabulary.",
    )
    key_challenges: str = Field(
        "",
        description="One sentence (<=40 words): the 1-2 dominant failure patterns to defend "
        "against THIS round — a single current challenge, never a growing list.",
    )
    upstream_context: str = Field(
        "", description="Short framing prepended around problem_description, or empty."
    )
    downstream_context: str = Field(
        "", description="Short framing appended around problem_description, or empty."
    )


class OriginFinding(OptimizerResponseModel):
    """An UNCITED finding is rejected by the apply loop, and only ``confidence == "high"`` auto-confirms."""

    field: str = Field(
        default="", description="Checklist field id, e.g. 'task_description', 'column.query'."
    )
    proposed_value: str = Field(default="", description="The value proposed for this field.")
    confidence: str = Field(
        default="low", description="'high' or 'low'. Only 'high' auto-confirms."
    )
    evidence: str = Field(
        default="",
        description="What in the input supports this — a header name, a sample value, or a stated operator preference. Findings with no evidence are rejected.",
    )


class OriginQuestion(OptimizerResponseModel):
    field: str = Field(
        default="", description="Checklist field id the answer resolves, e.g. 'column.query'."
    )
    prompt: str = Field(default="", description="Short operator-facing question.")
    options: list[str] = Field(
        default_factory=list,
        description="Optional closed set of acceptable answers; empty = free text.",
    )


class OriginNextAction(OptimizerResponseModel):
    """The deterministic checklist, not this field, decides completeness: a false ``ready`` is re-checked and rejected."""

    kind: str = Field(
        default="propose",
        description="'ask' (need operator input), 'propose' (findings applied), or 'ready' (resolver believes origin complete — re-checked).",
    )
    questions: list[OriginQuestion] = Field(
        default_factory=list,
        description="For kind='ask': operator-facing questions, each naming the field it resolves so the answer applies directly.",
    )


class CheckinOutput(OptimizerResponseModel):
    """Task decomposition leaves the origin block empty; origin resolution fills it AND the Layer-1
    fields `checkin_output` offers."""

    # The Layer-1 slots the model is shown; `checkin_output` narrows them to a chain's.
    offered: ClassVar[tuple[str, ...]] = PROMPT_FIELDS

    # Field order is GENERATION order; optional and trailing, a constrained decoder skips a field.
    persona: str = ""
    task_intent: str
    answer_format: str = Field(
        description="The output contract only: the exact shape the scorer extracts, as described to you in context. The valid labels are appended deterministically — never list them here.",
    )
    problem_description: str = ""
    instruction: str = ""
    thinking_style: str = Field(
        default="",
        description="How to reason. Never the output shape or the scoring rule — those are answer_format's.",
    )
    task_context: CheckinTaskContext = Field(default_factory=CheckinTaskContext)
    assessment: str = Field(default="", description="One-line read of the current origin state.")
    findings: list[OriginFinding] = Field(default_factory=list)
    next_action: OriginNextAction = Field(default_factory=OriginNextAction)
    recap: str = Field(
        default="",
        description="On a 'ready' turn: a jargon-free paragraph restating what the campaign will do, for the operator to confirm intent.",
    )

    @model_validator(mode="after")
    def _check_the_starting_prompt(self) -> CheckinOutput:
        # Never a wire `maxLength`: a constrained decoder honours that by cutting mid-sentence.
        problems = [
            f"{name} must not be empty"
            for name in ("task_intent", "answer_format")
            if name in self.offered and not getattr(self, name).strip()
        ] + [
            f"{name} is {len(value)} chars (at most {ORIGIN_FIELD_MAX})"
            for name in self.offered
            if len(value := getattr(self, name)) > ORIGIN_FIELD_MAX
        ]
        if problems:
            raise ValueError(
                "; ".join(problems) + " — these fields are the starting prompt; "
                "move domain detail into task_context."
            )
        return self


assert set(PROMPT_FIELDS) <= set(CheckinOutput.model_fields)


_SPLICE_FIELDS = ("upstream_context", "downstream_context")


def checkin_output(prompt_fields: Iterable[str]) -> type[CheckinOutput]:
    """A slot for each prompt field a node declares, so a chain bearing none offers the model none;
    the splice framing only where ONE node declares its target (`scored_prompts`). A hidden slot
    keeps its place and reads empty."""
    declared = list(prompt_fields)
    offered = tuple(f for f in PROMPT_FIELDS if f in declared)
    if offered == PROMPT_FIELDS and declared.count("problem_description") == 1:
        return CheckinOutput
    hidden: dict[str, Any] = {
        name: (SkipJsonSchema[str], "") for name in PROMPT_FIELDS if name not in offered
    }
    if declared.count("problem_description") != 1:
        # Excluded from the dump the framing commits: a hidden splice reads empty.
        splices: dict[str, Any] = {
            name: (SkipJsonSchema[str], Field(default="", exclude=True)) for name in _SPLICE_FIELDS
        }
        unspliced = create_model(
            "CheckinTaskContext",
            __base__=CheckinTaskContext,
            __doc__=CheckinTaskContext.__doc__,
            **splices,
        )
        hidden["task_context"] = (unspliced, Field(default_factory=unspliced))
    narrowed = create_model(
        "CheckinOutput", __base__=CheckinOutput, __doc__=CheckinOutput.__doc__, **hidden
    )
    narrowed.offered = offered
    return narrowed


def campaign_framing(
    stores: Stores, campaign_config: CampaignConfig, dataset: ResolvedDataset | None
) -> TaskDecomposition:
    if campaign_config.task_framing == "off":
        return TaskDecomposition()
    return committed_task_context(stores, dataset)


def committed_task_context(stores: Stores, dataset: ResolvedDataset | None) -> TaskDecomposition:
    """A PURE read, so identity may use it: a decomposition needs the cycle a mint is still computing."""
    if dataset is None:
        return TaskDecomposition()
    return readable_task_context(stores, dataset)


def checkin_provider() -> str:
    """What a check-in call sends on, which its `quota.py::paid_call` is admitted against."""
    return checkin_manifest().config.provider


def checkin_template() -> OptimizerPromptTemplate:
    """No panel is filled into the check-in, so any other ``{{slot}}`` reaches the model literally."""
    checkin = checkin_manifest()
    template = running_prompt(CHECKIN_NODE, checkin.config, checkin.manifest)
    if unknown := template.unknown_slots({"consultation_instruction"}):
        raise FileUnreadableError(
            FileKind.OPTIMIZER_PROMPT,
            path=str(checkin_manifest_path()),
            reason=unknown_slots_said(CHECKIN_NODE, unknown),
        )
    return template


async def run_checkin(
    *,
    response_model: type[CheckinOutput],
    consultation_instruction: str,
    user_content: str,
    stores: Stores,
) -> tuple[CheckinOutput, int]:
    """Every record the call writes lands on the scope's ledger, which its door binds over the
    draft's (`draft_ledger`): no run scope stands at a turn."""
    return await answer(
        checkin_call_context(stores),
        response_model,
        FilledPrompt({}, {"consultation_instruction": consultation_instruction}),
        user_content=user_content,
    )


def draft_ledger(stores: Stores, draft_id: str) -> CycleEventLog:
    """What a draft's resolver turn bills, which Start moves into the root cycle it mints."""
    return CycleEventLog.open(CycleDir(stores.drafts.draft_dir(draft_id)))


def checkin_call_context(stores: Stores) -> LLMCallContext:
    return LLMCallContext(
        node=CHECKIN_NODE,
        config=checkin_manifest().config,
        template=checkin_template(),
        round_num=0,
        cache=stores.optimizer_reuse,
        seed=None,
    )


async def commit_task_framing(
    stores: Stores,
    dataset_name: str,
    description: str,
    *,
    prompt_fields: Iterable[str],
) -> None:
    """*prompt_fields*: each the chain's prompt nodes declare, once per node declaring it."""
    result, _ = await run_checkin(
        response_model=checkin_output(prompt_fields),
        consultation_instruction=(
            "Return a JSON object with exactly these keys. Be concise and actionable."
        ),
        user_content=(
            "The user has provided a raw context description. Parse it into "
            "structured Layer 1 prompt fields.\n\n"
            f"Context:\n{description}"
        ),
        stores=stores,
    )
    stores.tenant_datasets.save_task_context(
        dataset_name,
        TaskDecomposition.from_dict(
            {**result.task_context.model_dump(), "raw_description": description}
        ),
    )
