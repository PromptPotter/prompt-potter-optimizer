from __future__ import annotations

from typing import Any

from promptpotter.domain.dataset_source import Source
from promptpotter.shared.extras import Extra, require_extra


def from_hub(src: Source, **kwargs: Any) -> Any:
    """*src*'s hub dataset at its pinned ``rev``, in its ``config`` where it names one."""
    require_extra(Extra.BENCHMARKS)
    from datasets import load_dataset

    return load_dataset(src.repo, *src.configs, revision=src.rev, **kwargs)
