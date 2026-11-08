from __future__ import annotations

import dataclasses
import enum
import inspect
import json
import sys
import textwrap
import types
import typing
from collections.abc import Sequence
from pathlib import Path

from fastapi.routing import APIRoute
from processes import replace_changed
from pydantic import BaseModel, SecretStr
from pydantic.fields import ComputedFieldInfo, FieldInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from promptpotter.application.initialization.wiring import complete_registries
from promptpotter.config.paths import PACKAGE_ROOT
from promptpotter.domain.campaign import LifecycleFilter
from promptpotter.domain.command_kinds import PAYLOAD_MODEL_FOR_KIND, CommandKind
from promptpotter.domain.cycle_paths import (
    ALL_DOTS_PATTERN,
    HOP_SEP,
    ID_COMPONENT_PATTERN,
    UNIT_SEP,
)
from promptpotter.domain.phases import DashboardState, ProducerState, RunPhase
from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTION_PREFIX
from promptpotter.domain.said import TONES
from promptpotter.domain.sample import SAMPLE_TAG_DIGITS
from promptpotter.domain.spend import RATE_PRICED_LABEL
from promptpotter.infrastructure.llm.heartbeat import HEARTBEAT_INTERVAL_S
from promptpotter.infrastructure.runtime_flags import RECENT_STEP_S
from promptpotter.main import API_ROUTERS, app
from promptpotter.presentation.api.routers.commands import commands_router
from promptpotter.shared.worded import Worded, registered


def _served_roots() -> list[typing.Any]:
    roots: list[typing.Any] = [app.router.responses["default"]["model"]]
    for mount in API_ROUTERS:
        for route in mount.router.routes:
            # FastAPI sets `response_model` on the instance outside `APIRoute`'s declared attributes.
            if isinstance(route, APIRoute) and (answer := vars(route)["response_model"]):
                roots.append(answer)
    return roots


REPO = PACKAGE_ROOT.parent
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
    if annotation is str or annotation is SecretStr:
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

    if origin in (typing.Required, typing.NotRequired, typing.Annotated):
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

    if origin in (list, set, frozenset, Sequence):
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

    # Before the model branch: a hooked model emits the schema it declares, unless that is its own.
    if isinstance(annotation, type) and "__get_pydantic_json_schema__" in vars(annotation):
        hooked: typing.Any = annotation
        own = vars(hooked).get("__pydantic_core_schema__")
        declared = hooked.__get_pydantic_json_schema__(own, lambda schema: schema)
        if declared["type"] == "list":
            return _array_of(declared["items_schema"]["cls"])
        if declared["type"] == "dict":
            return "Record<string, unknown>"

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _export(annotation) if annotation.model_fields else "Record<string, unknown>"

    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return _export(annotation)

    return "unknown"


def _jsdoc(text: str | None, indent: str = "  ") -> str:
    if not text:
        return ""
    return f"{indent}/** {textwrap.fill(text, width=78, subsequent_indent=f'{indent} * ')} */\n"


def _emit_field(name: str, info: FieldInfo, annotation: typing.Any, *, omittable: bool) -> str:
    # Pydantic serializes defaults on the wire ⇒ no `?` in TS, unless a serializer drops the field.
    ts_type = _emit_type(annotation)
    return f"{_jsdoc(info.description)}  {info.alias or name}{'?' if omittable else ''}: {ts_type};"


def _wire_fields(model: type[BaseModel]) -> list[tuple[str, FieldInfo]]:
    """An excluded field never crosses as JSON (an upload's bytes ride the form)."""
    return [(name, info) for name, info in model.model_fields.items() if not info.exclude]


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
    members = " | ".join(repr(m) for m in typing.get_args(LifecycleFilter.__value__))
    note = "`GET /hierarchy?lifecycle=` (domain/campaign.py::LifecycleFilter); absent = 'active'."
    return f"// {note}\nexport type LifecycleFilter = {members};"


def _emit_command_contract() -> str:
    """Exports what it names, so it runs before the exported blocks are collected."""
    routes = {
        route.path.removeprefix("/commands/"): route
        for route in commands_router.routes
        if isinstance(route, APIRoute)
    }
    sent, answered = [], []
    for kind in sorted(CommandKind):
        model = PAYLOAD_MODEL_FOR_KIND[kind]
        route = vars(routes[kind.value])
        answered.append(f"  {json.dumps(kind.value)}: {_emit_type(route['response_model'])};")
        # A kind posted as a form has no JSON body to type.
        if all(p.field_info.annotation is not model for p in route["dependant"].body_params):
            continue
        required = [name for name, info in _wire_fields(model) if info.is_required()]
        keys = " | ".join(json.dumps(name) for name in required) or "never"
        sent.append(f"  {json.dumps(kind.value)}: CommandSent<{_export(model)}, {keys}>;")
    browser = " | ".join(json.dumps(kind.value) for kind in sorted(CommandKind) if kind.browser)
    return (
        "// Each kind whose `Press` claims the browser door "
        "(domain/command_kinds.py::CommandKind.browser).\n"
        f"export type BrowserKind = {browser};\n\n"
        "type DeepPartial<T> = T extends object ? { [P in keyof T]?: DeepPartial<T[P]> } : T;\n"
        "// A body as sent: its required keys, every other one omittable at any depth.\n"
        "export type CommandSent<T, K extends keyof T> = { [P in K]: DeepPartial<T[P]> } & {\n"
        "  [P in Exclude<keyof T, K>]?: DeepPartial<T[P]>;\n"
        "};\n\n"
        "// Each JSON-posted kind's body IS its payload model "
        "(domain/command_kinds.py::PAYLOAD_MODEL_FOR_KIND).\n"
        "export interface CommandPayloads {\n" + "\n".join(sent) + "\n}\n\n"
        "// What each kind answers: its route's `response_model` "
        "(presentation/api/routers/commands.py).\n"
        "export interface CommandResponses {\n" + "\n".join(answered) + "\n}"
    )


class _Declared(typing.NamedTuple):
    module: str
    name: str
    table: Worded[typing.Any, typing.Any]
    key_type: typing.Any
    row_type: typing.Any


def _declared_type(module: str, name: str, annotation: object) -> typing.Any:
    """The one annotation, evaluated alone: a module's TYPE_CHECKING-only names stay unread."""
    holder = type(name, (), {"__annotations__": {name: annotation}, "__module__": module})
    return typing.get_type_hints(holder)[name]


def _word_tables() -> list[_Declared]:
    """Every registered `Worded`, under the annotated module binding that declares it.

    The app is imported and its registries completed as its boot completes them, so the registry
    holds what it serves; a re-export binds without an annotation and is no owner. A table no
    declaration owns, or two own, is refused.
    """
    complete_registries()
    owners: dict[int, list[tuple[str, str, object]]] = {}
    for module, loaded in list(sys.modules.items()):
        if not module.startswith("promptpotter."):
            continue
        for name, annotation in vars(loaded).get("__annotations__", {}).items():
            if isinstance(value := vars(loaded).get(name), Worded):
                owners.setdefault(id(value), []).append((module, name, annotation))
    declared: list[_Declared] = []
    for table in registered():
        found = owners.get(id(table), [])
        if len(found) != 1:
            where = [f"{module}::{name}" for module, name, _ in found] or "no annotated binding"
            raise SystemExit(f"word table keyed {list(table)[:3]} is declared by {where}")
        ((module, name, annotation),) = found
        key_type, row_type = typing.get_args(_declared_type(module, name, annotation))
        declared.append(_Declared(module, name, table, key_type, row_type))
    if len({d.name for d in declared}) != len(declared):
        raise SystemExit("two word tables share an exported name")
    return sorted(declared, key=lambda d: (d.module, d.name))


def _wire(value: object) -> object:
    return value.value if isinstance(value, enum.Enum) else value


def _emit_word_tables(emitted: set[str]) -> str:
    """Each closed set's words, whole and under its Python name, so no surface holds a table of its
    own. A row's columns are its NamedTuple's; a named key or column type is exported once."""
    unions: dict[str, str] = {}

    def named(kind: typing.Any) -> str | None:
        if isinstance(kind, type) and issubclass(kind, enum.Enum):
            body = _emit_enum_union(kind, f"{kind.__module__}::{kind.__name__}")
        elif isinstance(kind, typing.TypeAliasType):
            body = f"// {kind.__module__}::{kind.__name__}\nexport type {kind.__name__} = {_emit_type(kind)};"
        else:
            return None
        if kind.__name__ not in emitted:
            unions.setdefault(kind.__name__, body)
        return str(kind.__name__)

    def is_row(kind: typing.Any) -> bool:
        return isinstance(kind, type) and issubclass(kind, tuple) and hasattr(kind, "_fields")

    def row_of(kind: type) -> str:
        """A NamedTuple, a table's row or a model one of its columns holds, as its interface."""
        if kind.__name__ not in rows_seen:
            rows_seen[kind.__name__] = ""  # held before its columns are walked
            rows_seen[kind.__name__] = (
                f"export interface {kind.__name__} {{\n"
                + "".join(
                    f"  {col}: {column(hint)};\n"
                    for col, hint in typing.get_type_hints(kind).items()
                )
                + "}"
            )
        return kind.__name__

    def column(hint: typing.Any) -> str:
        if is_row(hint):
            return row_of(hint)
        args = typing.get_args(hint)
        if typing.get_origin(hint) is tuple and args[1:] == (Ellipsis,) and is_row(args[0]):
            return f"{row_of(args[0])}[]"
        members = [hint] if named(hint) else [a for a in args if named(a)]
        if not members:
            return _emit_type(hint)
        nullable = ["null"] if type(None) in args else []
        return " | ".join([str(named(m)) for m in members] + nullable)

    def literal(value: object) -> str:
        if isinstance(value, BaseModel):
            dumped = value.model_dump(mode="json").items()
            return (
                "{ "
                + ", ".join(f"{c}: {json.dumps(v, ensure_ascii=False)}" for c, v in dumped)
                + " }"
            )
        if isinstance(value, tuple) and hasattr(value, "_asdict"):
            return "{ " + ", ".join(f"{c}: {literal(v)}" for c, v in value._asdict().items()) + " }"
        if isinstance(value, tuple):
            return "[" + ", ".join(literal(v) for v in value) + "]"
        return json.dumps(_wire(value), ensure_ascii=False)

    tables: list[str] = []
    orders: list[str] = []
    rows_seen: dict[str, str] = {}
    for module, name, table, key_type, row_type in _word_tables():
        orders.append(f"  [{name}, {json.dumps([str(_wire(k)) for k in table])}],")
        keys = named(key_type) or _emit_type(key_type)
        row = column(row_type)
        body = "\n".join(f"  {json.dumps(str(_wire(k)))}: {literal(v)}," for k, v in table.items())
        owner = module.removeprefix("promptpotter.").replace(".", "/")
        # A table over a subset of its key set is partial: a reader handles the member it lacks.
        domain = (
            len(key_type)
            if isinstance(key_type, type) and issubclass(key_type, enum.Enum)
            else len(typing.get_args(getattr(key_type, "__value__", key_type))) or len(table)
        )
        record = f"Record<{keys}, {row}>"
        tables.append(
            f"// Mirror of {owner}.py::{name}, in display order.\nexport const {name}: "
            f"{record if len(table) == domain else f'Partial<{record}>'} = {{\n{body}\n}};"
        )
    # A row interface named like an exported model would MERGE with it, silently.
    if clash := emitted & set(rows_seen):
        raise SystemExit(f"word-table rows share a name with an exported type: {sorted(clash)}")
    # An object's own key order puts integer-like keys first, so the display order rides beside it.
    order = (
        "// Each table's keys in display order (lib/api/words.ts walks a table by it).\n"
        "export const WORD_ORDER: ReadonlyMap<object, readonly string[]> = new Map<object, "
        "readonly string[]>([\n" + "\n".join(orders) + "\n]);"
    )
    return "\n\n".join([*unions.values(), *rows_seen.values(), *tables, order])


def _emit_served_words() -> str:
    return (
        "// domain/pipeline_schema.py::SCHEMA_DESCRIPTION_PREFIX.\n"
        f"export const SCHEMA_DESCRIPTION_PREFIX = {json.dumps(SCHEMA_DESCRIPTION_PREFIX)};\n"
        "// The word beside a figure our rate table priced (domain/spend.py::RATE_PRICED_LABEL).\n"
        f"export const RATE_PRICED_LABEL = {json.dumps(RATE_PRICED_LABEL)};\n"
        "// How wide a row's slot is printed (domain/sample.py::sample_tag).\n"
        f"export const SAMPLE_TAG_DIGITS = {SAMPLE_TAG_DIGITS};"
    )


def _emit_tones() -> str:
    return (
        "// Every tone, quietest first (domain/said.py::Tone); only `warn` and `danger` pin.\n"
        f"export const TONES = {json.dumps(list(TONES))} as const;\n"
        "export type Tone = (typeof TONES)[number];"
    )


def _emit_live_windows() -> str:
    return (
        "// How often an open call beats (infrastructure/llm/heartbeat.py::HEARTBEAT_INTERVAL_S).\n"
        f"export const HEARTBEAT_INTERVAL_S = {HEARTBEAT_INTERVAL_S:g};\n"
        "// The shortest silence between two ray steps worth marking. Mirror of\n"
        "// infrastructure/runtime_flags.py::RECENT_STEP_S, the window a producer reads `live` in.\n"
        f"export const RECENT_STEP_S = {RECENT_STEP_S:g};"
    )


def _emit_cycle_path_grammar() -> str:
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
    paragraph = " ".join(inspect.cleandoc(owner.__doc__ or "").split("\n\n")[0].split())
    # A dataclass declared with no docstring is handed its signature as one.
    return "" if paragraph.startswith(f"{owner.__name__}(") else _jsdoc(paragraph, indent="")


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
        for name, info in _wire_fields(model)
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
    for root in _served_roots():
        _emit_type(root)
    command_contract = _emit_command_contract()
    emitted = {owner.__name__ for owner in _EXPORTED} | {
        "RunPhase",
        "ProducerState",
        "DashboardState",
        "Tone",
    }
    # Before the blocks are collected: a model only a table's column reaches is exported too.
    word_tables = _emit_word_tables(emitted)
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
    blocks.append(_emit_tones())
    blocks.append(command_contract)
    blocks.append(word_tables)
    blocks.append(_emit_served_words())
    blocks.append(_emit_live_windows())
    blocks.append(_emit_cycle_path_grammar())
    content = _HEADER + "\n\n".join(blocks) + "\n"
    verb = "wrote" if replace_changed(_OUT_PATH, content) else "unchanged"
    print(f"{verb} {_OUT_PATH.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
