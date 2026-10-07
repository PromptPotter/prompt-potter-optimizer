"""One block-library entry: its material and its AUTHORED provenance, unknown left ``None``. A measured lift is
never a field here — it is derived from the archive, per model and dataset."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from promptpotter.domain.strict_model import StrictModel

__all__ = ["PromptBlock"]


class PromptBlock(StrictModel):
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    source: str
    level: Literal["target", "optimizer"] | None = None
    role: str | None = None
    technique_family: str | None = None
    source_paper: str | None = None
    source_url: str | None = None
    source_locator: str | None = None
    year: int | None = None
    evolved_or_authored: Literal["evolved", "authored", "selected"] | None = None
    evolved_on_model: str | None = None
    evaluated_on_models: tuple[str, ...] | None = None
    task_family: str | None = None
    benchmark: str | None = None
    baseline: str | None = None
    reported_score: float | None = None
    reported_lift: float | None = None
    verbatim: bool | None = None
    notes: str | None = None
