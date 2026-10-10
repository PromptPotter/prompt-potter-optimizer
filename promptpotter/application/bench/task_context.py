"""Read resolves tenant-then-install; a decomposition always lands in tenant."""

from __future__ import annotations

from pydantic import Field, model_validator

from promptpotter.application.bench.llm_call import (
    LLMCallContext,
    OptimizerResponseModel,
    run_optimizer_node,
)
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.optimizer_manifest import llm_node_document, running_prompt
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, OptimizerPromptTemplate
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS, TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store.dataset_access import (
    readable_task_context,
)
from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "CheckinOutput",
    "CheckinTaskContext",
    "campaign_framing",
    "checkin_call_context",
    "checkin_campaign_call_context",
    "commit_task_framing",
    "committed_task_context",
    "run_checkin",
]


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
    """Task decomposition leaves the origin block empty; origin resolution fills it AND the Layer-1 fields."""

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
            if not getattr(self, name).strip()
        ] + [
            f"{name} is {len(value)} chars (at most {ORIGIN_FIELD_MAX})"
            for name in PROMPT_STRING_FIELDS
            if len(value := getattr(self, name)) > ORIGIN_FIELD_MAX
        ]
        if problems:
            raise ValueError(
                "; ".join(problems) + " — these fields are the starting prompt; "
                "move domain detail into task_context."
            )
        return self


def campaign_framing(
    stores: Stores, campaign_config: CampaignConfig, dataset_name: str | None
) -> TaskDecomposition:
    if campaign_config.task_framing == "off":
        return TaskDecomposition()
    return committed_task_context(stores, dataset_name)


def committed_task_context(stores: Stores, dataset_name: str | None) -> TaskDecomposition:
    """A PURE read, so identity may use it: a decomposition needs the cycle a mint is still computing."""
    if dataset_name is None:
        return TaskDecomposition()
    task_context = readable_task_context(stores, dataset_name)
    # Refused here too: whichever seam reads the framing first must reject an over-budget field.
    task_context.check_budget(source=f"{dataset_name}/task_context.yaml")
    return task_context


def _checkin_template() -> OptimizerPromptTemplate:
    """No panel is filled into the check-in, so any other ``{{slot}}`` reaches the model literally."""
    _node, config, document = llm_node_document("checkin")
    template = running_prompt("checkin", config, document)
    if unknown := set(TEMPLATE_TOKEN_RE.findall(template.render())) - {"consultation_instruction"}:
        raise KeyError(f"Template 'checkin' references unknown slot(s): {sorted(unknown)}.")
    return template


async def run_checkin(
    *, consultation_instruction: str, user_content: str, context: LLMCallContext
) -> tuple[CheckinOutput, int]:
    raw, _prompt, repair_attempts = await run_optimizer_node(
        template_name="checkin",
        template=_checkin_template(),
        prompt_vars={"consultation_instruction": consultation_instruction},
        response_model=CheckinOutput,
        user_content=user_content,
        context=context,
    )
    assert isinstance(raw, CheckinOutput), (
        f"checkin must return CheckinOutput, got {type(raw).__name__}"
    )
    return raw, repair_attempts


def checkin_campaign_call_context(stores: Stores, campaign_id: str) -> LLMCallContext:
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise ValueError(f"check-in campaign {campaign_id!r} not found — cannot resolve its origin")
    return checkin_call_context(
        stores, CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(campaign.root_hop)))
    )


def checkin_call_context(stores: Stores, ledger: CycleEventLog) -> LLMCallContext:
    return LLMCallContext(ledger=ledger, round_num=0, cache=stores.optimizer_reuse)


async def commit_task_framing(
    stores: Stores,
    dataset_name: str,
    description: str,
    *,
    campaign_id: str,
    ledger: CycleEventLog,
) -> None:
    context = checkin_call_context(stores, ledger)
    token = set_cycle_ledger(ledger)
    try:
        result, _ = await run_checkin(
            consultation_instruction=(
                "Return a JSON object with exactly these keys. Be concise and actionable."
            ),
            user_content=(
                "The user has provided a raw context description. Parse it into "
                "structured Layer 1 prompt fields.\n\n"
                f"Context:\n{description}"
            ),
            context=context,
        )
    finally:
        reset_cycle_ledger(token)
    stores.tenant_datasets.save_task_context(
        dataset_name,
        TaskDecomposition.from_dict(
            {**result.task_context.model_dump(), "raw_description": description}
        ),
    )
