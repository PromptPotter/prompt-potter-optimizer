from __future__ import annotations

from collections.abc import Collection
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from promptpotter.domain.campaign_config import CampaignConfig, Knob, OptimizationConfig
from promptpotter.domain.pipeline_schema import ParamIntent
from promptpotter.domain.strict_model import StrictModel, UploadBytes


def _partial(
    model: type[BaseModel], *, only: Collection[str] | None = None, **nested: type[BaseModel]
) -> Any:
    """*model*'s fields, or *only* the ones named, each optional with no default and its own
    bounds and description: a layer names only what it sets, so no unsent field states a value a
    reader could take for one that applies. A *nested* field takes the partial named for it."""
    fields: dict[str, Any] = {}
    for name, info in model.model_fields.items():
        if only is not None and name not in only:
            continue
        bounds = [m for m in info.metadata if not isinstance(m, Knob)]
        annotation: Any = nested.get(name, info.annotation)
        fields[name] = (
            Annotated[annotation | None, *bounds, FieldInfo(description=info.description)],
            None,
        )
    return create_model(f"_{model.__name__}Fields", __base__=StrictModel, **fields)


# Neither `optimization` takes `budget`: a launch's budget is its payload's own field, the one door
# on every launch kind.
class OptimizationOverrides(
    _partial(OptimizationConfig, only=("optimizer", "nodes", "arm_gate", "halt_at_accuracy"))  # type: ignore[misc]
):
    """An ``optimization`` an operator sends: a check-in edit's, a mint's, a head-to-head arm's,
    laid over the one it names. Every other knob is the dataset's declaration."""


class PartialOptimizationConfig(
    _partial(OptimizationConfig, only=OptimizationConfig.model_fields.keys() - {"budget"})  # type: ignore[misc]
):
    """A stand-in declaration's ``optimization``: every knob a dataset declares."""


class ConfigEdit(_partial(CampaignConfig, optimization=OptimizationOverrides)):  # type: ignore[misc]
    """A check-in edit's ``config``, by `CampaignConfig`'s own paths. Only the fields sent move
    anything: its ``exclude_unset`` dump is the layer `campaign_config.py::merge_config_layers`
    lays."""


class PartialCampaignConfig(_partial(CampaignConfig, optimization=PartialOptimizationConfig)):  # type: ignore[misc]
    """A mint's stand-in declaration, by `CampaignConfig`'s own paths, laid over its defaults."""


class NodeOutputEdit(StrictModel):
    """One node's authored output contract: the schema whole, and the fields carrying the answer
    where the operator named them."""

    node: str = Field(min_length=1)
    output_schema: dict[str, Any]
    answer_fields: list[str] | None = None


class LibraryFile(StrictModel):
    """An uploaded candidate library, under every upload's one bound."""

    # A JSON door sends the bytes base64, as `ingest-upload`'s.
    model_config = ConfigDict(val_json_bytes="base64", ser_json_bytes="base64")

    filename: str = Field(default="", max_length=256)
    blob: UploadBytes = Field(repr=False, description="The file's bytes, base64.")


class EditDraftPatch(StrictModel):
    """Sparse mutation payload — only declared fields ride through. The one origin-edit vocabulary
    every ingress shares; the rules that apply one are ``draft_patch.py``'s, and stay out of this
    module so a command payload naming the type loads no resolver."""

    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    connector: str | None = Field(default=None, min_length=1, max_length=64)
    # A blank task is no task: the draft holds none until one is set.
    raw_task_description: str | None = Field(default=None, max_length=16384, pattern=r"\S")
    config: ConfigEdit | None = Field(
        default=None,
        description="Laid over the draft's config by `campaign_config.py::merge_config_layers`. "
        "A string `scoring` names the criterion whole; a `scoring` "
        "mapping is folded into the criterion the draft runs, key by key: `matcher` picks "
        "the per-sample matcher, `dials` the composite's weights (the empty string clears them), "
        "`per_sample` and `per_cell` set those formulas.",
    )
    reset: list[str] | None = Field(
        default=None,
        description="Dotted paths, by this patch's own fields, of the draft's own values to drop "
        "before the rest is laid, so the layer below shows through: "
        "`config.optimization.nodes.<node>.config.<key>` or `origin_prompts.<step>.<field>`. "
        "A path the draft does not set is a no-op.",
    )
    node_narrowing: dict[str, list[ParamIntent]] | None = None
    node_output: NodeOutputEdit | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    # Per step, its fields by name; replaces the draft's whole map.
    origin_prompts: dict[str, dict[str, str]] | None = None
    # The library's three sources; one patch sends at most one (`draft_patch.py`).
    candidate_library: list[str] | None = Field(default=None, min_length=1)
    candidate_library_column: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Build the candidate library from the distinct values of this draft column.",
    )
    candidate_library_file: LibraryFile | None = Field(
        default=None,
        description="Parse the candidate library from a file: one entry per line, or the first "
        "column of a CSV/Excel.",
    )
