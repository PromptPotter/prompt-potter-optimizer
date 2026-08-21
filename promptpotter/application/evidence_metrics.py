"""WHICH number a set of campaigns is compared on — the read-side measurand vocabulary.

Everything here is evaluated PER CELL and aggregated afterwards, and that is the whole design
constraint. A per-cell series gives both an interval on a campaign's mean and a BLOCKED paired
difference against another campaign on the cells both measured; a campaign-level scalar gives
neither, because cell difficulty never cancels. So a metric is a channel read off ONE row, or an
expression over channels — never a number assembled from a second document, which would be a fact
about the campaign rather than about the cell.

Three rules this module exists to enforce, all of which have bitten on real data:

- **A channel a row cannot answer is ABSENT, never zero.** ``total_time`` is 0.0 on a cached
  replay while ``step_timings`` still carries the seconds that were paid for, and
  ``formula/compiler.py::_build_namespace`` binds ``input_tokens = 0`` where a row has no
  ``step_tokens`` at all. Both read as a measurement and are not one.
- **Every channel is a fact about the CELL.** On the L4 recursion a cell IS a whole inner
  campaign, so its lift, its origin, its cost and its round count are the seed's own — read off
  the row that stands for it, never derived from what the outer campaign did afterwards.
- **No unit clamp.** ``compile_scorer`` ends in ``clamp_unit_score`` because a fitness is a
  fitness; seconds, token counts and a signed lift are none of those, and clamping would floor
  every regression at zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import computed_field

from promptpotter.application.scoring.formula.compiler import (
    CompiledExpression,
    compile_expression,
)
from promptpotter.domain.l4.proxies import OUTER_PROXY_KEYS
from promptpotter.domain.strict_model import StrictModel

# The L4 recursion's measurand, in logits: one inner campaign's mean-over-rounds lift over its OWN
# origin. Absent on an ordinary campaign, where a cell is a sample and has no origin of its own.
_LIFT_KEY = OUTER_PROXY_KEYS[0]

# What a number IS, which decides how it reads. `delta` is a signed difference and `level` an
# absolute value, so only the first earns a leading `+`; `composed` is a hand-typed expression whose
# units nothing can name — `higher_is_better` already has that unknown state and this is its twin.
MetricUnit = Literal["level", "delta", "seconds", "usd", "tokens", "rank", "rounds", "composed"]

CUSTOM_METRIC_PREFIX = "expr:"

# The catalogue key whose expression the SELECTION picks: `lift` where the rows carry a per-seed
# delta, `fitness` where a cell is a sample and there is none. One entry, because "Level" and
# "Lift over origin" naming one number is the synonym the root CLAUDE.md forbids.
MEASURAND = "measurand"


class MetricSpec(StrictModel):
    """One pickable metric. Served rather than restated in the browser, so a label and a unit have
    one owner and a picker cannot drift from what the server actually computed."""

    key: str
    label: str
    # The channel expression the key stands for — one evaluation path for a catalogue entry and a
    # hand-typed formula alike, so nothing can make the two disagree.
    expression: str
    unit: MetricUnit
    # ``None`` on a composed expression: ``lift / latency`` is up and ``latency / lift`` is down,
    # and nothing here can tell. The reader is told there is no direction rather than shown a
    # winner picked by a guess.
    higher_is_better: bool | None
    description: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def axis_label(self) -> str:
        """What to CALL this metric's axis: the label, plus the unit only where the unit names
        something the reader does not already have. SERVED, so neither surface hand-writes the
        "unnamed" set below to derive it."""
        return self.label if self.unit in _UNNAMED_UNITS else f"{self.label} ({self.unit})"


# A level and a delta are already in the measurand's own scale, and a composed expression has no
# unit at all — naming any of the three tells the reader nothing they do not have.
_UNNAMED_UNITS: frozenset[str] = frozenset({"level", "delta", "composed"})


# --- Channels: what one row can be asked for -------------------------------------------------


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _finite_sum(values: object) -> float | None:
    """``None`` unless *values* is a non-empty map of numbers. An empty ``step_timings`` is a row
    that recorded no timing, which is absent — summing it to 0.0 is the fabrication this guards."""
    if not isinstance(values, dict) or not values:
        return None
    total = 0.0
    for entry in values.values():
        number = _number(entry)
        if number is None:
            return None
        total += number
    return total


def _step_tokens_sum(pipeline_data: Mapping[str, Any], *keys: str) -> float | None:
    steps = pipeline_data.get("step_tokens")
    if not isinstance(steps, dict) or not steps:
        return None
    total = 0.0
    for entry in steps.values():
        if not isinstance(entry, dict):
            return None
        for key in keys:
            number = _number(entry.get(key))
            if number is None:
                return None
            total += number
    return total


def _row_channels(row: Mapping[str, Any]) -> dict[str, float]:
    """Every channel this ONE row can answer. A key absent from the result is a channel the row
    cannot answer, and every caller downstream treats it that way."""
    pipeline_data = row.get("pipeline_data")
    pd: Mapping[str, Any] = pipeline_data if isinstance(pipeline_data, dict) else {}
    out: dict[str, float] = {}

    def put(name: str, value: float | None) -> None:
        if value is not None:
            out[name] = value

    put("fitness", _number(row.get("fitness")))
    put("rank", _number(row.get("ground_truth_rank")))

    # `step_timings`, never `total_time`: the latter is THIS replay's wall clock, so a cache hit
    # banks 0.0 while the work it replays took minutes. On L4 it is the inner campaign's own span.
    put("latency", _finite_sum(pd.get("step_timings")))

    # The seed's own trajectory. `lift` is what the outer loop SCORES — the mean over the round
    # budget — while `final_lift` is where it actually ended and `peak_lift` the best it reached;
    # a run can score well and end badly, and only carrying all three can show it.
    put("lift", _number(pd.get(_LIFT_KEY)))
    put("origin", _number(pd.get("inner_origin_level")))
    put("final_lift", _number(pd.get("inner_final_lift")))
    put("peak_lift", _number(pd.get("inner_peak_lift")))
    put("rounds", _number(pd.get("inner_rounds_ran")))
    put("round_budget", _number(pd.get("inner_round_budget")))
    put("unworked", _number(pd.get("inner_unworked_s")))

    # Two homes, never a fallback chain: a row is EITHER an inner campaign, whose cost the spawn
    # site forwards, OR a pipeline sample, whose cost rides `step_tokens`. No outer node is
    # `is_llm`, so an L4 row never carries `step_tokens` and the two cannot both answer.
    # `is None`, never `or`: a cell that genuinely cost 0.0 is a MEASUREMENT, and falling through
    # on it would report a free cell as an unmeasured one — this module's own rule, inverted.
    for name, own, steps in (
        ("cost", _number(pd.get("inner_spend_usd")), _step_tokens_sum(pd, "cost_usd")),
        ("tokens", _number(pd.get("inner_tokens")), _step_tokens_sum(pd, "input", "output")),
    ):
        put(name, own if own is not None else steps)

    return out


CHANNELS: tuple[str, ...] = (
    "fitness",
    "lift",
    "origin",
    "final_lift",
    "peak_lift",
    "rounds",
    "round_budget",
    "unworked",
    "latency",
    "cost",
    "tokens",
    "rank",
)


def cell_channels(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """``{cell: {channel: value}}``, keyed by the row's ``query`` — the cell identity that survives
    across campaigns where a per-campaign ``sample_id`` names a different cell.

    Several rows on one cell average. A row that answered NOTHING leaves no key behind: a cell
    present with an empty map would be counted as one the metric could not read, when nothing
    measured it at all."""
    sums: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        cell = row.get("query")
        if not isinstance(cell, str):
            continue
        answered = _row_channels(row)
        if not answered:
            continue
        per_cell = sums.setdefault(cell, {})
        for channel, value in answered.items():
            per_cell.setdefault(channel, []).append(value)
    return {
        cell: {channel: sum(vs) / len(vs) for channel, vs in channels.items()}
        for cell, channels in sums.items()
    }


def available_channels(
    channels_by_campaign: Mapping[str, dict[str, dict[str, float]]],
) -> frozenset[str]:
    """The channels EVERY selected campaign can answer on at least one cell. Intersected, not
    unioned: a metric one campaign carries and another cannot is a comparison with one side
    missing, and offering it invites exactly that reading."""
    per_campaign = [
        frozenset(channel for cell in cells.values() for channel in cell)
        for cells in channels_by_campaign.values()
    ]
    return frozenset.intersection(*per_campaign) if per_campaign else frozenset()


# --- The catalogue, resolved against what the SELECTION carries -------------------------------


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
            "Seconds the seed's inner campaign was blocked rather than working — the machine "
            "suspended, or queued behind the rate limiter another cell was using — and handed "
            "back to its deadline. Read it beside a short run: it separates a cell that was slow "
            "from a box that was oversubscribed while it ran."
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
        expression="rank",
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
        "What each campaign's own scoring formula made of the measurand. Pooling on it averages "
        "numbers produced by different formulas."
    ),
)


def catalogue_for(available: frozenset[str]) -> tuple[MetricSpec, ...]:
    """The metrics THIS selection can actually answer, in picker order.

    A metric no selected campaign carries is not offered at all: a picker listing one is how an
    operator ends up reading a wall of "unavailable" and concluding the number is broken. The
    measurand comes first and resolves against the same set — the seed's own lift where the cells
    carry one, the cell's own fitness where a cell is a sample and there is no origin to lift
    over."""
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
                    else "The value each cell was scored at. A cell here is a sample, which has "
                    "no origin of its own to lift over."
                ),
            )
        )
    # Offered only BESIDE the lift: on the recursion `fitness` is the composed score a campaign's
    # own formula made of that lift, a different number. Where the measurand already IS fitness, a
    # second entry would be one number under two names.
    if seed_lift and "fitness" in available:
        out.append(_COMPOSITE_FITNESS)
    out.extend(m for m in _ENTRIES if m.expression in available)
    return tuple(out)


# --- Compiling ------------------------------------------------------------------------------


def compile_metric(expression: str) -> CompiledExpression:
    """Safe-AST compile over the channel namespace — the same primitive, allow-list and builtins
    every other formula in the package rides, deliberately without ``clamp_unit_score``.

    Checked against every channel a row can answer, and DELIBERATELY wider than the served
    ``namespace``: the catalogue hides a metric this selection cannot answer, while naming one
    anyway through the composed-expression door has to reach the honest reading — every cell
    unscorable, counted rather than zeroed. Narrowing this to the intersection turns that
    reading into a refusal and closes a door the design opened."""
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
    """A catalogue key, or ``expr:<formula>``. One evaluation path for both — a named metric IS its
    expression, so nothing can make the two disagree.

    Raises ``ValueError`` / ``SyntaxError`` on anything unresolvable; the route turns those into a
    clean 400, which is the contract ``routers/campaigns/cycles.py::_resolve_lens`` already holds
    for ``?lens=``. HTTP status is not this layer's to know."""
    if selector.startswith(CUSTOM_METRIC_PREFIX):
        expression = selector[len(CUSTOM_METRIC_PREFIX) :].strip()
        if not expression:
            raise ValueError(f"{CUSTOM_METRIC_PREFIX!r} was given no expression.")
        return (
            MetricSpec(
                key="custom",
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
        # An empty catalogue does NOT mean nothing was measured — `available` is the
        # INTERSECTION, so campaigns each carrying a channel the others cannot answer offer
        # nothing jointly. Say the shared set is empty, never that the rows are.
        offered = sorted(m.key for m in catalogue)
        raise ValueError(
            f"Metric {selector!r} is not one this selection can answer. It offers "
            f"{offered or 'nothing — no channel is carried by every campaign here'}, or "
            f"'{CUSTOM_METRIC_PREFIX}<expression>' over {sorted(available)}."
        )
    return (named, compile_metric(named.expression))


__all__ = [
    "CHANNELS",
    "CUSTOM_METRIC_PREFIX",
    "MEASURAND",
    "MetricSpec",
    "MetricUnit",
    "available_channels",
    "catalogue_for",
    "cell_channels",
    "compile_metric",
    "resolve_metric",
]
