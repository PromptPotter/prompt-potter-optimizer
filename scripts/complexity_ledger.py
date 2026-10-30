"""What the next change costs, counted — and the ceiling that holds each row where it stands.

    python scripts/complexity_ledger.py            # print the ledger
    python scripts/complexity_ledger.py --check    # the gate's `surface-ledger` check

Each ``BASELINE`` row is a CEILING: a row above it fails, and a raise owes a written reason in the
COMMIT BODY (root ``CLAUDE.md`` § ``<surface-ledger>``). A fall needs no edit.

A script and not a module of the package: it counts every layer, the suite and the generated
contract beside them, none of which an installed wheel carries.
"""

from __future__ import annotations

import ast
import collections
import importlib
import inspect
import json
import re
import sys
import typing
from collections.abc import Iterator
from pathlib import Path

from processes import REPO

BASELINE = {
    # The `__init__.py` files that re-export names instead of staying empty.
    "reexport_shims": 2,
    # Campaign-config knobs, plus the node knobs each optimizer member declares.
    "config_leaf_fields": 96,
    # `Settings` fields — what the environment can set.
    "settings_env": 42,
    # Parameters annotated `Any`.
    "any_params": 43,
    # `dict[str, Any]` maps declared in `domain/`.
    "domain_any_maps": 65,
    # Models that opt out of `StrictModel`'s `extra="forbid"`.
    "models_lax": 9,
    # Injections the optimizers' members price — each a block an optimizer prompt can carry.
    "injections": 31,
    # Escalation rules, priced the same way: the round, climb and heal tables together, since a
    # transition written as a branch beside the tables was policy the count could not see.
    "escalation_rules": 12,
    # Function-local imports of the package's own modules. The bare invocation's first-run check
    # is an application read, and `--help` loads no use case, so it is named as a string in `main()`.
    "deferred_imports": 7,
    # Files naming three or more `CycleRecord` arms, each a site the next arm must reach.
    "p_record": 17,
    # The arms each file naming most of them leaves out: a mirror rotting raises it.
    "p_record_drift": 13,
    # Served properties no surface reads by name; a payload field only the terminal sends is one.
    "p_served": 176,
    # Files naming three or more `CommandKind` rows, each a site the next row must reach.
    "p_command": 10,
    # The rows each file naming most of them leaves out.
    "p_command_drift": 0,
    # What every optimizer member is handed: `NodeContext`'s public members, inherited ones included,
    # and the public members of each object one of its properties exposes.
    "p_member": 12,
    # What a member may declare beyond it: each `CycleRead` kind counted as `p_member` is.
    "p_reads": 324,
    # `tests/test_*.py` — `tests/CLAUDE.md` names what each one owns.
    "test_files": 6,
    # Test functions across them: one journey per `# N.` section, plus the forks a section names.
    "test_functions": 25,
}

_PACKAGE_ROOT = REPO / "promptpotter"

# ``assets/`` is install DATA the package reads, never code it imports — the optimizer
# manifest, the exported dashboard, the benchmark dataset definitions.
_ASSETS_ROOT = _PACKAGE_ROOT / "assets"

# The suite's shape (`tests/CLAUDE.md`): prose alone let four files land in one arc against it.
_TESTS_ROOT = REPO / "tests"


def _count_tests() -> tuple[int, int]:
    files = sorted(_TESTS_ROOT.glob("test_*.py"))
    functions = sum(
        line.startswith(("def test_", "async def test_"))
        for f in files
        for line in f.read_text(encoding="utf-8").splitlines()
    )
    return len(files), functions


# What the browser is OFFERED — every property of every schema in the generated contract, so the
# roster is the app's own answer and not a hand-kept list.
_CONTRACT = REPO / "docs" / "specs" / "openapi.generated.json"


def _served_properties() -> list[str]:
    schemas = json.loads(_CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    return [prop for s in schemas.values() for prop in s.get("properties", {})]


# Where a change lands by hand: the package, the scripts and the webapp's production source. A test, a prose doc and a generated file are no such site.
_WEBAPP_SOURCE = ("app", "components", "lib")
_CLI_ROOT = _PACKAGE_ROOT / "presentation" / "cli"


def _sites() -> dict[Path, str]:
    webapp = REPO / "webapp"
    browser = [
        p
        for root in _WEBAPP_SOURCE
        for p in (webapp / root).rglob("*")
        if p.suffix in (".ts", ".tsx")
        and ".generated." not in p.name
        and not any("test" in part for part in p.relative_to(webapp).parts)
    ]
    files = [
        *_package_files("*.py"),
        *(REPO / "scripts").glob("*.py"),
        *browser,
    ]
    return {p: p.read_text(encoding="utf-8") for p in files}


_Spelling = tuple[re.Pattern[str], re.Pattern[str]]


def _spelled(literal: str, *identifiers: str) -> _Spelling:
    """A roster entry by NAME, then as a quoted string."""
    named = re.compile("|".join(rf"\b{re.escape(name)}\b" for name in identifiers))
    return named, re.compile(rf"[\"']{re.escape(literal)}[\"']")


# Three entries named make a file a site the roster's next entry must reach.
_ENUMERATES = 3


def _enumerations(sites: dict[Path, str], roster: list[_Spelling]) -> tuple[int, int]:
    """The files enumerating *roster*, and the entries each mirror leaves out. A file naming more
    than half the roster mirrors it; one naming fewer reads its subset, so leaves out nothing. A
    quoted value is often a common word, so it counts only inside a file that already enumerates by
    name."""
    enumerating = left_out = 0
    for text in sites.values():
        named = [bool(name.search(text)) for name, _ in roster]
        if sum(named) < _ENUMERATES:
            continue
        enumerating += 1
        if 2 * sum(named) <= len(roster):
            continue
        left_out += sum(
            not (hit or quoted.search(text)) for hit, (_, quoted) in zip(named, roster, strict=True)
        )
    return enumerating, left_out


def _members_bound(cls: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for stmt in cls.body:
        if isinstance(stmt, _Function | ast.ClassDef):
            members.add(stmt.name)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            members.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            members.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    members.update(
        node.attr
        for node in ast.walk(cls)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )
    return {member for member in members if not member.startswith("_")}


def _class_node(cls: type) -> ast.ClassDef:
    body = ast.parse(Path(inspect.getfile(cls)).read_text(encoding="utf-8")).body
    for name in cls.__qualname__.split("."):
        node = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == name)
        body = node.body
    return node


def _public_surface(cls: type) -> set[str]:
    """The public names *cls* answers to: its own and every package class's it inherits."""
    return {
        member
        for klass in cls.__mro__
        if klass.__module__.startswith("promptpotter")
        for member in _members_bound(_class_node(klass))
    }


def _imported_names(module: str) -> dict[str, object]:
    """What a module's names resolve to, the ones it imports for typing alone included."""
    tree = ast.parse(Path(inspect.getfile(importlib.import_module(module))).read_text("utf-8"))
    return {
        alias.asname or alias.name: getattr(importlib.import_module(node.module), alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level
        for alias in node.names
        if node.module.startswith("promptpotter")
    }


def _count_handed(cls: type) -> int:
    """*cls*'s public surface, and the public surface of each package class a public property or
    annotated attribute exposes — a member reaches through one as it reaches the context itself."""
    node = _class_node(cls)
    names = _imported_names(cls.__module__)
    exposed: list[tuple[str, ast.expr | None]] = []
    for stmt in node.body:
        if isinstance(stmt, _Function) and any(
            isinstance(d, ast.Name) and d.id == "property" for d in stmt.decorator_list
        ):
            exposed.append((stmt.name, stmt.returns))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            exposed.append((stmt.target.id, stmt.annotation))
    handed = set(_public_surface(cls))
    for attr, annotation in exposed:
        if attr.startswith("_") or annotation is None:
            continue
        for ref in ast.walk(annotation):
            sub = names.get(ref.id) if isinstance(ref, ast.Name) else None
            if isinstance(sub, type) and sub.__module__.startswith("promptpotter"):
                handed |= {f"{attr}.{member}" for member in _public_surface(sub)}
    return len(handed)


# A `type` / `interface` declaration's keys declare a shape and read nothing. Its body opens on
# `=`, `<`, `{` or `extends`: an import's `type X,` specifier is no declaration, and read as one
# it swallows the code after it up to the next `;` at depth zero.
_TYPE_DECLARATION = re.compile(
    r"^[ \t]*(?:export\s+)?(?:declare\s+)?(interface|type)\s+\w+(?=\s*(?:[=<{]|extends\b))",
    re.MULTILINE,
)
# A property read, never one assigned; then destructured: a binding's pattern, or a parameter's.
_READ = re.compile(r"\.(\w+)\b(?!\s*=(?![=>]))|\[[\"'](\w+)[\"']\](?!\s*=(?![=>]))")
_DESTRUCTURED = re.compile(
    r"\b(?:const|let|var)\s*\{([^{}]*)\}\s*(?::[^=;]*)?=(?![=>])"
    r"|\(\s*\{([^{}]*)\}\s*(?::[^(){};]*)?\)\s*(?:=>|\{|:)"
)


def _without_types(text: str) -> str:
    kept: list[str] = []
    end = 0
    for match in _TYPE_DECLARATION.finditer(text):
        if match.start() < end:
            continue
        kept.append(text[end : match.start()])
        depth, end = 0, match.end()
        while end < len(text):
            char = text[end]
            end += 1
            if char in "{([":
                depth += 1
            elif char in "})]":
                depth -= 1
                if not depth and match[1] == "interface":
                    break
            elif char == ";" and not depth:
                break
    kept.append(text[end:])
    return "".join(kept)


def _read_names(sites: dict[Path, str]) -> set[str]:
    """By NAME, not by schema: a name read on any type counts as read on every schema declaring it,
    and a whole-object dump (`JSON.stringify`, a copy button) reads nothing here."""
    read: set[str] = set()
    for path, text in sites.items():
        if path.suffix in (".ts", ".tsx"):
            code = _without_types(text)
            for groups in _DESTRUCTURED.findall(code):
                for entry in "".join(groups).split(","):
                    if key := re.match(r"\s*(\w+)", entry):
                        read.add(key[1])
        elif _CLI_ROOT in path.parents:
            code = text
        else:
            continue
        read.update(name for match in _READ.findall(code) for name in match if name)
    return read


def _package_files(pattern: str) -> list[Path]:
    stowaways = sorted(_ASSETS_ROOT.rglob("*.py")) if _ASSETS_ROOT.is_dir() else []
    assert not stowaways, (
        f"Python files under {_ASSETS_ROOT.name}/ are excluded from every ledger count, so "
        f"these are uncounted surface: {[str(p.relative_to(_PACKAGE_ROOT)) for p in stowaways]}. "
        "assets/ is install DATA — move code into the package proper, or out of the dataset "
        "that staged it."
    )
    return [p for p in _PACKAGE_ROOT.rglob(pattern) if _ASSETS_ROOT not in p.parents]


_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _nodes(tree: ast.AST) -> Iterator[tuple[ast.AST, int]]:
    """Every node of *tree* with the number of functions enclosing it, in ``ast.walk``'s order."""
    todo: collections.deque[tuple[ast.AST, int]] = collections.deque([(tree, 0)])
    while todo:
        node, depth = todo.popleft()
        yield node, depth
        inside = depth + isinstance(node, _Function)
        todo.extend((child, inside) for child in ast.iter_child_nodes(node))


def _is_bare_any(annotation: ast.expr) -> bool:
    """``Any`` or ``Any | None``. Not a mypy flag: ``disallow_any_explicit`` rejects an honest
    ``dict[str, Any]`` just as hard, and ``warn_return_any`` cannot see a param at all — an
    ``Any`` IS a complete annotation."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        right = annotation.right
        if not (isinstance(right, ast.Constant) and right.value is None):
            return False
        annotation = annotation.left
    return isinstance(annotation, ast.Name) and annotation.id == "Any"


_ANY_MAPS = ("dict[str, Any]", "Mapping[str, Any]")


def _declared_extra(model: ast.ClassDef) -> str | None:
    extra: str | None = None
    for stmt in model.body:
        if not isinstance(stmt, ast.Assign) or not any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
        ):
            continue
        value = stmt.value
        keys: dict[object, ast.expr] = {}
        if isinstance(value, ast.Call):
            keys = {kw.arg: kw.value for kw in value.keywords if kw.arg}
        elif isinstance(value, ast.Dict):
            keys = {
                k.value: v
                for k, v in zip(value.keys, value.values, strict=True)
                if isinstance(k, ast.Constant)
            }
        node_extra = keys.get("extra")
        if isinstance(node_extra, ast.Constant):
            extra = str(node_extra.value)
    return extra


def _count_lax_models(bases: dict[str, list[str]], declared: dict[str, str | None]) -> int:
    """Models that do NOT end up ``extra="forbid"`` — Pydantic's default returns a valid-looking
    instance from a misspelled keyword. AST, not import: eager package init makes import order a
    hazard."""

    def is_model(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name == "BaseModel":
            return True
        if name in seen or name not in bases:
            return False
        return any(is_model(b, seen | {name}) for b in bases[name])

    def effective_extra(name: str, seen: frozenset[str] = frozenset()) -> str | None:
        if name in seen or name not in bases:
            return None
        if declared.get(name):
            return declared[name]
        for base in bases[name]:
            found = effective_extra(base, seen | {name})
            if found:
                return found
        return None

    return sum(
        1
        for name in bases
        if name != "BaseModel" and is_model(name) and effective_extra(name) != "forbid"
    )


# A function-local import of our OWN package, `importlib.import_module` included unless its
# argument is a literal naming another package. Sanctioned for one reason, which the import must
# declare: it gates an optional extra, so hoisting it would make the core un-importable without
# that extra (docs/decisions.md § 0006). Mark those `# extras: <name>` on the import line or the one above; the
# rest are debt and count here. Why startup and cycles do not excuse one:
# `docs/developer/conventions.md` § Code shape.
_EXTRAS_MARKER = "# extras:"


def _imports_own_package(node: ast.AST) -> typing.TypeGuard[ast.stmt | ast.expr]:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").startswith("promptpotter")
    if isinstance(node, ast.Import):
        return any(a.name.startswith("promptpotter") for a in node.names)
    if not (isinstance(node, ast.Call) and node.args):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name != "import_module":
        return False
    target = node.args[0]
    return not (isinstance(target, ast.Constant) and isinstance(target.value, str)) or (
        target.value.startswith("promptpotter")
    )


def _source_counts(py_files: list[Path]) -> dict[str, int]:
    """The four counts read off source, in ONE parse and one walk per module.

    A deferred import counts once per function enclosing it, so a closure's import is charged to
    the closure and to the function that holds it. ``domain_any_maps`` is scoped to ``domain/``
    because package-wide the shape is mostly the backend's own runtime-keyed overlay, and a count
    that flags what nobody may fix gets muted."""
    domain_root = _PACKAGE_ROOT / "domain"
    any_params = any_maps = deferred = 0
    bases: dict[str, list[str]] = {}
    declared: dict[str, str | None] = {}
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        in_domain = domain_root in path.parents
        for node, depth in _nodes(ast.parse(source)):
            if isinstance(node, _Function):
                args = node.args
                annotations = [a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs]
                any_params += sum(ann is not None and _is_bare_any(ann) for ann in annotations)
                if in_domain:
                    any_maps += sum(
                        ann is not None and any(s in ast.unparse(ann) for s in _ANY_MAPS)
                        for ann in (*annotations, node.returns)
                    )
            elif isinstance(node, ast.ClassDef):
                if names := [b.id for b in node.bases if isinstance(b, ast.Name)]:
                    bases[node.name] = names
                    declared[node.name] = _declared_extra(node)
            if depth and _imports_own_package(node):
                context = lines[max(0, node.lineno - 2) : node.end_lineno or node.lineno]
                deferred += depth * (not any(_EXTRAS_MARKER in line for line in context))
    return {
        "any_params": any_params,
        "domain_any_maps": any_maps,
        "models_lax": _count_lax_models(bases, declared),
        "deferred_imports": deferred,
    }


def _is_reexport_shim(init_file: Path) -> bool:
    """A module-level ``from … import`` binding a name that is no module and that the body never
    reads is there to be imported from HERE. A submodule imported for its registration is not."""
    tree = ast.parse(init_file.read_text(encoding="utf-8"))
    read = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom) or stmt.module == "__future__":
            continue
        # `from . import x` is relative to the package this `__init__.py` is.
        base = init_file.parents[stmt.level - 1] if stmt.level else REPO
        home = base.joinpath(*(stmt.module or "").split("."))
        for alias in stmt.names:
            is_module = (home / f"{alias.name}.py").is_file() or (home / alias.name).is_dir()
            if not is_module and (alias.asname or alias.name) not in read:
                return True
    return False


def compute_ledger() -> dict[str, int]:
    from promptpotter.application import optimizers
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.knobs import KNOBS, member_knob_count
    from promptpotter.application.optimizers.nodes import CycleRead
    from promptpotter.config.settings import Settings
    from promptpotter.domain.command_kinds import PAYLOAD_MODEL_FOR_KIND, CommandKind
    from promptpotter.domain.run_records import CycleRecord

    py_files = _package_files("*.py")
    source = _source_counts(py_files)
    sites = _sites()
    arms = typing.get_args(typing.get_args(CycleRecord)[0])
    records, records_left_out = _enumerations(
        sites, [_spelled(a.model_fields["record_type"].default, a.__name__) for a in arms]
    )
    commands, commands_left_out = _enumerations(
        sites,
        [
            _spelled(k.value, f"CommandKind.{k.name}", PAYLOAD_MODEL_FOR_KIND[k].__name__)
            for k in CommandKind
        ],
    )
    priced: collections.Counter[str] = collections.Counter()
    for member in optimizers.NODE_MEMBERS.table.values():
        priced.update(member.priced_surface)
    read = _read_names(sites)
    test_files, test_functions = _count_tests()

    return {
        "reexport_shims": sum(_is_reexport_shim(p) for p in py_files if p.name == "__init__.py"),
        # The overlay leaf stands for the node knobs each member declares, counted instead.
        "config_leaf_fields": len(KNOBS) - 1 + member_knob_count(),
        "settings_env": len(Settings.model_fields),
        "any_params": source["any_params"],
        "domain_any_maps": source["domain_any_maps"],
        "models_lax": source["models_lax"],
        **priced,
        "deferred_imports": source["deferred_imports"],
        "p_record": records,
        "p_record_drift": records_left_out,
        "p_served": sum(prop not in read for prop in _served_properties()),
        "p_command": commands,
        "p_command_drift": commands_left_out,
        "p_member": _count_handed(NodeContext),
        "p_reads": sum(map(_count_handed, CycleRead.__subclasses__())),
        "test_files": test_files,
        "test_functions": test_functions,
    }


def _format(ledger: dict[str, int]) -> str:
    width = max(len(k) for k in ledger)
    rows = [f"  {k.ljust(width)}  {v:>4}  / {BASELINE.get(k, '-')}" for k, v in ledger.items()]
    return "complexity ledger (count / ceiling)\n" + "\n".join(rows)


def check(ledger: dict[str, int]) -> list[str]:
    """What stands above ``BASELINE`` — empty where every row is at or under its ceiling."""
    if set(ledger) != set(BASELINE):
        moved = sorted(set(ledger) ^ set(BASELINE))
        return [f"complexity-ledger dimensions changed ({moved}); update BASELINE in this commit"]
    risen = {k: (v, BASELINE[k]) for k, v in ledger.items() if v > BASELINE[k]}
    if not risen:
        return []
    return [
        "the cost of the next change grew (dimension: actual vs ceiling): "
        f"{risen}. Subtract instead, or raise the ceiling with a reason in the commit body."
    ]


if __name__ == "__main__":
    counted = compute_ledger()
    if "--check" not in sys.argv[1:]:
        print(_format(counted))
        raise SystemExit(0)
    risen = check(counted)
    if risen:
        print("\n".join(risen) + "\nBASELINE lives in scripts/complexity_ledger.py.")
    raise SystemExit(bool(risen))
