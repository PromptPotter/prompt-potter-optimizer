from __future__ import annotations

import json
from collections.abc import Collection
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from promptpotter.domain.campaign_config import CampaignConfig, Knob, OptimizationConfig
from promptpotter.domain.pipeline_schema import ParamIntent
from promptpotter.domain.strict_model import StrictModel, UploadBytes


def _partial(
    model: type[BaseModel], *, only: Collection[str] | None = None, **nested: type[BaseModel]
) -> Any:
    """*model*'s fields, or *only* the ones named, each optional with no default: a layer names only what it sets."""
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


# An edit's `optimization` takes no `budget`: a launch's bound is its payload's own field, the one
# door on every launch kind; a declaration's (`CampaignLayer`) is its standing budget.
class OptimizationOverrides(
    _partial(OptimizationConfig, only=("optimizer", "nodes", "arm_gate", "halt_at_fitness"))  # type: ignore[misc]
):
    """An ``optimization`` an operator sends: a check-in edit's, a mint's, a head-to-head arm's,
    laid over the one it names. Every other knob is the dataset's declaration."""


class ConfigEdit(_partial(CampaignConfig, optimization=OptimizationOverrides)):  # type: ignore[misc]
    """A check-in edit's ``config``, by `CampaignConfig`'s own paths. Only the fields sent move
    anything: its ``exclude_unset`` dump is a layer of `campaign_config.py::CAMPAIGN_RULE`'s fold."""


class OptimizationLayer(_partial(OptimizationConfig)):  # type: ignore[misc]
    """One layer of a declaration's ``optimization``: every knob, its budget the standing one."""


class CampaignLayer(_partial(CampaignConfig, optimization=OptimizationLayer)):  # type: ignore[misc]
    """One layer of a ``campaign`` declaration, by `CampaignConfig`'s own paths: a definition
    file's, or a mint's stand-in for the dataset's own. Its budget is the declaration's standing
    one at every door (`authored.py::ConfigLayers.of`); a knob it does not name (a retired one)
    refuses where the layer is read."""


# The shipped datasets' largest schema is 293 characters of JSON.
def _bounded_schema[T](schema: T) -> T:
    if len(json.dumps(schema)) > 16384:
        raise ValueError("an output schema is at most 16384 characters of JSON")
    return schema


class NodeOutputEdit(StrictModel):
    """One node's authored output contract: the schema whole, and the fields carrying the answer
    where the operator named them."""

    node: str = Field(min_length=1)
    output_schema: Annotated[dict[str, Any], AfterValidator(_bounded_schema)]
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
        description="Laid over the draft's config by `CampaignConfig`'s rule per key: an absent "
        "key leaves its value, `null` drops the draft's own so the layer below shows through, and "
        '`""` is the empty string. A string `scoring` names the criterion whole; a `scoring` '
        "mapping is folded into the criterion the draft runs, key by key: `matcher` picks "
        "the per-sample matcher, `dials` the composite's weights (the empty string clears them), "
        "`per_sample` and `per_cell` set those formulas.",
    )
    node_narrowing: dict[str, list[ParamIntent]] | None = None
    node_output: NodeOutputEdit | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    # Per step, its fields by name; replaces the draft's whole map. A field is at most a task's
    # length (the shipped longest is 320).
    origin_prompts: dict[str, dict[str, Annotated[str, Field(max_length=16384)]]] | None = None
    # The library's three sources; one patch sends at most one (`draft_patch.py`).
    candidate_library: list[Annotated[str, Field(max_length=1024)]] | None = Field(
        default=None, min_length=1
    )
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
