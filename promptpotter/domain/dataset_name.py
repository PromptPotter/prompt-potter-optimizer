from __future__ import annotations

import re

_DATASET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*$")


def validate_dataset_name(name: str) -> str:
    """The name IS the dataset's path under its tier, ``/``-separated; lowercase-only, since both
    shipped filesystems fold case."""
    if not name or not _DATASET_NAME_RE.match(name):
        raise ValueError(
            f"Invalid dataset name: {name!r}. `/`-separated segments of lowercase alphanumerics, "
            "hyphens and underscores, each starting with a letter or digit."
        )
    return name
