"""Task check-in: raw context → L1 prompt fields + task_context, cached as ``task_context.yaml``. Read and
write do NOT share a tier — read resolves tenant-then-install, a decomposition always lands in tenant."""

from __future__ import annotations

from pydantic import Field, model_validator

from promptpotter.application.bench.llm_call import (
    LLMCallContext,
    OptimizerResponseModel,
    run_optimizer_node,
)
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.optimizer_manifest import llm_node_document, running_prompt
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, OptimizerPromptTemplate
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import SpendBook, spending_under
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store.dataset_access import (
    readable_task_context,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.tracing.bridge import observed_node

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


# Per-field ceiling on a check-in-authored starting prompt, sized so every hand-authored origin
# field in `datasets/*/prompts/` fits under it bar one templated `instruction`.
ORIGIN_FIELD_MAX = 600

# ---------------------------------------------------------------------------
# checkin — one-time decomposition of user context into Layer-1 fields.
# ---------------------------------------------------------------------------


class CheckinTaskContext(OptimizerResponseModel):
    """Domain context inside the checkin output. Every field renders VERBATIM into every optimizer prompt and is frozen for
    the run, so an over-budget one is REFUSED at mint rather than clipped by a renderer. Overflow belongs elsewhere."""

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
    """One origin-readiness field the resolver proposes a value for. An UNCITED finding is rejected by the apply loop, and
    only ``confidence == "high"`` auto-confirms."""

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
    """One operator-facing question on an ``ask`` turn. ``field`` names the checklist field the answer resolves, so the
    panel applies it as a confirmed patch rather than the operator hunting for the control."""

    field: str = Field(
        default="", description="Checklist field id the answer resolves, e.g. 'column.query'."
    )
    prompt: str = Field(default="", description="Short operator-facing question.")
    options: list[str] = Field(
        default_factory=list,
        description="Optional closed set of acceptable answers; empty = free text.",
    )


class OriginNextAction(OptimizerResponseModel):
    """What the resolver wants next. The deterministic checklist — not this field — decides
    completeness, so a false ``ready`` is re-checked and rejected."""

    kind: str = Field(
        default="propose",
        description="'ask' (need operator input), 'propose' (findings applied), or 'ready' (resolver believes origin complete — re-checked).",
    )
    questions: list[OriginQuestion] = Field(
        default_factory=list,
        description="For kind='ask': operator-facing questions, each naming the field it resolves so the answer applies directly.",
    )


class CheckinOutput(OptimizerResponseModel):
    """Output of the checkin prompt. Two modes share one shape: task decomposition leaves the origin block empty, origin
    resolution fills it AND the Layer-1 fields — which seed the campaign's starting prompt either way."""

    # FIELD ORDER IS GENERATION ORDER, and the two the starting prompt cannot lack are REQUIRED
    # on the wire: optional and trailing, a constrained decoder closes the object without them.
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
    # Origin-resolution block — populated only on the web ingest check-in path.
    assessment: str = Field(default="", description="One-line read of the current origin state.")
    findings: list[OriginFinding] = Field(default_factory=list)
    next_action: OriginNextAction = Field(default_factory=OriginNextAction)
    recap: str = Field(
        default="",
        description="On a 'ready' turn: a jargon-free paragraph restating what the campaign will do, for the operator to confirm intent.",
    )

    @model_validator(mode="after")
    def _check_the_starting_prompt(self) -> CheckinOutput:
        # Parse-side only, never a wire `maxLength`: a constrained decoder honours that by
        # cutting the string mid-sentence, which is a broken origin rather than a short one.
        # Raised here, the message rides the schema-repair retry back to the model.
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
    """The framing a campaign's target renders splice in — its dataset's committed one, or none
    at all under ``task_framing: off``. Every render and identity of a campaign reads this."""
    if campaign_config.task_framing == "off":
        return TaskDecomposition()
    return committed_task_context(stores, dataset_name)


def committed_task_context(stores: Stores, dataset_name: str | None) -> TaskDecomposition:
    """The framing check-in committed, read PURELY — the half IDENTITY may use, since a
    decomposition needs a cycle to bill to and a mint is computing that cycle."""
    if dataset_name is None:
        return TaskDecomposition()
    task_context = readable_task_context(stores, dataset_name)
    # The budget is enforced HERE too, not only on the async path: whichever seam reads the
    # framing first is the one that must refuse an over-budget field, or the clip lands on
    # every render for the run's whole life.
    task_context.check_budget(source=f"{dataset_name}/task_context.yaml")
    return task_context


def _checkin_template() -> OptimizerPromptTemplate:
    """No panel is filled into the check-in, so its one caller-supplied token is all it may name —
    any other ``{{slot}}`` would reach the model literally."""
    _node, config, document = llm_node_document("checkin")
    template = running_prompt("checkin", config, document)
    if unknown := set(TEMPLATE_TOKEN_RE.findall(template.render())) - {"consultation_instruction"}:
        raise KeyError(f"Template 'checkin' references unknown slot(s): {sorted(unknown)}.")
    return template


async def run_checkin(
    *, consultation_instruction: str, user_content: str, context: LLMCallContext
) -> tuple[CheckinOutput, int]:
    """The ``checkin`` node's one call for both modes, and its repair attempts."""
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
    """The web turn's audit home — the check-in campaign's own cycle ledger. ``draft_id`` IS the
    ``campaign_id`` (re-keyed at ``create_checkin_campaign``); both modes bill through the one call."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise ValueError(f"check-in campaign {campaign_id!r} not found — cannot resolve its origin")
    return checkin_call_context(
        stores, CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(campaign.root_hop)))
    )


def checkin_call_context(stores: Stores, ledger: CycleEventLog) -> LLMCallContext:
    """The check-in call's audit home. The cache is what makes an unchanged decomposition free on
    replay; omitting it silently re-spends."""
    return LLMCallContext(ledger=ledger, round_num=0, cache=stores.optimizer_reuse)


async def commit_task_framing(
    stores: Stores,
    dataset_name: str,
    description: str,
    *,
    campaign_id: str,
    ledger: CycleEventLog,
    book: SpendBook,
) -> None:
    """Decompose ``description`` through the ``checkin`` node and COMMIT it as the dataset's
    framing, billed on ``ledger`` and admitted against ``book``."""
    context = checkin_call_context(stores, ledger)
    token = set_cycle_ledger(ledger)
    try:
        with spending_under(book):
            async with observed_node(
                "checkin",
                "llm",
                obs=None,
                campaign_id=campaign_id,
                round_num=0,
            ):
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
