from __future__ import annotations

import dataclasses
import enum
import json
import sys
import textwrap
import types
import typing
from collections.abc import Mapping

from fastapi.routing import APIRoute
from processes import REPO
from pydantic import BaseModel
from pydantic.fields import ComputedFieldInfo, FieldInfo

sys.path.insert(0, str(REPO))

from promptpotter.domain.command_kinds import PAYLOAD_MODEL_FOR_KIND, CommandKind
from promptpotter.main import API_ROUTERS, app
from promptpotter.presentation.api.routers.commands import commands_router


def _served_roots() -> list[typing.Any]:
    roots: list[typing.Any] = [app.router.responses["default"]["model"]]
    for router in API_ROUTERS:
        for route in router.routes:
            # FastAPI sets `response_model` on the instance outside `APIRoute`'s declared attributes.
            if isinstance(route, APIRoute) and (answer := vars(route)["response_model"]):
                roots.append(answer)
    return roots


_OUT_PATH = REPO / "webapp" / "lib" / "api" / "types.generated.ts"

# Each named type the roots reach, in the order first reached, held as its interface.
_EXPORTED: dict[type, str] = {}


def _export(owner: type) -> str:
    if owner not in _EXPORTED:
        _EXPORTED[owner] = ""  # held before its fields are walked: a type may reach itself
        _EXPORTED[owner] = _emit_interface(owner)
    return str(owner.__name__)


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
    if annotation is type(None):
        return "null"
    if isinstance(annotation, typing.TypeAliasType):
        return _emit_type(annotation.__value__)
    if isinstance(annotation, typing.NewType):
        return _emit_type(annotation.__supertype__)

    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return " | ".join(
            repr(m.value) if isinstance(m.value, str) else str(m.value) for m in annotation
        )

    if typing.is_typeddict(annotation):
        return _export(annotation)

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Required, typing.NotRequired):
        return _emit_type(args[0])

    if origin in (typing.Union, types.UnionType):
        has_none = type(None) in args
        non_none = [a for a in args if a is not type(None)]
        rendered = " | ".join(_emit_type(a) for a in non_none)
        return f"{rendered} | null" if has_none else rendered

    if origin is typing.Literal:
        values = [a.value if isinstance(a, enum.Enum) else a for a in args]
        # `json.dumps` for a non-string: a Python `True` is not a TypeScript literal.
        return " | ".join(repr(v) if isinstance(v, str) else json.dumps(v) for v in values)

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
        return _export(annotation) if annotation.model_fields else "Record<string, unknown>"

    if isinstance(annotation, type) and "__get_pydantic_json_schema__" in vars(annotation):
        hooked: typing.Any = annotation
        declared = hooked.__get_pydantic_json_schema__(None, lambda schema: schema)
        return _array_of(declared["items_schema"]["cls"])

    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return _export(annotation)

    return "unknown"


def _jsdoc(text: str | None) -> str:
    return f"  /** {textwrap.fill(text, width=78, subsequent_indent='   * ')} */\n" if text else ""


def _emit_field(name: str, info: FieldInfo, annotation: typing.Any, *, omittable: bool) -> str:
    # Pydantic serializes defaults on the wire ⇒ no `?` in TS, unless a serializer drops the field.
    ts_type = _emit_type(annotation)
    return f"{_jsdoc(info.description)}  {info.alias or name}{'?' if omittable else ''}: {ts_type};"


def _omitted_unset(model: type[BaseModel]) -> frozenset[str]:
    """The fields a model's own serializer leaves off the wire when nobody set them."""
    if any(info.is_required() for info in model.model_fields.values()):
        return frozenset()
    dumped = model.model_construct().model_dump(mode="json")
    return frozenset(name for name in model.model_fields if name not in dumped)


def _computed_return_type(model: type[BaseModel], name: str) -> typing.Any:
    """``ComputedFieldInfo.return_type`` is undefined under ``from __future__ import annotations``."""
    prop = getattr(model, name)
    fget = getattr(prop, "fget", None) or prop
    return typing.get_type_hints(fget).get("return", typing.Any)


def _emit_computed_field(model: type[BaseModel], name: str, info: ComputedFieldInfo) -> str:
    ts_type = _emit_type(_computed_return_type(model, name))
    return f"{_jsdoc((info.description or '').strip())}  {name}: {ts_type};"


def _emit_enum_union(enum_cls: type[enum.Enum], note: str) -> str:
    members = " | ".join(repr(m.value) for m in enum_cls)
    return f"// {note}\nexport type {enum_cls.__name__} = {members};"


def _emit_lifecycle_filter() -> str:
    from promptpotter.domain.campaign import LifecycleFilter

    members = " | ".join(repr(m) for m in typing.get_args(LifecycleFilter.__value__))
    note = "`GET /campaigns?lifecycle=` (domain/campaign.py::LifecycleFilter); absent = 'active'."
    return f"// {note}\nexport type LifecycleFilter = {members};"


def _emit_command_contract() -> str:
    """Exports what it names, so it runs before the exported blocks are collected."""
    answers = {
        route.path.removeprefix("/commands/"): vars(route)["response_model"]
        for route in commands_router.routes
        if isinstance(route, APIRoute)
    }
    kinds = sorted(CommandKind)
    sent, answered = [], []
    for kind in kinds:
        model = PAYLOAD_MODEL_FOR_KIND[kind]
        required = [name for name, info in model.model_fields.items() if info.is_required()]
        keys = " | ".join(json.dumps(name) for name in required) or "never"
        sent.append(f"  {json.dumps(kind.value)}: CommandSent<{_export(model)}, {keys}>;")
        answered.append(f"  {json.dumps(kind.value)}: {_emit_type(answers[kind.value])};")
    members = " | ".join(json.dumps(k.value) for k in kinds)
    return (
        "// Every kind `POST /commands/{kind}` dispatches (domain/command_kinds.py).\n"
        f"export type CommandKind = {members};\n\n"
        "type DeepPartial<T> = T extends object ? { [P in keyof T]?: DeepPartial<T[P]> } : T;\n"
        "// A body as sent: its required keys, every other one omittable at any depth.\n"
        "export type CommandSent<T, K extends keyof T> = { [P in K]: DeepPartial<T[P]> } & {\n"
        "  [P in Exclude<keyof T, K>]?: DeepPartial<T[P]>;\n"
        "};\n\n"
        "// Each kind's body IS its payload model "
        "(domain/command_kinds.py::PAYLOAD_MODEL_FOR_KIND).\n"
        "export interface CommandPayloads {\n" + "\n".join(sent) + "\n}\n\n"
        "// What each kind answers: its route's `response_model` "
        "(presentation/api/routers/commands.py).\n"
        "export interface CommandResponses {\n" + "\n".join(answered) + "\n}"
    )


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
    from promptpotter.domain.results import DISPLAY_METRIC_INFO, LevelUnit

    members = " | ".join(repr(m) for m in DISPLAY_METRIC_INFO)
    level_units = " | ".join(repr(u) for u in typing.get_args(LevelUnit))
    rows = "\n".join(
        f"  {{ id: {metric!r}, label: {info.label!r}, glyph: {info.glyph!r},"
        f" title: {info.title!r} }},"
        for metric, info in DISPLAY_METRIC_INFO.items()
    )
    labels = "\n".join(
        f"  {metric!r}: {info.label!r}," for metric, info in DISPLAY_METRIC_INFO.items()
    )
    units = "\n".join(
        f"  {metric!r}: {info.unit!r}," for metric, info in DISPLAY_METRIC_INFO.items()
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
        "};\n\n"
        "// A displayed level's unit (domain/results.py::LevelUnit).\n"
        f"export type LevelUnit = {level_units};\n\n"
        "export const DISPLAY_METRIC_UNITS: Record<DisplayMetric, LevelUnit> = {\n"
        f"{units}\n"
        "};"
    )


def _emit_export_tiers() -> str:
    from promptpotter.domain.export import EXPORT_TIER_INFO

    members = " | ".join(json.dumps(tier) for tier in EXPORT_TIER_INFO)
    rows = "\n".join(
        f"  {{ id: {json.dumps(tier)}, label: {json.dumps(info.label, ensure_ascii=False)},"
        f" pasted: {'true' if info.pasted else 'false'} }},"
        for tier, info in EXPORT_TIER_INFO.items()
    )
    return (
        "// What `GET …/export?tier=` serves, in offer order. Mirror of\n"
        "// domain/export.py::EXPORT_TIER_INFO — `pasted` tiers are text to copy, the rest download.\n"
        f"export type ExportTier = {members};\n\n"
        "export const EXPORT_TIERS: readonly { id: ExportTier; label: string; pasted: boolean }[] = [\n"
        f"{rows}\n"
        "];"
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


def _emit_lens_labels() -> str:
    from promptpotter.application.mask.record import SCORE_LENS_LABELS, Lens
    from promptpotter.application.optimizers.potter.pobb.checks import ABORT_LENS_LABELS

    def table(name: str, kind: typing.Literal["score", "abort"], labels: Mapping[str, str]) -> str:
        rows = "\n".join(
            f"  {Lens(kind, body).spelling!r}: {label!r}," for body, label in labels.items()
        )
        return f"export const {name}: Record<string, string> = {{\n{rows}\n}};"

    return (
        "// Each lens a picklist offers by name -> its label, keyed as the `lens=` it sends, in\n"
        "// picklist order. Mirrors of application/mask/record.py::SCORE_LENS_LABELS and\n"
        "// pobb/checks.py::ABORT_LENS_LABELS, whose keys are asserted against\n"
        "// `ABORT_LENS_SUPPRESS` at import. Don't hand-list these.\n"
        + table("SCORE_LENS_LABELS", "score", SCORE_LENS_LABELS)
        + "\n\n"
        + table("ABORT_LENS_LABELS", "abort", ABORT_LENS_LABELS)
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
    # A dataclass declared with no docstring is handed its signature as one.
    return f"/** {doc[0]} */\n" if doc and not doc[0].startswith(f"{owner.__name__}(") else ""


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
    # Not `model_fields[...].annotation`: it can hold a bare `ForwardRef`, which emits `unknown`.
    hints = typing.get_type_hints(model)
    omitted = _omitted_unset(model)
    body_lines = [
        _emit_field(name, info, hints.get(name, info.annotation), omittable=name in omitted)
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

    for root in _served_roots():
        _emit_type(root)
    command_contract = _emit_command_contract()
    # Two types of one name would MERGE into one TypeScript interface, silently.
    if clashes := len(_EXPORTED) - len({owner.__name__ for owner in _EXPORTED}):
        raise SystemExit(f"{clashes} exported type name(s) are shared by two types")
    blocks = list(_EXPORTED.values())
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
    blocks.append(command_contract)
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
    blocks.append(_emit_export_tiers())
    blocks.append(_emit_lens_labels())
    blocks.append(_emit_cell_term_meta())
    blocks.append(_emit_recent_step())
    blocks.append(_emit_cycle_path_grammar())
    content = _HEADER + "\n\n".join(blocks) + "\n"
    _OUT_PATH.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {_OUT_PATH.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
