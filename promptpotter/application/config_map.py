"""The served config map: each knob's effective value and source, grouped by estimand, and the couplings."""

from __future__ import annotations

from typing import Any, get_args

from pydantic import Field

from promptpotter.application.knobs import (
    CouplingSeverity,
    KnobSource,
    check_couplings,
    declared_couplings,
    resolve_knob_states,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.pipeline_resolve import cycle_config, recorded_read
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    Estimand,
    knob_label,
)
from promptpotter.domain.pipeline_schema import KnobLayers
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores, owned_campaign


class ConfigKnob(StrictModel):
    path: str = Field(
        description="Dotted CampaignConfig path (or const.<NAME> for a hardcoded floor)"
    )
    label: str = Field(description="Short display name (prefix-stripped)")
    value: Any = Field(description="Effective value in this campaign's frozen config")
    source: KnobSource = Field(
        description="Where the value came from: default | required | manifest (an optimizer "
        "node's knob, as its manifest declares it) | the layer that set it: campaign (the "
        "operator's edit; any set bench knob), and on an optimizer node's knob dataset, "
        "free_tier or seed"
    )


class ConfigEstimandGroup(StrictModel):
    key: str = Field(description="Estimand key (selection, difficulty, ability, …)")
    label: str = Field(description="Human-readable estimand name")
    knobs: list[ConfigKnob] = Field(description="Knobs that move this estimand, in declared order")


_SEVERITIES: tuple[CouplingSeverity, ...] = get_args(CouplingSeverity)


class ConfigCoupling(StrictModel):
    name: str = Field(description="Coupling id")
    labels: list[str] = Field(description="Short display names for those knobs")
    relation: str = Field(description="The relationship rule, plain language")
    consequence: str = Field(description="What goes wrong when the combination is violated")
    severity: CouplingSeverity = Field(
        description="collision (soundness) | inert (wasted knob) | info (relationship)"
    )
    active: bool = Field(
        description="True when this campaign's config is in the violating combination"
    )


class ConfigMapResponse(StrictModel):
    """Built off the couplings the pre-run preflight warning reads, so no surface disagrees with
    the engine on which knobs collide."""

    groups: list[ConfigEstimandGroup] = Field(description="Estimand groups, in declared order")
    couplings: list[ConfigCoupling] = Field(
        description="Declared couplings in reading order: the ones this config violates first, "
        "then gravest severity first, declaration order within a severity"
    )


def campaign_config_map(stores: Stores, campaign_id: str) -> ConfigMapResponse:
    campaign = owned_campaign(stores, campaign_id)
    cycle = cycle_config(stores, campaign, campaign.root_hop)
    laid_by = cycle.knob_layers(recorded_read(stores, campaign.root_hop).knob_layers)
    return config_map(cycle.config, laid_by)


def config_map(config: CampaignConfig, laid_by: KnobLayers) -> ConfigMapResponse:
    selected = select_optimizer(config.optimization)
    states = resolve_knob_states(config, selected, laid_by)
    groups = [
        ConfigEstimandGroup(
            key=estimand.value,
            label=estimand.value.replace("_", " ").title(),
            knobs=[
                ConfigKnob(path=s.path, label=knob_label(s.path), value=s.value, source=s.source)
                for s in states
                if estimand in s.estimands
            ],
        )
        for estimand in Estimand
    ]
    active = {c.name for c in check_couplings(config, selected)}
    couplings = [
        ConfigCoupling(
            name=c.name,
            labels=[knob_label(k) for k in c.knobs],
            relation=c.relation,
            consequence=c.consequence,
            severity=c.severity,
            active=c.name in active,
        )
        for c in declared_couplings(selected)
    ]
    return ConfigMapResponse(
        groups=[g for g in groups if g.knobs],
        couplings=sorted(couplings, key=lambda c: (not c.active, _SEVERITIES.index(c.severity))),
    )
