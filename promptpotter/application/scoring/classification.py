from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.domain.results import ArmOutcome, degradation_reading
from promptpotter.domain.results_health import classify_result, is_deprecated
from promptpotter.domain.validators import BrokenSignal, StopRule, StopSignal
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell, MeasuredCell


def terminal_ranking(pipeline_data: Mapping[str, Any], schema: PipelineSchema | None) -> list[Any]:
    """Decided by key PRESENCE: an empty terminal list is NO_RESULT, never an earlier node's pool."""
    if not schema:
        return []
    for node in reversed(schema.nodes):
        if node.emits_ranking and (ranking := node.ranking_in(pipeline_data)) is not None:
            return ranking
    return []


def extract_warning_types(facts: MeasuredCell) -> list[str]:
    return classify_result(facts).all_codes


@shapes_optimizer_prompt
def scoreable_rows(results: Iterable[GradedCell]) -> list[GradedCell]:
    """A DEPRECATED row stays: dropping a refusal pays the prompt accuracy for the cells it could not answer."""
    return [cell for cell in results if cell.scored]


class DegradationCheck:
    name = "degradation"

    def __init__(
        self, threshold: float = 0.4, min_samples: int = 3, *, fatal_fastpath: bool = True
    ) -> None:
        self.threshold = threshold
        self.min_samples = min_samples
        self.fatal_fastpath = fatal_fastpath

    def check(self, results: Sequence[GradedCell]) -> StopSignal | None:
        if self.fatal_fastpath and results:
            classification = classify_result(results[-1].facts)
            fatal = classification.dominant_fatal
            if fatal is not None:
                n = len(results)
                return BrokenSignal(
                    self.name,
                    ArmOutcome.BROKEN,
                    degradation_reading(
                        source=self.name,
                        degraded_count=n,
                        total_scored=n,
                        warning_types=dict.fromkeys(classification.fatal_codes, 1),
                        dominant_warning=fatal,
                        fatal=True,
                    ),
                )

        n = len(results)
        if n < self.min_samples:
            return None
        # Deprecated samples only: an advisory transient must not eliminate a candidate.
        degraded = sum(1 for r in results if is_deprecated(r.facts))
        if degraded / n < self.threshold:
            return None

        wtypes: Counter[str] = Counter()
        for r in results:
            wtypes.update(extract_warning_types(r.facts))
        dominant = max(wtypes, key=wtypes.get) if wtypes else "unknown"  # type: ignore[arg-type]
        return BrokenSignal(
            self.name,
            ArmOutcome.BROKEN,
            degradation_reading(
                source=self.name,
                degraded_count=degraded,
                total_scored=n,
                warning_types=wtypes,
                dominant_warning=dominant,
            ),
        )

    def earliest_stop(
        self,
        results: Sequence[GradedCell],
        upcoming: Sequence[tuple[Sample, GradedCell | None]],
    ) -> int | None:
        """The RATE alone — the fatal fast-path fires on one row's content."""
        degraded = sum(1 for r in results if is_deprecated(r.facts))
        for m, (_, row) in enumerate(upcoming, start=len(results) + 1):
            degraded += row is None or is_deprecated(row.facts)
            if m >= self.min_samples and degraded / m >= self.threshold:
                return m
        return None


def build_degradation_checks(config: CampaignConfig) -> list[StopRule]:
    opt = config.optimization
    checks: list[StopRule] = []
    if opt.degradation_threshold > 0:
        checks.append(
            DegradationCheck(
                threshold=opt.degradation_threshold,
                fatal_fastpath=opt.degradation_fatal_fastpath,
            )
        )
    return checks


__all__ = [
    "DegradationCheck",
    "build_degradation_checks",
    "extract_warning_types",
    "is_deprecated",
    "scoreable_rows",
    "terminal_ranking",
]
