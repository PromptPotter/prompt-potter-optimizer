from __future__ import annotations

import dataclasses
import enum
import json
import sys
import textwrap
import types
import typing
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, TypeAdapter
from pydantic.fields import ComputedFieldInfo, FieldInfo

# ruff: noqa: E402 -- we import from promptpotter after adjusting sys.path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from promptpotter.application.bench.task_context import OriginNextAction, OriginQuestion
from promptpotter.application.campaign_listing import CampaignListResponse, CampaignSummary
from promptpotter.application.commands.origin_resolving import ResolveOriginResponse
from promptpotter.application.commands.payloads import (
    CommandAcceptedBody,
    DatasetReplaced,
    EditDraftCampaignPayload,
    StartCheckinPayload,
    StartRunPayload,
)
from promptpotter.application.config_map import (
    ConfigCoupling,
    ConfigEstimandGroup,
    ConfigKnob,
    ConfigMapResponse,
)
from promptpotter.application.cycle_files import FileContentResponse, FileEntry, FilesResponse
from promptpotter.application.cycle_listing import CyclesResponse
from promptpotter.application.datasets.draft_build import DraftCampaignWire, DraftDependency
from promptpotter.application.datasets.draft_campaign import (
    EditDraftPatch,
    NodeOutputEdit,
    OptimizationOverrides,
)
from promptpotter.application.datasets.origin_readiness import (
    FieldGap,
    OriginLastResolution,
    OriginReadiness,
    OriginResolution,
    RaisedCommand,
)
from promptpotter.application.evidence.comparison import (
    ArmReplicate,
    Comparability,
    EvidencePower,
    EvidenceVariance,
    MetricReading,
    OrderConfound,
    PairwiseComparison,
)
from promptpotter.application.evidence.grid import (
    FactorCell,
    FactorGridReading,
    FactorLevel,
    FactorReading,
)
from promptpotter.application.evidence.head_to_head import (
    HeadToHead,
    HeadToHeadRow,
    PairGuard,
    SelectionPair,
)
from promptpotter.application.evidence.metric_catalogue import MetricSpec
from promptpotter.application.evidence.read import (
    ConfigKeys,
    EditSpread,
    EffectProvenance,
    Evidence,
    RankedEdit,
)
from promptpotter.application.evidence.subjects import (
    ScenarioReading,
    SubjectMask,
    SubjectReading,
    WinnerChainPoint,
)
from promptpotter.application.jobs.account_activity import ActivityBucket, ActivityResponse
from promptpotter.application.jobs.capacity import (
    MachineHolder,
    MachineNotice,
    MachineQueueEntry,
    MachineRefusal,
    MachineStatusResponse,
)
from promptpotter.application.jobs.launcher.checkin import StartCheckinResponse
from promptpotter.application.jobs.quota import QuotaStatus
from promptpotter.application.maintenance.archive_maintenance import ArchiveReport
from promptpotter.application.maintenance.storage_report import (
    CampaignStorageResponse,
    DatasetStorageEntry,
    DatasetStorageResponse,
    WorkspaceStorageEntry,
    WorkspaceStorageResponse,
)
from promptpotter.application.optimizer_manifest import (
    KnobRow,
    NodeKnobs,
    OptimizerEntry,
    OptimizerKnobsResponse,
    OptimizerRoster,
    StartPrompt,
)
from promptpotter.application.origin_listing import DatasetIndexEntry, OriginEntry
from promptpotter.application.pipeline_resolve import (
    CampaignPipelineResponse,
    CampaignRunsWith,
    DatasetPipelineResponse,
    OptimizerPipelineResponse,
    RunsWithParam,
    VendorModels,
)
from promptpotter.application.served_dashboard import (
    RoundAxis,
    SampleWalk,
    ServedDashboard,
    VerifyPassProgress,
    WarmingDashboard,
)
from promptpotter.domain.activity import (
    ActivityDecision,
    ActivityItem,
    ActivityState,
    DecisionAction,
    DecisionFact,
)
from promptpotter.domain.backend import ServedBackpressure
from promptpotter.domain.bench import (
    BandedValue,
    BenchReading,
    BenchScore,
    BenchStatus,
    DatasetSplit,
    LiftCost,
    OwnLevel,
    PassStop,
)
from promptpotter.domain.campaign import Arm, ArmBudget, BenchSet
from promptpotter.domain.cells import (
    Cell,
    CellCandidate,
    CellRow,
    CellSpan,
    CellsResponse,
    DatasetItem,
)
from promptpotter.domain.command_kinds import CommandKind, OriginGateDecisionPayload
from promptpotter.domain.cycle_listing import CycleListEntry, LineStanding, RunStatus
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import (
    DashboardCandidate,
    DashboardSample,
    LiveCandidate,
    OptimizerLimit,
    RunLimits,
    ServedRound,
)
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.opt_search_point import (
    EvidenceGrounding,
    IndividualLineage,
    OptSearchPoint,
    Variation,
)
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.paired_reading import (
    ArmPointer,
    CellSet,
    Coverage,
    EstimatorSpec,
    FlipCounts,
    LiftEstimate,
    Measurand,
    MeasuredLift,
    MemberAddress,
    PairedReading,
    PairMember,
    TestFamily,
)
from promptpotter.domain.phases import (
    PauseReading,
    ProducerAlert,
    ProducerReading,
    RunAdmission,
)
from promptpotter.domain.pipeline_schema import (
    ManifestNodeOverlay,
    ModelCapability,
    NestedPipelineRef,
    NodeConfigParam,
    NodeOutputSchema,
    NodeReach,
    NodeSearchNarrowing,
    ParamIntent,
    PipelineView,
    PipelineViewEdge,
    PipelineViewNode,
)
from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.domain.results import (
    ArmAbility,
    ArmElection,
    ArmPanel,
    ArmReading,
    ArmSpend,
    DegradationContext,
    DegradationHealth,
    DiagnosticRunRecord,
    LineRate,
    LivesReading,
    OptimizerFact,
    OverlapReading,
    RoundResult,
    RunStanding,
    ScoreboardRow,
    ScoredCandidate,
    VerifyReading,
)
from promptpotter.domain.round_audit import (
    LoopWarning,
    NodeBlock,
    NodeInput,
    NodeOutput,
    RoundAudit,
)
from promptpotter.domain.ruler import AbilityReading, RulerStanding
from promptpotter.domain.run_records import ConfigOverrides, CycleSeed, ForkRemainder, SpawnedBy
from promptpotter.domain.scoring import (
    SHEET_ROW,
    Diagnostics,
    JudgeReading,
    NodeWarning,
    PipelineData,
    RerunComparison,
    TurnRecord,
)
from promptpotter.domain.spend import (
    CloseSpend,
    KindSpend,
    MeteredSpend,
    PrefixReading,
    SpendBucket,
    SpendCeilings,
    SpendRollup,
    StepUsage,
    TokenAccount,
)
from promptpotter.domain.wire_record import record_of
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    BenchPassProgress,
    CatchUpLogEntry,
    CurrentRound,
    DashboardError,
    RacingBlock,
)
from promptpotter.infrastructure.store.account_spend import LifetimeSpend
from promptpotter.infrastructure.store.family_ray_queries import RayItem, RayResponse
from promptpotter.infrastructure.store.lineage_queries import (
    ArmNode,
    CourseNode,
    ForkStamp,
    LensShift,
    LineageDivergence,
    MainLineStep,
)
from promptpotter.main import HealthResponse
from promptpotter.presentation.api.routers.active import ActiveSessionResponse
from promptpotter.presentation.api.routers.auth import (
    ConnectedAccount,
    MeResponse,
    UserSettings,
)
from promptpotter.presentation.api.routers.backends import (
    BackendHealthResponse,
    BackendResponse,
)
from promptpotter.presentation.api.routers.campaigns.manifests import (
    CheckinReopenResponse,
    ForkPreviewResponse,
)
from promptpotter.presentation.api.routers.datasets.index import DatasetIndexResponse
from promptpotter.presentation.api.routers.diagnostics import DiagnosticRunListResponse
from promptpotter.presentation.api.routers.origins import OriginListResponse

EXPORTED_MODELS: list[type] = [
    # The emitter does not recurse: register every nested type, before its container.
    ArchiveReport,
    AbilityReading,
    VerifyReading,
    ArmPanel,
    PrefixReading,
    ArmSpend,
    ArmAbility,
    ArmElection,
    ArmReading,
    DashboardCandidate,
    DashboardSample,
    DegradationHealth,
    PanelPrecision,
    OverlapReading,
    LineRate,
    OptimizerFact,
    ServedRound,
    DiagnosticRunRecord,
    ValidationFailure,
    RuntimeFailure,
    DegradationContext,
    ScoredCandidate,
    ScoreboardRow,
    EvidenceGrounding,
    Variation,
    IndividualLineage,
    OptimizerState,
    OptSearchPoint,
    record_of(StepUsage),
    TurnRecord,
    record_of(NodeWarning),
    record_of(Diagnostics),
    record_of(JudgeReading),
    record_of(PipelineData),
    RerunComparison,
    SHEET_ROW,
    RoundResult,
    SpendBucket,
    SpendRollup,
    KindSpend,
    MeteredSpend,
    LifetimeSpend,
    ForkRemainder,
    ServedBackpressure,
    BackendWarning,
    LoopWarning,
    DashboardError,
    OptimizerLimit,
    SpendCeilings,
    RunLimits,
    CloseSpend,
    LivesReading,
    RunStanding,
    CatchUpLogEntry,
    RacingBlock,
    LiveCandidate,
    TokenAccount,
    NodeInput,
    NodeOutput,
    NodeBlock,
    RoundAudit,
    CurrentRound,
    BenchPassProgress,
    VerifyPassProgress,
    ArmPointer,
    ProducerAlert,
    ProducerReading,
    RunAdmission,
    PauseReading,
    SampleWalk,
    RoundAxis,
    ServedDashboard,
    WarmingDashboard,
    MemberAddress,
    PairMember,
    CellSet,
    Measurand,
    EstimatorSpec,
    LiftEstimate,
    TestFamily,
    FlipCounts,
    MeasuredLift,
    Coverage,
    PairedReading,
    DatasetItem,
    CellCandidate,
    CellRow,
    RulerStanding,
    CellsResponse,
    CellSpan,
    Cell,
    ModelCapability,
    NodeConfigParam,
    NodeOutputSchema,
    NodeReach,
    PipelineViewNode,
    PipelineViewEdge,
    PipelineView,
    NestedPipelineRef,
    DatasetPipelineResponse,
    ActiveSessionResponse,
    SpawnedBy,
    BandedValue,
    OwnLevel,
    BenchReading,
    PassStop,
    BenchStatus,
    LiftCost,
    BenchScore,
    CycleListEntry,
    RunStatus,
    LineStanding,
    CyclesResponse,
    CommandAcceptedBody,
    StartCheckinPayload,
    StartRunPayload,
    RunsWithParam,
    VendorModels,
    CampaignRunsWith,
    Arm,
    CampaignSummary,
    CampaignListResponse,
    CampaignPipelineResponse,
    ForkPreviewResponse,
    EffectProvenance,
    EditSpread,
    RankedEdit,
    ConfigKeys,
    Comparability,
    ArmReplicate,
    FactorCell,
    FactorGridReading,
    FactorLevel,
    FactorReading,
    EvidenceVariance,
    EvidencePower,
    OrderConfound,
    MetricSpec,
    SubjectMask,
    ScenarioReading,
    WinnerChainPoint,
    SubjectReading,
    PairwiseComparison,
    MetricReading,
    DatasetSplit,
    BenchSet,
    ArmBudget,
    PairGuard,
    HeadToHeadRow,
    SelectionPair,
    HeadToHead,
    Evidence,
    FileEntry,
    FilesResponse,
    FileContentResponse,
    CycleHop,
    LineageDivergence,
    ForkStamp,
    LensShift,
    MainLineStep,
    ArmNode,
    CourseNode,
    RayItem,
    RayResponse,
    ProjectionEnvelope,
    ActivityItem,
    DecisionFact,
    DecisionAction,
    ActivityDecision,
    ActivityState,
    DiagnosticRunListResponse,
    ConnectedAccount,
    MeResponse,
    QuotaStatus,
    UserSettings,
    ActivityBucket,
    ActivityResponse,
    HealthResponse,
    BackendResponse,
    BackendHealthResponse,
    MachineHolder,
    MachineNotice,
    MachineQueueEntry,
    MachineRefusal,
    MachineStatusResponse,
    StartPrompt,
    OptimizerPipelineResponse,
    DatasetIndexEntry,
    DatasetIndexResponse,
    OriginEntry,
    OriginListResponse,
    CampaignStorageResponse,
    WorkspaceStorageEntry,
    WorkspaceStorageResponse,
    DatasetStorageEntry,
    DatasetStorageResponse,
    OptimizerKnobsResponse,
    NodeKnobs,
    KnobRow,
    OptimizerRoster,
    OptimizerEntry,
    ConfigKnob,
    ConfigEstimandGroup,
    ConfigCoupling,
    ConfigMapResponse,
    ConfigOverrides,
    ManifestNodeOverlay,
    NodeSearchNarrowing,
    CycleSeed,
    OriginGateDecisionPayload,
    OptimizationOverrides,
    ParamIntent,
    NodeOutputEdit,
    EditDraftPatch,
    EditDraftCampaignPayload,
    FieldGap,
    OriginReadiness,
    DraftDependency,
    DraftCampaignWire,
    OriginQuestion,
    OriginNextAction,
    OriginLastResolution,
    RaisedCommand,
    OriginResolution,
    ResolveOriginResponse,
    CheckinReopenResponse,
    StartCheckinResponse,
    DatasetReplaced,
]

_OUT_PATH = _REPO / "webapp" / "lib" / "api" / "types.generated.ts"


def _is_none_type(t: typing.Any) -> bool:
    return t is type(None)


def _array_of(member: typing.Any) -> str:
    rendered = _emit_type(member)
    return f"({rendered})[]" if " | " in rendered else f"{rendered}[]"


def _emit_type(annotation: typing.Any) -> str:
    if annotation is typing.Any:
        return "unknown"
    if annotation is str:
        return "string"
    if annotation is int or annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if _is_none_type(annotation):
        return "null"
    if isinstance(annotation, typing.TypeAliasType):
        return _emit_type(annotation.__value__)

    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return " | ".join(
            repr(m.value) if isinstance(m.value, str) else str(m.value) for m in annotation
        )

    if typing.is_typeddict(annotation):
        return str(annotation.__name__)

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Required, typing.NotRequired):
        return _emit_type(args[0])

    if origin in (typing.Union, types.UnionType):
        has_none = any(_is_none_type(a) for a in args)
        non_none = [a for a in args if not _is_none_type(a)]
        rendered = " | ".join(_emit_type(a) for a in non_none)
        return f"{rendered} | null" if has_none else rendered

    if origin is typing.Literal:
        # `json.dumps` for a non-string: a Python `True` is not a TypeScript literal.
        return " | ".join(repr(a) if isinstance(a, str) else json.dumps(a) for a in args)

    if origin in (list, set, frozenset):
        (inner,) = args
        return _array_of(inner)

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return _array_of(args[0])
        rendered = ", ".join(_emit_type(a) for a in args)
        return f"[{rendered}]"

    if origin is dict:
        # Pydantic JSON keys are always strings on the wire even when typed as int.
        _k, v_type = args
        return f"Record<string, {_emit_type(v_type)}>"

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__ if annotation.model_fields else "Record<string, unknown>"

    if isinstance(annotation, type) and "__get_pydantic_core_schema__" in vars(annotation):
        declared = TypeAdapter(annotation).json_schema(mode="serialization")
        return f"{declared['items']['$ref'].rpartition('/')[2]}[]"

    if dataclasses.is_dataclass(annotation) and annotation in EXPORTED_MODELS:
        return str(annotation.__name__)

    return "unknown"


def _emit_field(name: str, info: FieldInfo, annotation: typing.Any) -> str:
    # Pydantic serializes defaults on the wire ⇒ no `?` in TS, only `| null` for Optional.
    ts_type = _emit_type(annotation)
    description = info.description
    comment_block = ""
    if description:
        wrapped = textwrap.fill(description, width=78, subsequent_indent="   * ")
        comment_block = f"  /** {wrapped} */\n"
    return f"{comment_block}  {info.alias or name}: {ts_type};"


def _resolved_hints(model: type[BaseModel]) -> dict[str, typing.Any]:
    """``model_fields[...].annotation`` can hold a bare ``ForwardRef``, which emits ``unknown``."""
    return typing.get_type_hints(model)


def _computed_return_type(model: type[BaseModel], name: str) -> typing.Any:
    """``ComputedFieldInfo.return_type`` is undefined under ``from __future__ import annotations``."""
    prop = getattr(model, name)
    fget = getattr(prop, "fget", None) or prop
    return typing.get_type_hints(fget).get("return", typing.Any)


def _emit_computed_field(model: type[BaseModel], name: str, info: ComputedFieldInfo) -> str:
    ts_type = _emit_type(_computed_return_type(model, name))
    doc = (info.description or "").strip()
    comment_block = ""
    if doc:
        wrapped = textwrap.fill(doc, width=78, subsequent_indent="   * ")
        comment_block = f"  /** {wrapped} */\n"
    return f"{comment_block}  {name}: {ts_type};"


def _emit_enum_union(enum_cls: type[enum.Enum], note: str) -> str:
    members = " | ".join(repr(m.value) for m in enum_cls)
    return f"// {note}\nexport type {enum_cls.__name__} = {members};"


def _emit_lifecycle_filter() -> str:
    from promptpotter.domain.campaign import LifecycleFilter

    members = " | ".join(repr(m) for m in typing.get_args(LifecycleFilter.__value__))
    note = "`GET /campaigns?lifecycle=` (domain/campaign.py::LifecycleFilter); absent = 'active'."
    return f"// {note}\nexport type LifecycleFilter = {members};"


def _emit_arm_outcomes_ended_early() -> str:
    from promptpotter.domain.results import ArmOutcome

    members = ", ".join(repr(o.value) for o in ArmOutcome if o.ended_early)
    return (
        "// The outcomes whose walk stopped before its panel "
        "(domain/results.py::ArmOutcome.ended_early).\n"
        f"export const ARM_OUTCOMES_ENDED_EARLY: readonly ArmOutcome[] = [{members}];"
    )


def _emit_command_kinds() -> str:
    members = " | ".join(repr(k.value) for k in sorted(CommandKind))
    note = "Every kind `POST /commands/{kind}` dispatches (domain/command_kinds.py)."
    return f"// {note}\nexport type CommandKind = {members};"


def _emit_stop_reason_tables() -> str:
    from promptpotter.domain.phases import STOP_REASON_INFO, StopCategory, StopOutcome, StopReason

    def table(column: str) -> str:
        return "\n".join(
            f"  {reason.value!r}: {str(getattr(info, column))!r},"
            for reason, info in STOP_REASON_INFO.items()
        )

    outcome_union = " | ".join(repr(o.value) for o in StopOutcome)
    category_union = " | ".join(repr(c.value) for c in StopCategory)
    return (
        _emit_enum_union(StopReason, "Why a cycle ended (domain/phases.py::StopReason).") + "\n\n"
        "// Whether a stop SUCCEEDED, and the only half of the table that decides anything —\n"
        "// `StopOutcome`, where `paused` is the one non-terminal member. Ask it rather than\n"
        "// matching names: a hand-listed set of crash names rots in both directions.\n"
        f"export type StopOutcome = {outcome_union};\n"
        "export const STOP_REASON_OUTCOMES: Record<StopReason, StopOutcome> = {\n"
        f"{table('outcome')}\n"
        "};\n\n"
        "// WHAT ended a run (`StopCategory`) — a budget, a declared limit, the search itself, an\n"
        "// operator, the outside world or a failure. Ask it rather than listing reasons.\n"
        f"export type StopCategory = {category_union};\n"
        "export const STOP_REASON_CATEGORIES: Record<StopReason, StopCategory> = {\n"
        f"{table('category')}\n"
        "};"
    )


def _emit_run_phase_tables() -> str:
    from promptpotter.domain.phases import DASHBOARD_STATE_PAUSE_WORDS, RUN_PHASE_INFO

    columns = (
        "label",
        "walks",
        "settled",
        "authoring",
        "awaits_operator",
        "dock_priority",
        "parked",
        "parked_attached",
    )
    rows = "\n".join(
        f"  {phase.value!r}: {{ "
        + ", ".join(f"{column}: {json.dumps(getattr(info, column))}" for column in columns)
        + " },"
        for phase, info in RUN_PHASE_INFO.items()
    )
    words = "\n".join(
        f"  {state.value!r}: {json.dumps(said)},"
        for state, said in DASHBOARD_STATE_PAUSE_WORDS.items()
    )
    return (
        "// What each run phase IS. Mirror of domain/phases.py::RUN_PHASE_INFO — the single\n"
        "// source of a phase's label and facts; the terminal reads the same rows.\n"
        "export interface RunPhaseInfo {\n"
        "  label: string;\n"
        "  walks: boolean;\n"
        "  settled: boolean;\n"
        "  authoring: boolean;\n"
        "  awaits_operator: boolean;\n"
        "  dock_priority: number;\n"
        "  parked: string;\n"
        "  parked_attached: string;\n"
        "}\n"
        "export const RUN_PHASE_INFO: Record<RunPhase, RunPhaseInfo> = {\n"
        f"{rows}\n"
        "};\n\n"
        "// What a run asked to pause is finishing, per `dashboard.json::state`. Mirror of\n"
        '// domain/phases.py::DASHBOARD_STATE_PAUSE_WORDS; `""` names nothing worth saying.\n'
        "export const DASHBOARD_STATE_PAUSE_WORDS: Record<DashboardState, string> = {\n"
        f"{words}\n"
        "};"
    )


def _emit_reading_state_tables() -> str:
    from promptpotter.domain.paired_reading import (
        READING_STATE_INFO,
        ReadingState,
        ReadingStateKind,
    )

    def table(column: str) -> str:
        return "\n".join(
            f"  {state.value!r}: {str(getattr(info, column))!r},"
            for state, info in READING_STATE_INFO.items()
        )

    return (
        _emit_enum_union(
            ReadingState,
            "Whether a pair was read, and why not (domain/paired_reading.py::ReadingState).",
        )
        + "\n\n"
        + _emit_enum_union(
            ReadingStateKind,
            "What kind of answer a ReadingState is: waiting is not a fault, refused is not absent.",
        )
        + "\n\n"
        "// The served label and sentence per state. Mirror of\n"
        "// domain/paired_reading.py::READING_STATE_INFO — the single wording source.\n"
        "export const READING_STATE_LABELS: Record<ReadingState, string> = {\n"
        f"{table('label')}\n"
        "};\n\n"
        "export const READING_STATE_SENTENCES: Record<ReadingState, string> = {\n"
        f"{table('sentence')}\n"
        "};\n\n"
        "export const READING_STATE_KINDS: Record<ReadingState, ReadingStateKind> = {\n"
        f"{table('kind')}\n"
        "};"
    )


def _emit_round_advance_tables() -> str:
    from promptpotter.domain.results import ROUND_ADVANCE_INFO, RoundAdvance

    def table(column: str) -> str:
        return "\n".join(
            f"  {advance.value!r}: {str(getattr(info, column))!r},"
            for advance, info in ROUND_ADVANCE_INFO.items()
        )

    return (
        _emit_enum_union(
            RoundAdvance,
            "What a closed round did to the best-so-far line (domain/results.py::RoundAdvance).",
        )
        + "\n\n"
        "// The served label and sentence per advance. Mirror of\n"
        "// domain/results.py::ROUND_ADVANCE_INFO — the single wording source.\n"
        "export const ROUND_ADVANCE_LABELS: Record<RoundAdvance, string> = {\n"
        f"{table('label')}\n"
        "};\n\n"
        "export const ROUND_ADVANCE_SENTENCES: Record<RoundAdvance, string> = {\n"
        f"{table('sentence')}\n"
        "};"
    )


def _emit_spend_tables() -> str:
    from promptpotter.domain.spend import (
        CEILING_METER_LABELS,
        PREFIX_STATE_TITLES,
        RATE_PRICED_LABEL,
        SPEND_KIND_LABELS,
    )

    meter_keys = " | ".join(repr(m) for m in CEILING_METER_LABELS)
    meters = "\n".join(f"  {meter!r}: {word!r}," for meter, word in CEILING_METER_LABELS.items())
    kinds = " | ".join(repr(k) for k in SPEND_KIND_LABELS)
    rows = "\n".join(
        f"  {{ key: {key!r}, label: {label!r} }}," for key, label in SPEND_KIND_LABELS.items()
    )
    states = " | ".join(repr(s) for s in PREFIX_STATE_TITLES)
    titles = "\n".join(f"  {state!r}: {title!r}," for state, title in PREFIX_STATE_TITLES.items())
    return (
        "// Who spent it (domain/spend.py::TokenUsageKind).\n"
        f"export type SpendKind = {kinds};\n\n"
        "// Each kind's word, in display order. Mirror of domain/spend.py::SPEND_KIND_LABELS.\n"
        "export const SPEND_KINDS: readonly { key: SpendKind; label: string }[] = [\n"
        f"{rows}\n"
        "];\n\n"
        "// A provider prefix-cache reading's state (domain/spend.py::PrefixState).\n"
        f"export type PrefixState = {states};\n\n"
        "// What each state means. Mirror of domain/spend.py::PREFIX_STATE_TITLES.\n"
        "export const PREFIX_STATE_TITLES: Record<PrefixState, string> = {\n"
        f"{titles}\n"
        "};\n\n"
        "// The word beside a figure a ceiling counted. Mirror of "
        "domain/spend.py::CEILING_METER_LABELS.\n"
        f"export const CEILING_METER_LABELS: Record<{meter_keys}, string> = {{\n"
        f"{meters}\n"
        "};\n\n"
        "// The word beside a figure our rate table priced, which is never spent. Mirror of "
        "domain/spend.py::RATE_PRICED_LABEL.\n"
        f"export const RATE_PRICED_LABEL = {RATE_PRICED_LABEL!r};"
    )


def _emit_theta_caveat_table() -> str:
    from promptpotter.domain.ruler import THETA_CAVEAT_INFO

    members = " | ".join(repr(c.value) for c in THETA_CAVEAT_INFO)
    rows = "\n".join(
        f"  {caveat.value!r}: {{ head: {json.dumps(info.head, ensure_ascii=False)}, "
        f"body: {json.dumps(info.body, ensure_ascii=False)} }},"
        for caveat, info in THETA_CAVEAT_INFO.items()
    )
    return (
        "// What each θ caveat says. Mirror of domain/ruler.py::THETA_CAVEAT_INFO — the single\n"
        "// wording; the terminal prints the same `head`.\n"
        f"export const THETA_CAVEAT_INFO: Record<{members}, {{ head: string; body: string }}> = {{\n"
        f"{rows}\n"
        "};"
    )


def _emit_label_tables() -> str:
    from promptpotter.application.evidence.head_to_head import GUARD_STATE_LABELS
    from promptpotter.application.evidence.subjects import SUBJECT_KIND_LABELS
    from promptpotter.domain.dashboard_rows import SAMPLE_MOVEMENT_LABELS
    from promptpotter.domain.results import ARM_VERDICT_LABELS
    from promptpotter.domain.run_records import MINT_KIND_LABELS

    def table(name: str, owner: str, labels: Mapping[typing.Any, str]) -> str:
        members = " | ".join(repr(str(member)) for member in labels)
        rows = "\n".join(
            f"  {str(member)!r}: {json.dumps(label, ensure_ascii=False)},"
            for member, label in labels.items()
        )
        return (
            f"// Mirror of {owner}::{name} — the single wording.\n"
            f"export const {name}: Record<{members}, string> = {{\n{rows}\n}};"
        )

    return "\n\n".join(
        [
            table("ARM_VERDICT_LABELS", "domain/results.py", ARM_VERDICT_LABELS),
            table("GUARD_STATE_LABELS", "application/evidence/head_to_head.py", GUARD_STATE_LABELS),
            table("MINT_KIND_LABELS", "domain/run_records.py", MINT_KIND_LABELS),
            table("SAMPLE_MOVEMENT_LABELS", "domain/dashboard_rows.py", SAMPLE_MOVEMENT_LABELS),
            table("SUBJECT_KIND_LABELS", "application/evidence/subjects.py", SUBJECT_KIND_LABELS),
        ]
    )


def _emit_display_metrics() -> str:
    from promptpotter.domain.results import DISPLAY_METRIC_INFO

    members = " | ".join(repr(m) for m in DISPLAY_METRIC_INFO)
    rows = "\n".join(
        f"  {{ id: {metric!r}, label: {info.label!r}, glyph: {info.glyph!r},"
        f" title: {info.title!r} }},"
        for metric, info in DISPLAY_METRIC_INFO.items()
    )
    labels = "\n".join(
        f"  {metric!r}: {info.label!r}," for metric, info in DISPLAY_METRIC_INFO.items()
    )
    return (
        "// A column every arm carries (domain/results.py::DisplayMetric).\n"
        f"export type DisplayMetric = {members};\n\n"
        "// Each column's words, in pick order. Mirror of domain/results.py::DISPLAY_METRIC_INFO.\n"
        "export const DISPLAY_METRICS: readonly {\n"
        "  id: DisplayMetric;\n  label: string;\n  glyph: string;\n  title: string;\n}[] = [\n"
        f"{rows}\n"
        "];\n\n"
        "export const DISPLAY_METRIC_LABELS: Record<DisplayMetric, string> = {\n"
        f"{labels}\n"
        "};"
    )


def _emit_verify_strategy_labels() -> str:
    from promptpotter.domain.results import VERIFY_STRATEGY_LABELS

    rows = "\n".join(f"  {name!r}: {label!r}," for name, label in VERIFY_STRATEGY_LABELS.items())
    return (
        "// How a verify pass's pick of fresh cells reads, by strategy. Mirror of\n"
        "// domain/results.py::VERIFY_STRATEGY_LABELS.\n"
        'export const VERIFY_STRATEGY_LABELS: Record<VerifyReading["strategy"], string> = {\n'
        f"{rows}\n"
        "};"
    )


def _emit_abort_lens_labels() -> str:
    from promptpotter.application.optimizers.potter.pobb.checks import ABORT_LENS_LABELS

    rows = "\n".join(f"  {variant!r}: {label!r}," for variant, label in ABORT_LENS_LABELS.items())
    return (
        "// Abort-lens variant -> operator label, in picklist order. Mirror of\n"
        "// pobb/checks.py::ABORT_LENS_LABELS, whose keys are asserted against\n"
        "// `ABORT_LENS_SUPPRESS` at import. Don't hand-list these.\n"
        "export const ABORT_LENS_LABELS: Record<string, string> = {\n"
        f"{rows}\n"
        "};"
    )


def _emit_cell_term_meta() -> str:
    from promptpotter.application.scoring.evaluators import cell_terms_meta

    rows = "\n".join(
        f"  {{ name: {m['name']!r}, direction: {m['direction']!r},"
        f" description: {m['description']!r}, dial: {json.dumps(m['dial'])},"
        f" primary: {json.dumps(m['primary'])} }},"
        for m in cell_terms_meta()
    )
    return (
        "export interface CellTermMeta {\n"
        "  name: string;\n"
        '  direction: "high" | "low";\n'
        "  description: string;\n"
        '  dial: "anchored" | "unit" | null;\n'
        "  primary: boolean;\n"
        "}\n\n"
        "// What a per_cell formula can name, mirrored from application/scoring/evaluators.py.\n"
        "export const CELL_TERM_META: CellTermMeta[] = [\n"
        f"{rows}\n"
        "];"
    )


def _emit_pipeline_schema_words() -> str:
    from promptpotter.domain.pipeline_schema import (
        MOVABLE_AGENT_LABELS,
        SCHEMA_DESCRIPTION_PREFIX,
    )

    agents = " | ".join(json.dumps(a) for a in MOVABLE_AGENT_LABELS)
    rows = "\n".join(
        f"  {agent}: {json.dumps(label, ensure_ascii=False)},"
        for agent, label in MOVABLE_AGENT_LABELS.items()
    )
    return (
        "// Who may move a search axis, worded. Mirror of\n"
        "// domain/pipeline_schema.py::MOVABLE_AGENT_LABELS.\n"
        f"export const MOVABLE_AGENT_LABELS: Record<{agents}, string> = {{\n"
        f"{rows}\n"
        "};\n\n"
        "// domain/pipeline_schema.py::SCHEMA_DESCRIPTION_PREFIX.\n"
        f"export const SCHEMA_DESCRIPTION_PREFIX = {json.dumps(SCHEMA_DESCRIPTION_PREFIX)};"
    )


def _emit_prompt_string_fields() -> str:
    from promptpotter.domain.search_point import PROMPT_STRING_FIELDS

    rows = "\n".join(f"  {name!r}," for name in PROMPT_STRING_FIELDS).replace("'", '"')
    return (
        "// The PromptTemplate decomposition field SET. Mirror of\n"
        "// domain/search_point.py::PROMPT_STRING_FIELDS — canonical MEMBERSHIP only, since each\n"
        "// prompt kind orders its own render (PromptTemplate.RENDER_ORDER). Don't hand-list these.\n"
        "export const PROMPT_STRING_FIELDS = [\n"
        f"{rows}\n"
        "] as const;"
    )


def _emit_recent_step() -> str:
    from promptpotter.infrastructure.runtime_flags import RECENT_STEP_S

    return (
        "// The shortest silence between two ray steps worth marking. Mirror of\n"
        "// infrastructure/runtime_flags.py::RECENT_STEP_S, the window a producer reads `live` in.\n"
        f"export const RECENT_STEP_S = {RECENT_STEP_S};"
    )


def _emit_cycle_path_grammar() -> str:
    from promptpotter.domain.cycle_paths import (
        ALL_DOTS_PATTERN,
        HOP_SEP,
        ID_COMPONENT_PATTERN,
        UNIT_SEP,
    )

    for pattern in (ID_COMPONENT_PATTERN, ALL_DOTS_PATTERN):
        if "/" in pattern:
            raise ValueError(
                f"cycle-path pattern {pattern!r} contains '/', which cannot ride a TS regex "
                "literal unescaped. Escape it here deliberately rather than emitting a regex "
                "that differs from the Python one."
            )

    return (
        "// The cycle-address grammar. Mirror of domain/cycle_paths.py, which owns it and\n"
        "// asserts at import that no separator matches the id charset — the precondition that\n"
        "// makes encode/decode round-trip. Don't hand-declare these.\n"
        "//\n"
        "// Two deliberate asymmetries with the Python side, both correct, neither drift:\n"
        "//  - decodeCyclePath('') is null here and () there. A CyclePath is non-empty by\n"
        "//    construction in the browser; in Python () is a real value meaning depth 1.\n"
        "//  - This decoder validates the charset inline; the Python one defers to\n"
        "//    descend_store, which must validate anyway because it also receives hops the\n"
        "//    codec never produced. The browser has no such downstream boundary.\n"
        # `json.dumps`, not `!r`: a Python repr picks single quotes, which the file's style forbids.
        f"export const CYCLE_PATH_HOP_SEP = {json.dumps(HOP_SEP)};\n"
        f"export const CYCLE_PATH_UNIT_SEP = {json.dumps(UNIT_SEP)};\n"
        f"export const ID_COMPONENT_RE = /{ID_COMPONENT_PATTERN}/;\n"
        f"export const ALL_DOTS_RE = /{ALL_DOTS_PATTERN}/;"
    )


def _doc_block(owner: type) -> str:
    doc = (owner.__doc__ or "").strip().splitlines()
    return f"/** {doc[0]} */\n" if doc else ""


def _emit_record(record: type) -> str:
    # A dataclass serializes every field, so none is optional on the wire.
    is_dataclass = dataclasses.is_dataclass(record)
    hints = typing.get_type_hints(record, include_extras=not is_dataclass)
    if is_dataclass:
        hints = {f.name: hints[f.name] for f in dataclasses.fields(record)}
    # `__optional_keys__` misses a `NotRequired` spelled under `from __future__ import annotations`.
    optional = set(vars(record).get("__optional_keys__", ())) | {
        name for name, hint in hints.items() if typing.get_origin(hint) is typing.NotRequired
    }
    body_lines = [
        f"  {name}{'?' if name in optional else ''}: {_emit_type(hint)};"
        for name, hint in sorted(hints.items(), key=lambda item: item[0] in optional)
    ]
    if getattr(record, "__pydantic_config__", {}).get("extra") == "allow":
        body_lines.append("  [key: string]: unknown;")
    return (
        f"{_doc_block(record)}export interface {record.__name__} {{\n"
        + "\n".join(body_lines)
        + "\n}"
    )


def _emit_interface(model: type) -> str:
    if not issubclass(model, BaseModel):
        return _emit_record(model)
    hints = _resolved_hints(model)
    body_lines = [
        _emit_field(name, info, hints.get(name, info.annotation))
        for name, info in model.model_fields.items()
    ]
    body_lines += [
        _emit_computed_field(model, name, info)
        for name, info in model.model_computed_fields.items()
    ]
    if model.model_config.get("extra") == "allow":
        body_lines.append("  [key: string]: unknown;")
    return (
        f"{_doc_block(model)}export interface {model.__name__} {{\n" + "\n".join(body_lines) + "\n}"
    )


_HEADER = """\
// AUTOGENERATED by scripts/build_ts_types.py — do not hand-edit.
// Run `python scripts/build_ts_types.py` to regenerate from the Pydantic
// models in `promptpotter/` and commit the diff alongside any schema change.

"""


def main() -> int:
    from promptpotter.domain.phases import DashboardState, ProducerState, RunPhase
    from promptpotter.domain.results import ArmOutcome

    blocks = [_emit_interface(model) for model in EXPORTED_MODELS]
    blocks.append(
        _emit_enum_union(
            ArmOutcome, "How an arm's measurement ended (domain/results.py::ArmOutcome)."
        )
    )
    blocks.append(_emit_arm_outcomes_ended_early())
    blocks.append(
        _emit_enum_union(RunPhase, "The coarse run-state axis (domain/phases.py::RunPhase).")
    )
    blocks.append(
        _emit_enum_union(
            ProducerState,
            "What a cycle's producer is doing now (domain/phases.py::ProducerState).",
        )
    )
    blocks.append(
        _emit_enum_union(
            DashboardState,
            "The fine-grained activity axis, `dashboard.json::state` "
            "(domain/phases.py::DashboardState).",
        )
    )
    blocks.append(_emit_lifecycle_filter())
    blocks.append(_emit_command_kinds())
    blocks.append(_emit_stop_reason_tables())
    blocks.append(_emit_run_phase_tables())
    blocks.append(_emit_reading_state_tables())
    blocks.append(_emit_round_advance_tables())
    blocks.append(_emit_spend_tables())
    blocks.append(_emit_theta_caveat_table())
    blocks.append(_emit_label_tables())
    blocks.append(_emit_pipeline_schema_words())
    blocks.append(_emit_display_metrics())
    blocks.append(_emit_verify_strategy_labels())
    blocks.append(_emit_abort_lens_labels())
    blocks.append(_emit_cell_term_meta())
    blocks.append(_emit_recent_step())
    blocks.append(_emit_cycle_path_grammar())
    blocks.append(_emit_prompt_string_fields())
    content = _HEADER + "\n\n".join(blocks) + "\n"
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    prior = _OUT_PATH.read_text(encoding="utf-8") if _OUT_PATH.is_file() else ""
    if prior == content:
        print(f"{_OUT_PATH.relative_to(_REPO)} — up to date.")
        return 0
    _OUT_PATH.write_text(content, encoding="utf-8")
    print(f"{_OUT_PATH.relative_to(_REPO)} — regenerated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
