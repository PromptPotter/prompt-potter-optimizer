"""Generate TS interfaces from the Pydantic models in :data:`EXPORTED_MODELS`
→ ``webapp/lib/api/types.generated.ts``. CI re-runs this; non-empty diff fails."""

from __future__ import annotations

import sys
import textwrap
import types
import typing
from pathlib import Path

from pydantic import BaseModel
from pydantic.fields import FieldInfo

# ruff: noqa: E402 -- we import from promptpotter after adjusting sys.path
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from promptpotter.domain.pipeline_schema import NodeConfigParam, NodeOutputSchema
from promptpotter.domain.results import (
    DiagnosticRunRecord,
    RoundSummary,
    RoundSummaryCandidate,
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
    SessionSummary,
)
from promptpotter.presentation.api.routers.datasets import (
    DatasetItem,
    DatasetPipelineResponse,
    DatasetPreviewResponse,
    MeasurementDot,
    MeasurementSeriesResponse,
    SampleSeries,
)
from promptpotter.presentation.api.routers.measurements import (
    LeverageResponse,
    PerQueryRow,
)
from promptpotter.presentation.api.routers.verify import DiagnosticRunListResponse

EXPORTED_MODELS: list[type[BaseModel]] = [
    # Nested types first so the TS file reads top-down.
    RoundSummaryCandidate,
    RoundSummary,
    DiagnosticRunRecord,
    # --- live dashboard state ---
    SpendBucket,
    SpendRollup,
    BackendWarning,
    LoopWarning,
    BackfillLogEntry,
    InFlightCall,
    DashboardError,
    RunLimits,
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
    SessionSummary,
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
    # --- measurements router ---
    PerQueryRow,
    LeverageResponse,
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


def _emit_field(name: str, info: FieldInfo) -> str:
    # Pydantic serializes defaults on the wire ⇒ no `?` in TS, only `| null` for Optional.
    annotation = info.annotation
    ts_type = _emit_type(annotation)
    description = info.description
    comment_block = ""
    if description:
        wrapped = textwrap.fill(description, width=78, subsequent_indent="   * ")
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
    body_lines = [_emit_field(name, info) for name, info in model.model_fields.items()]
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
