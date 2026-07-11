"""Generate TS interfaces from the Pydantic models in :data:`EXPORTED_MODELS`
→ ``webapp/lib/api/types.generated.ts``. CI re-runs this; non-empty diff fails."""

from __future__ import annotations

import enum
import sys
import textwrap
import types
import typing
from pathlib import Path

from pydantic import BaseModel
from pydantic.fields import ComputedFieldInfo, FieldInfo

# ruff: noqa: E402 -- we import from promptpotter after adjusting sys.path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from promptpotter.domain.escalation_signals import RuntimeFailure, ValidationFailure
from promptpotter.domain.l1_layout import L1Layout
from promptpotter.domain.opt_search_point import (
    EvidenceGrounding,
    FewShotExample,
    IndividualLineage,
    L1SituationalExample,
    L1SupplementalRule,
    L2L3Memory,
    OptSearchPoint,
    WoundChannels,
)
from promptpotter.domain.outer_verdict import OuterCellEffect, OuterVerdict
from promptpotter.domain.pipeline_schema import NodeConfigParam, NodeOutputSchema
from promptpotter.domain.results import (
    DegradationHealth,
    DiagnosticRunRecord,
    RoundResult,
    RoundSummary,
    RoundSummaryCandidate,
    SampleOrderStep,
    ScoreboardRow,
    ScoredCandidate,
)
from promptpotter.infrastructure.projections.live_dashboard.state import (
    BackendWarning,
    BackfillLogEntry,
    DashboardError,
    InFlightCall,
    LiveDashboardState,
    LoopWarning,
    RunLimits,
    SpendBucket,
    SpendRollup,
)
from promptpotter.presentation.api.middleware.command_dispatcher import (
    CommandAcceptedBody,
)
from promptpotter.presentation.api.routers.active import (
    ActiveSessionResponse,
    CycleListEntry,
    CyclesResponse,
)
from promptpotter.presentation.api.routers.campaigns.files import (
    FileContentResponse,
    FileEntry,
    FilesResponse,
)
from promptpotter.presentation.api.routers.campaigns.lineage import (
    CampaignLineageCandidate,
    CampaignLineageCycle,
    CampaignLineageResponse,
    CampaignLineageRound,
    LineageDivergence,
)
from promptpotter.presentation.api.routers.campaigns.registry import (
    CampaignListResponse,
    CampaignSummary,
)
from promptpotter.presentation.api.routers.datasets import (
    DatasetItem,
    DatasetPipelineResponse,
    DatasetPreviewResponse,
    MeasurementDot,
    MeasurementSeriesResponse,
    SampleSeries,
)
from promptpotter.presentation.api.routers.verify import DiagnosticRunListResponse

EXPORTED_MODELS: list[type[BaseModel]] = [
    # Nested types first so the TS file reads top-down.
    RoundSummaryCandidate,
    DegradationHealth,
    OuterCellEffect,
    OuterVerdict,
    RoundSummary,
    DiagnosticRunRecord,
    # --- the round document (`rounds/round_NNNN.json` IS `RoundResult.model_dump()`;
    # also the `GET /rounds/{n}` response model). Nested graph, dependencies first. ---
    ValidationFailure,
    RuntimeFailure,
    ScoredCandidate,
    ScoreboardRow,
    SampleOrderStep,
    FewShotExample,
    EvidenceGrounding,
    IndividualLineage,
    WoundChannels,
    L1Layout,
    L1SupplementalRule,
    L1SituationalExample,
    L2L3Memory,
    OptSearchPoint,
    RoundResult,
    SpendBucket,
    SpendRollup,
    # --- dashboard.json IS `LiveDashboardState` (the webapp polls it every 2s). It was
    # hand-declared webapp-side with an index signature that typechecked anything. ---
    BackendWarning,
    LoopWarning,
    DashboardError,
    RunLimits,
    InFlightCall,
    BackfillLogEntry,
    LiveDashboardState,
    # --- datasets router ---
    DatasetItem,
    DatasetPreviewResponse,
    MeasurementDot,
    SampleSeries,
    MeasurementSeriesResponse,
    NodeConfigParam,
    NodeOutputSchema,
    DatasetPipelineResponse,
    # --- active router ---
    ActiveSessionResponse,
    CycleListEntry,
    CyclesResponse,
    # --- commands middleware ---
    CommandAcceptedBody,
    # --- campaigns/registry router ---
    CampaignSummary,
    CampaignListResponse,
    # --- campaigns/files router ---
    FileEntry,
    FilesResponse,
    FileContentResponse,
    # --- campaigns/lineage router ---
    CampaignLineageCandidate,
    CampaignLineageRound,
    CampaignLineageCycle,
    LineageDivergence,
    CampaignLineageResponse,
    # --- verify router ---
    DiagnosticRunListResponse,
]

_OUT_PATH = _REPO / "webapp" / "lib" / "api" / "types.generated.ts"


def _is_none_type(t: typing.Any) -> bool:
    return t is type(None)


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

    # An enum is its value set. Emitting `unknown` (the old fall-through) let the webapp
    # hand-write the union instead — and its `run_phase` union was missing two of the six
    # RunPhase members. A name-set the compiler didn't derive goes stale in silence.
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return " | ".join(
            repr(m.value) if isinstance(m.value, str) else str(m.value) for m in annotation
        )

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Union, types.UnionType):
        has_none = any(_is_none_type(a) for a in args)
        non_none = [a for a in args if not _is_none_type(a)]
        rendered = " | ".join(_emit_type(a) for a in non_none)
        return f"{rendered} | null" if has_none else rendered

    if origin is typing.Literal:
        return " | ".join(repr(a) if isinstance(a, str) else str(a) for a in args)

    if origin in (list, set, frozenset):
        (inner,) = args
        return f"{_emit_type(inner)}[]"

    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return f"{_emit_type(args[0])}[]"
        rendered = ", ".join(_emit_type(a) for a in args)
        return f"[{rendered}]"

    if origin is dict:
        # Pydantic JSON keys are always strings on the wire even when typed as int.
        _k, v_type = args
        return f"Record<string, {_emit_type(v_type)}>"

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__

    return "unknown"


def _emit_field(name: str, info: FieldInfo, annotation: typing.Any) -> str:
    # Pydantic serializes defaults on the wire ⇒ no `?` in TS, only `| null` for Optional.
    ts_type = _emit_type(annotation)
    description = info.description
    comment_block = ""
    if description:
        wrapped = textwrap.fill(description, width=78, subsequent_indent="   * ")
        comment_block = f"  /** {wrapped} */\n"
    return f"{comment_block}  {name}: {ts_type};"


def _resolved_hints(model: type[BaseModel]) -> dict[str, typing.Any]:
    """Field annotations with forward refs resolved.

    ``model_fields[...].annotation`` can still hold a bare ``ForwardRef`` when the
    referent is defined below the model in its own module (pydantic resolves it inside
    the core schema, but never writes it back). Emitting that yields ``unknown``.
    """
    return typing.get_type_hints(model)


def _computed_return_type(model: type[BaseModel], name: str) -> typing.Any:
    """A computed field's return type, read off the underlying property.

    ``ComputedFieldInfo.return_type`` is ``PydanticUndefined`` under
    ``from __future__ import annotations`` (the annotation is still a string), so go to
    the getter and resolve it there.
    """
    prop = getattr(model, name)
    fget = getattr(prop, "fget", None) or prop
    return typing.get_type_hints(fget).get("return", typing.Any)


def _emit_computed_field(model: type[BaseModel], name: str, info: ComputedFieldInfo) -> str:
    """A ``@computed_field`` — ``model_dump`` emits it, so it is part of the wire type."""
    ts_type = _emit_type(_computed_return_type(model, name))
    doc = (info.description or "").strip()
    comment_block = ""
    if doc:
        wrapped = textwrap.fill(doc, width=78, subsequent_indent="   * ")
        comment_block = f"  /** {wrapped} */\n"
    return f"{comment_block}  {name}: {ts_type};"


def _emit_stop_reason_labels() -> str:
    """Emit ``STOP_REASON_INFO`` (domain/phases.py) as a TS label const — the
    single label source, mirrored to the webapp without hand-maintained drift."""
    from promptpotter.domain.phases import STOP_REASON_INFO

    rows = "\n".join(
        f"  {reason.value!r}: {info.label!r}," for reason, info in STOP_REASON_INFO.items()
    )
    return (
        "// Operator-facing label per terminal reason (StopReason). Mirror of\n"
        "// domain/phases.py::STOP_REASON_INFO — the single label source.\n"
        "export const STOP_REASON_LABELS: Record<string, string> = {\n"
        f"{rows}\n"
        "};"
    )


def _emit_interface(model: type[BaseModel]) -> str:
    hints = _resolved_hints(model)
    body_lines = [
        _emit_field(name, info, hints.get(name, info.annotation))
        for name, info in model.model_fields.items()
    ]
    # Computed fields ARE on the wire (``model_dump`` emits them) but live outside
    # ``model_fields`` — omitting them hands the webapp a type that is missing keys the
    # server always sends.
    body_lines += [
        _emit_computed_field(model, name, info)
        for name, info in model.model_computed_fields.items()
    ]
    if model.model_config.get("extra") == "allow":
        body_lines.append("  [key: string]: unknown;")
    doc = (model.__doc__ or "").strip().splitlines()
    doc_line = doc[0] if doc else ""
    doc_block = f"/** {doc_line} */\n" if doc_line else ""
    return f"{doc_block}export interface {model.__name__} {{\n" + "\n".join(body_lines) + "\n}"


_HEADER = """\
// AUTOGENERATED by scripts/build_ts_types.py — do not hand-edit.
// Run `python scripts/build_ts_types.py` to regenerate from the Pydantic
// models in `promptpotter/` and commit the diff alongside any schema change.

"""


def main() -> int:
    blocks = [_emit_interface(model) for model in EXPORTED_MODELS]
    blocks.append(_emit_stop_reason_labels())
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
