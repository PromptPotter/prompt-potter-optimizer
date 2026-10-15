"""The optional installs (`pyproject.toml` extras) code asks for by name, read off the package's own metadata."""

from __future__ import annotations

import re
import sys
from enum import StrEnum
from functools import cache
from importlib import metadata
from typing import NamedTuple

from promptpotter.shared.errors import PotterError


class Extra(StrEnum):
    ANTHROPIC = "anthropic"
    BENCHMARKS = "benchmarks"
    DSPY = "dspy"
    EXCEL = "excel"
    HARBOR = "harbor"
    OBSERVABILITY = "observability"


_MARKER = re.compile(r'extra\s*==\s*"([^"]+)"')
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class _Requirements(NamedTuple):
    core: tuple[str, ...]
    by_extra: dict[str, tuple[str, ...]]


@cache
def _requirements() -> _Requirements:
    meta = metadata.metadata("promptpotter")
    core: list[str] = []
    by_extra: dict[str, list[str]] = {name: [] for name in meta.get_all("Provides-Extra") or ()}
    for requirement in metadata.requires("promptpotter") or ():
        spec, _, marker = requirement.partition(";")
        if name := _NAME.match(spec.strip()):
            extra = _MARKER.search(marker)
            (by_extra[extra.group(1)] if extra else core).append(name.group(0))
    return _Requirements(tuple(core), {extra: tuple(names) for extra, names in by_extra.items()})


def _absent(names: tuple[str, ...]) -> tuple[str, ...]:
    def absent(name: str) -> bool:
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            return True
        return False

    return tuple(name for name in names if absent(name))


def missing(extra: Extra) -> tuple[str, ...]:
    """The extra's distributions not installed; asked of the metadata, so nothing is imported."""
    if extra not in (declared := _requirements().by_extra):
        raise RuntimeError(
            f"extra {extra!r} is not in the installed metadata — declare it in "
            "pyproject.toml and re-install the package"
        )
    return _absent(declared[extra])


def require_core() -> None:
    try:
        absent = _absent(_requirements().core)
    except metadata.PackageNotFoundError:
        absent = ("promptpotter",)
    if absent:
        raise ImportError(
            f"{sys.executable} lacks {', '.join(absent)}, so it is not the interpreter "
            "promptpotter was installed into. Run the repo venv "
            "(.venv/Scripts/python.exe, .venv/bin/python) — installing them here would hide the "
            "broken install, not fix it."
        )


def installed(extra: Extra) -> bool:
    return not missing(extra)


def install_hint(extra: Extra) -> str:
    return f'pip install -e ".[{extra}]"'


class MissingExtraError(PotterError):
    """An install defect, served 422: the webapp retries every 5xx as transient
    (`webapp/lib/api/client.ts::failureKind`), and no retry installs a package."""

    http_status = 422
    code = "extra_missing"

    def __init__(self, extra: Extra, absent: tuple[str, ...]) -> None:
        super().__init__(
            f"This install lacks the `{extra}` extra ({', '.join(absent)}): {install_hint(extra)}",
            details={"extra": extra.value},
        )


def require_extra(extra: Extra) -> None:
    if absent := missing(extra):
        raise MissingExtraError(extra, absent)


__all__ = [
    "Extra",
    "MissingExtraError",
    "install_hint",
    "installed",
    "missing",
    "require_core",
    "require_extra",
]
