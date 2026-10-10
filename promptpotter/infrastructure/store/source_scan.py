from __future__ import annotations

import contextlib
import hashlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.config.paths import user_data_root
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json
from promptpotter.shared import hashing
from promptpotter.shared.hashing import (
    NormalizedSource,
    SourceFacts,
    SourceReads,
    UnitFacts,
    shapes_optimizer_prompt,
    source_facts,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable
    from types import ModuleType

__all__ = ["optimizer_prompt_shapers"]

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]

PLUMBING_MODULES = frozenset(
    {
        "promptpotter.infrastructure.llm.telemetry",
        "promptpotter.application.bench.llm_call",
        "promptpotter.application.bench.resume_and_fork.decisions",
        "promptpotter.application.optimizers.nodes",
        "promptpotter.application.runner.measurement",
        "promptpotter.shared.hashing",
        __name__,
        "promptpotter.infrastructure.store.io",
        "promptpotter.config.paths",
    }
)


class _Scope(NamedTuple):
    names: frozenset[str]
    classes: frozenset[str]
    imports: dict[str, tuple[str, str | None]]


def _module_name(path: Path) -> str:
    parts = path.relative_to(_PACKAGE_ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _module_path(module: str) -> Path | None:
    base = _PACKAGE_ROOT.parent.joinpath(*module.split("."))
    return next((p for p in (base.with_suffix(".py"), base / "__init__.py") if p.is_file()), None)


class _FactCache:
    _FILE = Path(".cache") / "source_facts.json"

    def __init__(self) -> None:
        self._held: dict[str, SourceFacts] | None = None
        self._salt = b""
        self._used: set[str] = set()
        self._grew = False

    def _key(self, text: str) -> str:
        if not self._salt:
            self._salt = Path(str(hashing.__file__)).read_bytes() + sys.version.encode()
        return hashlib.sha256(self._salt + text.encode("utf-8")).hexdigest()

    def of(self, text: str) -> SourceFacts:
        if self._held is None:
            held = read_json_tolerant(user_data_root() / self._FILE, {})
            self._held = held if isinstance(held, dict) else {}
        key = self._key(text)
        self._used.add(key)
        if key not in self._held:
            self._held[key] = source_facts(text)
            self._grew = True
        return self._held[key]

    def keep(self, live: Iterable[str]) -> None:
        if not self._grew or self._held is None:
            return
        wanted = self._used | {self._key(text) for text in live}
        self._held = {key: facts for key, facts in sorted(self._held.items()) if key in wanted}
        with contextlib.suppress(OSError):
            write_json(user_data_root() / self._FILE, self._held)
        self._grew = False


_FACTS = _FactCache()


class _Package:
    def __init__(self, hashed: Iterable[tuple[str, SourceFacts]]) -> None:
        self._scopes: dict[str, _Scope | None] = {m: self._scope_of(f) for m, f in hashed}

    @staticmethod
    def _bind(rows: Iterable[list[str]]) -> dict[str, tuple[str, str | None]]:
        table: dict[str, tuple[str, str | None]] = {}
        for bound, module, name in rows:
            full = f"{module}.{name}"
            table[bound] = (full, None) if _module_path(full) else (module, name)
        return table

    def _scope_of(self, facts: SourceFacts) -> _Scope:
        return _Scope(
            frozenset(facts["names"]), frozenset(facts["classes"]), self._bind(facts["imports"])
        )

    def scope(self, module: str) -> _Scope | None:
        if module not in self._scopes:
            path = _module_path(module)
            self._scopes[module] = (
                self._scope_of(_FACTS.of(path.read_text(encoding="utf-8"))) if path else None
            )
        return self._scopes[module]

    def is_class(self, module: str, name: str) -> bool:
        scope = self.scope(module)
        return scope is not None and name in scope.classes

    def resolve(self, module: str, name: str) -> tuple[str, str] | None:
        seen: set[tuple[str, str]] = set()
        while (module, name) not in seen and (scope := self.scope(module)) is not None:
            seen.add((module, name))
            if name in scope.names:
                return module, name
            source = scope.imports.get(name)
            if source is None or source[1] is None:
                return None
            module, name = source[0], source[1]
        return None

    def reached(self, module: str, reads: SourceReads) -> set[tuple[str, str]]:
        scope = self.scope(module)
        assert scope is not None
        imports = {**scope.imports, **self._bind(reads["imports"])}
        out: set[tuple[str, str] | None] = set()
        for bound, attr in reads["attrs"]:
            holder, member = imports[bound]
            if member is None:
                out.add(self.resolve(holder, attr))
        for name in reads["names"]:
            source = imports.get(name)
            if source is None:
                out.add(self.resolve(module, name))
            elif source[1] is not None:
                out.add(self.resolve(source[0], source[1]))
        return {hit for hit in out if hit is not None}


def optimizer_prompt_shapers(
    hashed: Iterable[ModuleType],
    *,
    covered: Iterable[ModuleType] = (),
    foreign: Collection[str] = (),
) -> tuple[NormalizedSource, ...]:
    """Read off the source, so the set cannot depend on which modules this process imported."""
    texts = {path: path.read_text(encoding="utf-8") for path in sorted(_PACKAGE_ROOT.rglob("*.py"))}
    units: list[tuple[str, UnitFacts]] = [
        (module, unit)
        for path, text in texts.items()
        if not (module := _module_name(path)).startswith(tuple(f"{f}." for f in foreign))
        and shapes_optimizer_prompt.__name__ in text
        for unit in _FACTS.of(text)["units"]
    ]
    whole = {m for m, unit in units if unit["whole"]}
    scanned = [m for m in hashed if m.__name__ not in whole]
    covered_names = whole | {m.__name__ for m in (*scanned, *covered)} | PLUMBING_MODULES
    marked = {(m, unit["name"]) for m, unit in units if unit["name"]}
    modules = [(m.__name__, _FACTS.of(Path(str(m.__file__)).read_text("utf-8"))) for m in scanned]
    package = _Package(modules)
    checked = [*((m, unit["reads"]) for m, unit in units), *((m, f["reads"]) for m, f in modules)]
    breaches = sorted(
        {
            f"{target[0]}.{target[1]} (read by {module})"
            for module, reads in checked
            for target in package.reached(module, reads)
            if target[0] not in covered_names
            and target not in marked
            and not package.is_class(*target)
        }
    )
    _FACTS.keep(texts.values())
    if breaches:
        raise RuntimeError(
            "hashed code reads package names nothing hashes — mark each one whose value reaches "
            f"prompt text `shapes_optimizer_prompt`: {breaches}"
        )
    return tuple(NormalizedSource(unit["source"]) for _, unit in units)
