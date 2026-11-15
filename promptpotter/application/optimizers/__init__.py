from __future__ import annotations

import typing
from collections.abc import Iterator
from pathlib import Path

from promptpotter.config.paths import optimizers_root
from promptpotter.shared.plugin_registry import Registry

MANIFEST_ENTRY_POINT_GROUP = "promptpotter.optimizer_manifests"
"""Published, keyed by manifest name: renaming it un-registers every plugin at once."""


class ManifestDir(typing.NamedTuple):
    # What `optimization.optimizer` selects.
    name: str
    directory: Path


def _validate_manifest(obj: object, origin: str) -> ManifestDir:
    if not isinstance(obj, ManifestDir) or not obj.name or not obj.directory.is_dir():
        raise RuntimeError(
            f"[{origin}] resolved to {obj!r}: an optimizer manifest is a `ManifestDir` naming "
            "what `optimization.optimizer` selects and the directory holding its pipeline.yaml."
        )
    return obj


def _manifests() -> Iterator[tuple[str, object]]:
    for directory in sorted(optimizers_root().iterdir()):
        if (directory / "pipeline.yaml").is_file():
            yield str(directory), ManifestDir(directory.name, directory)


MANIFESTS = Registry(MANIFEST_ENTRY_POINT_GROUP, _manifests, _validate_manifest)
