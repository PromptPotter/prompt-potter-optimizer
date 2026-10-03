"""Three-bucket verdict, and ``DegradationCheck``, the stop rule that reads it. Structural-vs-transient is the BACKEND's,
read off the stamped ``WarningKind`` — a warning with no kind is therefore NOT structural, since guessing eliminates
candidates for free. ``infra`` deprecates the sample, so a transient is not one."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.domain.results import ArmOutcome
from promptpotter.domain.results_health import classify_result
from promptpotter.domain.scoring import is_graded
from promptpotter.domain.validators import StopRule, StopSignal
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement


@shapes_optimizer_prompt
def ranked_item_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.emits_ranking:
            keys.extend(node.output_keys)
    return keys


@shapes_optimizer_prompt
def get_ranked_items(r: Mapping[str, Any], ranked_item_keys: list[str] | None = None) -> list[Any]:
    pd = r.get("pipeline_data") or {}
    for key in ranked_item_keys or []:
        val: list[Any] | None = pd.get(key)
        if val:
            return val
    return []


def terminal_ranking(r: Mapping[str, Any], schema: PipelineSchema | None) -> list[Any]:
    """The ranked list from the LAST ranker that emitted its key — decided by key PRESENCE, so an empty terminal list is a legitimate
    NO_RESULT and never a fall-through to an earlier node's candidate pool."""
    pd = r.get("pipeline_data") or {}
    if not schema:
        return []
    for node in reversed(schema.nodes):
        if node.emits_ranking:
            for key in node.output_keys:
                if key in pd:
                    val = pd[key]
                    return val if isinstance(val, list) else []
    return []


def extract_warning_types(result: Mapping[str, Any]) -> list[str]:
    """Every advisory + fatal code seen on this result, for display and tracking. Classification itself is
    :func:`classify_result`'s."""
    return classify_result(result).all_codes


@shapes_optimizer_prompt
def is_deprecated(result: Mapping[str, Any]) -> bool:
    """True iff the classifier flagged the sample fatal or infra-truncated. Both deprecate it for accounting, but only
    ``fatal_codes`` participate in one-sighting fast-path elimination."""
    return classify_result(result).is_fatal


@shapes_optimizer_prompt
def scoreable_rows(results: list[QueryMeasurement]) -> list[QueryMeasurement]:
    """The EVIDENCE population — rows that carry a verdict (``domain/scoring.py::is_graded``). A
    DEPRECATED row stays: a refusal or a truncation is what the prompt produced, and dropping it
    would pay the prompt accuracy for failing on exactly the cells it could not answer.

    **One definition, because every published rate needs its ``n`` and its mean drawn from the same
    filter** — spelled per call site, a fourth exclusion added to one leaves the count describing a
    different population than the value beside it, with nothing raised. Load-bearing at L4, where a
    cell is a whole inner campaign: a floored 0.0 there does not read as "scored nothing", it reads
    as "drove the inner loop maximally DOWN". Deliberately NOT applied inside
    ``selection.py::_mean_fitness_by_cell``, whose own docstring says why.

    The unscored exclusion is also what keeps ``exploration.py::graded_response``'s raise armed for
    the real bug: it reads ``objective`` off this population, so a row with no verdict is gone
    before it gets there and an absent verdict on a row that SHOULD carry one still halts.
    """
    return [r for r in results if is_graded(r)]


class DegradationCheck:
    """The bench's ``BROKEN`` rule, whatever the optimizer: a fatal classification on one sighting,
    or a deprecated share past the threshold. Any single row it lets through is simply skipped."""

    name = "degradation"

    def __init__(
        self, threshold: float = 0.4, min_samples: int = 3, *, fatal_fastpath: bool = True
    ) -> None:
        self.threshold = threshold
        self.min_samples = min_samples
        self.fatal_fastpath = fatal_fastpath

    def check(self, results: list[QueryMeasurement]) -> StopSignal | None:
        if self.fatal_fastpath and results:
            classification = classify_result(results[-1])
            fatal = classification.dominant_fatal
            if fatal is not None:
                n = len(results)
                return StopSignal(
                    self.name,
                    ArmOutcome.BROKEN,
                    {
                        "degraded_rate": 1.0,
                        "degraded_count": n,
                        "total_scored": n,
                        "warning_types": dict.fromkeys(classification.fatal_codes, 1),
                        "dominant_warning": fatal,
                        "fatal": True,
                    },
                )

        n = len(results)
        if n < self.min_samples:
            return None
        # Count only genuinely-deprecated samples (fatal + infra/truncation) toward
        # elimination — NOT advisory transients. A non-fatal advisory warning (e.g.
        # web_search:low_document_count, which fires whenever fewer than max_sites docs
        # are gathered) must not eliminate a candidate that is otherwise scoring well.
        degraded = sum(1 for r in results if is_deprecated(r))
        rate = degraded / n
        if rate < self.threshold:
            return None

        wtypes: Counter[str] = Counter()
        for r in results:
            wtypes.update(extract_warning_types(r))
        dominant = max(wtypes, key=wtypes.get) if wtypes else "unknown"  # type: ignore[arg-type]
        return StopSignal(
            self.name,
            ArmOutcome.BROKEN,
            {
                "degraded_rate": rate,
                "degraded_count": degraded,
                "total_scored": n,
                "warning_types": dict(wtypes),
                "dominant_warning": dominant,
            },
        )

    def earliest_stop(
        self,
        results: list[QueryMeasurement],
        upcoming: Sequence[tuple[Sample, QueryMeasurement | None]],
    ) -> int | None:
        """The RATE alone — the fatal fast-path fires on one row's content."""
        degraded = sum(1 for r in results if is_deprecated(r))
        for m, (_, row) in enumerate(upcoming, start=len(results) + 1):
            degraded += row is None or is_deprecated(row)
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
    "get_ranked_items",
    "is_deprecated",
    "ranked_item_keys_from_schema",
    "scoreable_rows",
    "terminal_ranking",
]
