from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

from promptpotter.domain.cycle_paths import ID_COMPONENT_RE

_DATASET_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*")


def validate_dataset_name(name: str) -> str:
    """The name IS the dataset's path under its tier, ``/``-separated; lowercase-only, since both
    shipped filesystems fold case."""
    if not _DATASET_NAME_RE.fullmatch(name) or not all(
        ID_COMPONENT_RE.fullmatch(segment) for segment in name.split("/")
    ):
        raise ValueError(
            f"Invalid dataset name: {name!r}. `/`-separated segments of lowercase alphanumerics, "
            "hyphens and underscores, each starting with a letter or digit and none a Windows "
            "device name (con, nul, com1, ...)."
        )
    return name


# Parsed where the payload parses: a malformed name is the caller's 422, never a store's 500.
DatasetName = Annotated[str, AfterValidator(validate_dataset_name)]
