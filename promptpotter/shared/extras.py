"""The optional installs (`pyproject.toml` extras) code asks for by name, read off the package's own metadata."""

from __future__ import annotations

import re
from enum import StrEnum
from functools import cache
from importlib import metadata

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


@cache
def _distributions() -> dict[str, tuple[str, ...]]:
    """Read at first ask, never at import: a process off an install (a bare `PYTHONPATH`) imports fine."""
    try:
        meta = metadata.metadata("promptpotter")
    except metadata.PackageNotFoundError:
        raise RuntimeError(
            "promptpotter is not installed, so its extras cannot be read: pip install -e ."
        ) from None
    by_extra: dict[str, list[str]] = {name: [] for name in meta.get_all("Provides-Extra") or ()}
    for requirement in metadata.requires("promptpotter") or ():
        spec, _, marker = requirement.partition(";")
        if (extra := _MARKER.search(marker)) and (name := _NAME.match(spec.strip())):
            by_extra[extra.group(1)].append(name.group(0))
    if undeclared := sorted(set(Extra) - set(by_extra)):
        raise RuntimeError(
            f"extras {undeclared} are not in the installed metadata — declare them in "
            "pyproject.toml and re-install the package"
        )
    return {extra: tuple(names) for extra, names in by_extra.items()}


def missing(extra: Extra) -> tuple[str, ...]:
    """The extra's distributions not installed; asked of the metadata, so nothing is imported."""

    def absent(name: str) -> bool:
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            return True
        return False

    return tuple(name for name in _distributions()[extra] if absent(name))


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


__all__ = ["Extra", "MissingExtraError", "install_hint", "installed", "missing", "require_extra"]
