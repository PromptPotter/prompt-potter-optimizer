"""The key a data root lands under (`landing.py::LandedStamp`): derived from the code a load runs,
once per code. A process derives it once, and a data root keeps it filed by the package's bytes
(`config/paths.py::LANDING_KEYS_DIR`), so an entry on unchanged code hashes no schema."""

from __future__ import annotations

import hashlib
import inspect
import sys
from collections.abc import Mapping
from functools import cache
from pathlib import Path

import pydantic
from pydantic import TypeAdapter

from promptpotter.application.maintenance.migrate import MIGRATIONS
from promptpotter.config.paths import LANDING_KEYS_DIR, user_data_root
from promptpotter.domain.kinds import KindTable
from promptpotter.infrastructure.store.data_root import KINDS, FoldedLines, Parsed, row_schema
from promptpotter.infrastructure.store.io import read_text_optional, write_text
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.import_walk import import_closure

_PACKAGE = Path(__file__).resolve().parents[2]


def _ran(schema: object, modules: set[str]) -> None:
    """The module of every function and class a core schema calls: its validators and models."""
    if isinstance(schema, Mapping):
        for value in schema.values():
            _ran(value, modules)
    elif isinstance(schema, list | tuple):
        for value in schema:
            _ran(value, modules)
    elif callable(schema):
        modules.add(inspect.unwrap(getattr(schema, "func", schema)).__module__)


def loading_modules() -> dict[str, str]:
    """Every package module whose code a load runs, less its prose: the import closure of each row's
    reader and the validators and models its shape builds, and of every migration. A load dispatches
    by name through a `KindTable` alone (a ledger line's record kind, a state kind a record nests),
    so every table's every kind is walked: the importlib loads that registered them, never. The key's
    own module is no code a load runs."""
    modules: set[str] = set()
    for kind in KINDS:
        if isinstance(kind.shape, TypeAdapter):
            _ran(kind.shape.core_schema, modules)
        elif isinstance(kind.shape, Parsed | FoldedLines):
            # A parsed or a folded row's reader is its shape's first field.
            _ran(kind.shape[0], modules)
            if not isinstance(kind.shape.model, KindTable):
                _ran(TypeAdapter(kind.shape.model).core_schema, modules)
    for table in KindTable.every:
        _ran([TypeAdapter(one).core_schema for one in table.registered().values()], modules)
    _ran(list(MIGRATIONS.values()), modules)
    closure = import_closure(modules)
    closure.pop(__name__, None)
    # Every ledger line and banked state part parses through `KindTable.parse`, a module no schema
    # names: an edit there changes what loads, so the key must hold its code.
    if KindTable.__module__ not in closure:
        raise RuntimeError(f"the landing key omits {KindTable.__module__}, which every load runs")
    return closure


def derived_landing_key() -> str:
    """Every row's patterns, fate and shape, and the code of every module a load runs, less its
    prose: a helper a validator calls changes what loads as surely as a field does."""
    rows = {kind.name: row_schema(kind) for kind in KINDS}
    return stable_hash({"rows": rows, "code": loading_modules()}, length=16)


@cache
def _code_digest() -> str:
    """What the derived key is a function of: the package's bytes and its schema builder's version."""
    held = hashlib.sha256(f"{sys.version}|{pydantic.VERSION}".encode())
    for path in sorted(_PACKAGE.rglob("*.py")):
        held.update(path.relative_to(_PACKAGE).as_posix().encode())
        held.update(path.read_bytes())
    return held.hexdigest()[:32]


def _filed(digest: str) -> Path:
    return user_data_root() / LANDING_KEYS_DIR / digest


@cache
def _derived(digest: str) -> str:
    return read_text_optional(_filed(digest)).strip() or derived_landing_key()


def landing_key() -> str:
    """This code's key, filed in the data root that asked once derived."""
    key = _derived(digest := _code_digest())
    if not _filed(digest).is_file():
        write_text(_filed(digest), key)
    return key
