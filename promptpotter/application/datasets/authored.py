from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import ValidationError

from promptpotter.application.datasets.tabular_ingest import read_candidate_library_file
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.scoring.formula.compiler import compile_scorer, split_scoring_block
from promptpotter.application.scoring.judges.registry import judge_instrument
from promptpotter.config.paths import install_datasets_root
from promptpotter.domain.campaign import Environment, Purpose, RunDeclaration
from promptpotter.domain.campaign_config import CAMPAIGN_RULE, CampaignConfig
from promptpotter.domain.draft_edit import CampaignLayer
from promptpotter.domain.layer_fold import Folded, Layer, fold_layers, wire_patch
from promptpotter.domain.pipeline_schema import KnobLayer
from promptpotter.domain.scoring import Scorer
from promptpotter.infrastructure.store.dataset_access import (
    DEFAULTS_FILE,
    DEFINITION_FILE,
    DatasetDefinition,
    ResolvedDataset,
    campaign_defaults,
    read_dataset_definition,
    read_dataset_framing,
)
from promptpotter.infrastructure.store.io import parse_yaml
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.errors import (
    FileKind,
    FileUnreadableError,
    PayloadInvalidError,
    validation_reason,
)


@dataclass(frozen=True, slots=True)
class AuthoredDataset:
    campaign_config: CampaignConfig
    """The definition's validated ``campaign`` section, every layer laid on."""

    task_description: str
    """``task.md``'s body, stripped; ``""`` when the definition has none."""

    candidate_library: tuple[str, ...]
    """``candidate_library.txt`` — the per-pipeline origin's target list, part of
    the origin spec (a ``candidate_source`` node ranks each query against it).
    ``()`` when absent. The draft path carries this so reopening a dataset surfaces
    its already-dropped library as FULFILLED (the origin HOLDS the value); the run
    reads the same file directly for the term-index union."""


def _campaign_block(definition: DatasetDefinition | None) -> dict[str, Any]:
    layers = () if definition is None else definition.campaign_layers
    return fold_layers(CAMPAIGN_RULE, [Layer("dataset", layer) for layer in layers]).document


def dataset_campaign_block(dataset: ResolvedDataset) -> dict[str, Any]:
    """The ``campaign`` section with every layer laid on; the minted ``campaign.json`` is another schema."""
    return _campaign_block(read_dataset_definition(dataset))


def campaign_file_block(path: Path) -> dict[str, Any]:
    """A definition FILE's own ``campaign`` section, laid over nothing."""
    parsed = read_file(path, parse_yaml, FileKind.STORED_CONFIG)
    block = (parsed or {}).get("campaign") or {}
    if not isinstance(block, dict):
        raise FileUnreadableError(
            FileKind.STORED_CONFIG, path=str(path), reason="`campaign` must be a mapping"
        )
    return block


def validated_campaign_config(block: Mapping[str, Any], *, path: Path) -> CampaignConfig:
    try:
        return CampaignConfig.model_validate(block)
    except ValidationError as exc:
        raise FileUnreadableError(
            FileKind.STORED_CONFIG, path=str(path), reason=validation_reason(exc, shown=3)
        ) from exc


def load_dataset_campaign_config(dataset: ResolvedDataset) -> CampaignConfig:
    return validated_campaign_config(
        dataset_campaign_block(dataset), path=dataset.directory / DEFINITION_FILE
    )


def defaults_config() -> CampaignConfig:
    """`defaults.yaml`'s campaign, whole: the value of every knob no layer above it sets."""
    return validated_campaign_config(
        campaign_defaults(), path=install_datasets_root() / DEFAULTS_FILE
    )


# What the target runs on and is graded by: the declaration's environment, every other key its loop.
_ENVIRONMENT_KEYS = frozenset(
    {"pipeline_overlay", "exclude_nodes", "task_framing", "scoring", "judges"}
)


def declare_run(
    config: CampaignConfig,
    *,
    purpose: Purpose,
    dataset_hash: str,
    connector: str,
    bench_defaults_pin: str,
) -> RunDeclaration:
    """Every door's config becomes ONE declaration: what it states over today's defaults."""
    defaults = defaults_config()
    under = defaults.model_dump(mode="json")
    named = {k: v for k, v in config.model_dump(mode="json").items() if under[k] != v}
    named.pop("optimization", None)
    held = under["optimization"]
    optimization = {
        k: v
        for k, v in config.optimization.model_dump(mode="json").items()
        if k != "budget" and held[k] != v
    }
    budget, standing = config.optimization.budget, defaults.optimization.budget
    return RunDeclaration(
        purpose=purpose,
        dataset_hash=dataset_hash,
        run_seed=named.pop("run_seed", None),
        loop={
            **{k: v for k, v in named.items() if k not in _ENVIRONMENT_KEYS},
            "optimization": optimization,
        },
        budget=budget.only(
            a
            for a in budget.named
            if a not in standing.named or getattr(budget, a) != getattr(standing, a)
        ),
        environment=Environment(
            connector=connector,
            target={k: v for k, v in named.items() if k in _ENVIRONMENT_KEYS},
        ),
        bench_defaults_pin=bench_defaults_pin,
    )


def effective_config(declaration: RunDeclaration) -> CampaignConfig:
    """The ONE derivation of what a declaration runs: today's defaults under its parts."""
    defaults = defaults_config()
    loop = declaration.loop
    seed = {} if declaration.run_seed is None else {"run_seed": declaration.run_seed}
    budget = defaults.optimization.budget.over(declaration.budget)
    return CampaignConfig.model_validate(
        {
            **defaults.model_dump(mode="json"),
            **loop,
            **declaration.environment.target,
            **seed,
            "optimization": {
                **defaults.optimization.model_dump(mode="json"),
                **loop["optimization"],
                "budget": budget.model_dump(mode="json", exclude_unset=True),
            },
        }
    )


class ConfigLayers(NamedTuple):
    """A run's config as a door hands it to the mint: the dataset's ``campaign`` block, and the
    door's edit, the keys it SETS (`pipeline_resolve.py::laid_config` lays them)."""

    dataset: Mapping[str, Any]
    campaign: Mapping[str, Any]

    @classmethod
    def of(cls, dataset: ResolvedDataset, stated: CampaignLayer | None) -> ConfigLayers:
        """The dataset's block, or a declaration *stated* in its place."""
        return cls(dataset_campaign_block(dataset), {}) if stated is None else cls.stating(stated)

    @classmethod
    def stating(cls, stated: CampaignLayer) -> ConfigLayers:
        """*stated* is the edit, laid over `defaults.yaml` alone, as a definition is."""
        return cls(campaign_defaults(), stated.model_dump(mode="json", exclude_unset=True))

    def over(self, optimization: Mapping[str, Any]) -> ConfigLayers:
        """*optimization*, as a door sent it (`layer_fold.py::wire_patch`), laid into the edit,
        last; its ``budget`` is the payload's own, where a null axis is unbounded, never a clear."""
        sent = {k: v for k, v in optimization.items() if k != "budget"}
        budget = {"budget": optimization["budget"]} if "budget" in optimization else {}
        laid = wire_patch(CAMPAIGN_RULE, sent, "optimization")
        layers = [
            Layer("campaign", self.campaign),
            Layer("campaign", {"optimization": {**laid, **budget}}),
        ]
        return self._replace(campaign=fold_layers(CAMPAIGN_RULE, layers).document)

    def folded(self) -> Folded[KnobLayer]:
        layers: list[Layer[KnobLayer]] = [
            Layer("dataset", self.dataset),
            Layer("campaign", self.campaign),
        ]
        return fold_layers(CAMPAIGN_RULE, layers)

    def block(self) -> dict[str, Any]:
        """Both layers' keys, the edit last: what an upload's Start records as its definition's
        ``campaign``, so a later campaign on it starts where this one did."""
        return self.folded().document

    def config(self) -> CampaignConfig:
        """Both layers laid; the optimizer it names must select."""
        return selected_config(self.folded().document)


def selected_config(document: Mapping[str, Any]) -> CampaignConfig:
    try:
        out = CampaignConfig.model_validate(document)
    except ValidationError as exc:
        raise PayloadInvalidError(
            validation_reason(exc, shown=1), code="campaign_config_invalid"
        ) from exc
    select_optimizer(out.optimization)
    return out


def scorer_of(config: CampaignConfig, *, verifier_graded: bool) -> Scorer:
    """``verifier_graded`` is the bank's fact; a reader holding no bank passes ``False``."""
    spec = split_scoring_block(config.scoring, judge_instrument=None)
    return compile_scorer(
        spec.per_sample,
        spec.per_cell,
        verifier_graded=verifier_graded,
        judge_instrument=judge_instrument(config.judges),
    )


def dataset_scorer(dataset: ResolvedDataset) -> Scorer:
    return scorer_of(load_dataset_campaign_config(dataset), verifier_graded=False)


def read_authored_dataset(dataset: ResolvedDataset) -> AuthoredDataset:
    definition = read_dataset_definition(dataset)
    return AuthoredDataset(
        campaign_config=validated_campaign_config(
            _campaign_block(definition), path=dataset.directory / DEFINITION_FILE
        ),
        task_description=read_dataset_framing(dataset).description,
        candidate_library=read_candidate_library_file(dataset.directory),
    )
