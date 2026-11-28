"""The built-in optimizers' loader: each package's `members.MEMBERS`, registered from above into
`optimizer_contract.py::NODE_MEMBERS` where every registry completes."""

from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Iterator
from pathlib import Path

from promptpotter.application.optimizer_contract import NodeMember

BUILTIN_PACKAGES = frozenset(pkg.name for pkg in pkgutil.iter_modules(__path__) if pkg.ispkg)


def builtin_members() -> Iterator[tuple[str, object]]:
    for package in sorted(BUILTIN_PACKAGES):
        module = importlib.import_module(f"{__name__}.{package}.members")
        for obj in module.MEMBERS:
            yield module.__name__, obj


def member_package_dir(member: NodeMember) -> Path:
    """The built-in optimizer package declaring *member*, or the top package of a plugin's."""
    package = type(member).__module__.removeprefix(f"{__name__}.").split(".")[0]
    builtin = package in BUILTIN_PACKAGES
    top = f"{__name__}.{package}" if builtin else type(member).__module__.split(".")[0]
    return Path(str(sys.modules[top].__file__)).parent
