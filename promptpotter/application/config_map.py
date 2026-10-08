"""The config map: every knob under the estimand it moves, every coupling flagged against one config.
A READING of ``knobs.py``, kept out of it: that source is hashed into the optimizer prompt's identity."""

from __future__ import annotations

from typing import Any, get_args

from pydantic import Field

from promptpotter.application.campaign_config import (
    CampaignConfig,
    Estimand,
    estimand_doc,
    knob_label,
)
from promptpotter.application.knobs import (
    CouplingSeverity,
    check_couplings,
    declared_couplings,
    resolve_knob_states,
)
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "ConfigCoupling",
    "ConfigEstimandGroup",
    "ConfigKnob",
    "ConfigMapResponse",
    "config_map",
]


class ConfigKnob(StrictModel):
    path: str = Field(
        description="Dotted CampaignConfig path (or const.<NAME> for a hardcoded floor)"
    )
    label: str = Field(description="Short display name (prefix-stripped)")
    value: Any = Field(description="Effective value in this campaign's frozen config")
    source: str = Field(
        description="Where the value came from: default | campaign (operator-set) | required | "
        "manifest (an optimizer node's knob, as its manifest declares it)"
    )


class ConfigEstimandGroup(StrictModel):
    key: str = Field(description="Estimand key (selection, difficulty, ability, …)")
    label: str = Field(description="Human-readable estimand name")
    doc: str = Field(description="Plain-language one-liner of what this estimand is")
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


def config_map(config: CampaignConfig) -> ConfigMapResponse:
    """What moves which estimand, what overwrites what, and which knobs collide under *config*."""
    states = resolve_knob_states(config)
    groups = [
        ConfigEstimandGroup(
            key=estimand.value,
            label=estimand.value.replace("_", " ").title(),
            doc=estimand_doc(estimand),
            knobs=[
                ConfigKnob(path=s.path, label=knob_label(s.path), value=s.value, source=s.source)
                for s in states
                if estimand in s.estimands
            ],
        )
        for estimand in Estimand
    ]
    active = {c.name for c in check_couplings(config)}
    couplings = [
        ConfigCoupling(
            name=c.name,
            labels=[knob_label(k) for k in c.knobs],
            relation=c.relation,
            consequence=c.consequence,
            severity=c.severity,
            active=c.name in active,
        )
        for c in declared_couplings(config)
    ]
    return ConfigMapResponse(
        groups=[g for g in groups if g.knobs],
        # Stable, so declaration order survives within one severity.
        couplings=sorted(couplings, key=lambda c: (not c.active, _SEVERITIES.index(c.severity))),
    )
