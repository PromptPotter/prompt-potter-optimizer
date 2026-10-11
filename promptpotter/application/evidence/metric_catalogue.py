from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import computed_field

from promptpotter.application.scoring.formula.compiler import (
    CELL_CHANNELS,
    CompiledExpression,
    cell_channels_of,
    compile_expression,
)
from promptpotter.domain.scoring import GradedCell
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.statistics import mean_ci

MetricUnit = Literal["level", "delta", "seconds", "usd", "tokens", "rank", "rounds", "composed"]

CUSTOM_METRIC_PREFIX = "expr:"
CUSTOM_METRIC_KEY = "custom"

MEASURAND = "measurand"


class MetricSpec(StrictModel):
    """One pickable metric. Served rather than restated in the browser, so a label and a unit have
    one owner and a picker cannot drift from what the server actually computed."""

    key: str
    label: str
    expression: str
    unit: MetricUnit
    # `None` on a composed expression: `lift / latency` is up, `latency / lift` down, and nothing can tell.
    higher_is_better: bool | None
    description: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def axis_label(self) -> str:
        """What to CALL this metric's axis: the label, plus the unit only where the unit names
        something the reader does not already have. SERVED, so neither surface hand-writes the
        "unnamed" set below to derive it."""
        return self.label if self.unit in _UNNAMED_UNITS else f"{self.label} ({self.unit})"


_UNNAMED_UNITS: frozenset[str] = frozenset({"level", "delta", "composed"})


# The builder the per-sample formula namespace is cut from, so a channel here IS a `scoring:` term.
CHANNELS: tuple[str, ...] = CELL_CHANNELS


def cell_channels(rows: Iterable[GradedCell]) -> dict[str, dict[str, float]]:
    """Keyed by the row's `query`: a per-campaign `sample_id` names a different cell across campaigns."""
    sums: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        answered = cell_channels_of(row.facts, row.grade.fitness)
        # No key for a row that answered nothing: an empty map reads as unscorable, not unmeasured.
        if not answered:
            continue
        per_cell = sums.setdefault(row.facts.query, {})
        for channel, value in answered.items():
            per_cell.setdefault(channel, []).append(value)
    return {
        cell: {channel: sum(vs) / len(vs) for channel, vs in channels.items()}
        for cell, channels in sums.items()
    }


def available_channels(
    channels_by_campaign: Mapping[str, dict[str, dict[str, float]]],
) -> frozenset[str]:
    """Intersected, not unioned: a channel only one campaign carries compares it against nothing."""
    per_campaign = [
        frozenset(channel for cell in cells.values() for channel in cell)
        for cells in channels_by_campaign.values()
    ]
    return frozenset.intersection(*per_campaign) if per_campaign else frozenset()


def merge_cells(values: dict[str, float]) -> tuple[float | None, float | None, float | None, int]:
    """``(value, ci_lo, ci_hi, n_cells)``; no bracket below two cells."""
    ordered = [values[c] for c in sorted(values)]
    bracketed = mean_ci(ordered)
    if bracketed is not None:
        return (*bracketed, len(ordered))
    return (ordered[0] if ordered else None, None, None, len(ordered))


_ENTRIES: tuple[MetricSpec, ...] = (
    MetricSpec(
        key="final_lift",
        label="Final lift",
        expression="final_lift",
        unit="delta",
        higher_is_better=True,
        description=(
            "Where the seed's inner campaign ENDED, against its own origin — not the mean over "
            "its round budget that the loop scores, so a run which peaked early and gave it back "
            "reads differently here."
        ),
    ),
    MetricSpec(
        key="peak_lift",
        label="Peak lift",
        expression="peak_lift",
        unit="delta",
        higher_is_better=True,
        description="The best level the seed's inner campaign ever reached, against its own origin.",
    ),
    MetricSpec(
        key="origin",
        label="Origin level",
        expression="origin",
        unit="level",
        higher_is_better=True,
        description=(
            "The floor each seed started from. Carried because a lift alone cannot say whether a "
            "cell began hard or easy."
        ),
    ),
    MetricSpec(
        key="rounds",
        label="Rounds run",
        expression="rounds",
        unit="rounds",
        higher_is_better=False,
        description="How many L1 rounds each seed's inner campaign got through before it stopped.",
    ),
    MetricSpec(
        key="round_budget",
        label="Rounds available",
        expression="round_budget",
        unit="rounds",
        higher_is_better=None,
        description=(
            "How many L1 rounds each seed's inner campaign was GIVEN. Read beside Rounds run: "
            "two of two is a cell that finished, two of twelve is one that stopped early."
        ),
    ),
    MetricSpec(
        key="unworked",
        label="Time not working",
        expression="unworked",
        unit="seconds",
        higher_is_better=False,
        description=(
            "Seconds the cell was blocked rather than working — the machine suspended, or queued "
            "behind the rate limiter another cell was using — and handed back to its deadline. "
            "Read it beside a slow cell: it separates one that was slow from a box that was "
            "oversubscribed while it ran. Blank where the backend declares no envelope, which is "
            "no reading rather than a clean one."
        ),
    ),
    MetricSpec(
        key="latency",
        label="Time to reply",
        expression="latency",
        unit="seconds",
        higher_is_better=False,
        description=(
            "Seconds across the pipeline's steps, as measured when the cell was scored. A replayed "
            "cell reports what it cost to run, not what the replay cost."
        ),
    ),
    MetricSpec(
        key="cost",
        label="Cost",
        expression="cost",
        unit="usd",
        higher_is_better=False,
        description=(
            "Dollars per cell. On the recursion that is the seed's whole inner campaign; elsewhere "
            "it is the sample's own priced steps. Unavailable rather than free where nothing "
            "priced it."
        ),
    ),
    MetricSpec(
        key="tokens",
        label="Tokens",
        expression="tokens",
        unit="tokens",
        higher_is_better=False,
        description="Tokens per cell, on the same two sources as Cost.",
    ),
    MetricSpec(
        key="rank",
        label="Ground-truth rank",
        expression="ground_truth_rank",
        unit="rank",
        higher_is_better=False,
        description="Where the true answer landed in the pipeline's ranking. Lower is better.",
    ),
)

_COMPOSITE_FITNESS = MetricSpec(
    key="fitness",
    label="Composite fitness",
    expression="fitness",
    unit="level",
    higher_is_better=True,
    description=(
        "What the one scoring formula every campaign here declares makes of the measurand."
    ),
)


def catalogue_for(available: frozenset[str]) -> tuple[MetricSpec, ...]:
    seed_lift = "lift" in available
    out: list[MetricSpec] = []
    if seed_lift or "fitness" in available:
        out.append(
            MetricSpec(
                key=MEASURAND,
                label="Lift over origin" if seed_lift else "Fitness",
                expression="lift" if seed_lift else "fitness",
                unit="delta" if seed_lift else "level",
                higher_is_better=True,
                description=(
                    "How far each seed's own inner campaign moved off its own origin, averaged "
                    "over its round budget. This is the number the outer loop scores."
                    if seed_lift
                    else "The value each cell was scored at, under the one formula every campaign "
                    "here declares. A cell here is a sample, which has no origin of its own to "
                    "lift over."
                ),
            )
        )
    # Only beside the lift: where the measurand IS fitness it is one number, one entry.
    if seed_lift and "fitness" in available:
        out.append(_COMPOSITE_FITNESS)
    out.extend(m for m in _ENTRIES if m.expression in available)
    return tuple(out)


def compile_metric(expression: str) -> CompiledExpression:
    """Checked against every channel, wider than the served namespace: an unanswerable one is unscorable, never refused."""
    compiled = compile_expression(expression, source="compare metric expression")
    unknown = compiled.names - set(CHANNELS)
    if unknown:
        raise ValueError(
            f"Metric expression {expression!r} names {sorted(unknown)}, which no cell carries. "
            f"Available: {sorted(CHANNELS)}."
        )
    return compiled


def resolve_metric(
    selector: str, available: frozenset[str]
) -> tuple[MetricSpec, CompiledExpression]:
    if selector.startswith(CUSTOM_METRIC_PREFIX):
        expression = selector[len(CUSTOM_METRIC_PREFIX) :].strip()
        if not expression:
            raise ValueError(f"{CUSTOM_METRIC_PREFIX!r} was given no expression.")
        return (
            MetricSpec(
                key=CUSTOM_METRIC_KEY,
                label=expression,
                expression=expression,
                unit="composed",
                higher_is_better=None,
                description="Composed from the channels this selection carries.",
            ),
            compile_metric(expression),
        )

    catalogue = catalogue_for(available)
    named = next((m for m in catalogue if m.key == selector), None)
    if named is None:
        offered = sorted(m.key for m in catalogue)
        raise ValueError(
            f"Metric {selector!r} is not one this selection can answer. It offers "
            f"{offered or 'nothing — no channel is carried by every campaign here'}, or "
            f"'{CUSTOM_METRIC_PREFIX}<expression>' over {sorted(available)}."
        )
    return (named, compile_metric(named.expression))


__all__ = [
    "CHANNELS",
    "CUSTOM_METRIC_KEY",
    "CUSTOM_METRIC_PREFIX",
    "MEASURAND",
    "MetricSpec",
    "MetricUnit",
    "available_channels",
    "catalogue_for",
    "cell_channels",
    "compile_metric",
    "merge_cells",
    "resolve_metric",
]
