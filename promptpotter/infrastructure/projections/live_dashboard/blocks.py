"""Pure projections from scalar state + ``RoundBuffer`` to the ``dashboard.json`` shape — side-effect free, returning
plain dicts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.domain.dashboard_rows import (
    DashboardSample,
    LiveCandidate,
    SampleStatus,
    sample_status,
)
from promptpotter.domain.results import candidate_label
from promptpotter.domain.scoring import is_verifier_graded
from promptpotter.infrastructure.projections.live_dashboard.state import RacingBlock
from promptpotter.shared.composite import inline_short_formula_values

if TYPE_CHECKING:
    from promptpotter.infrastructure.projections.live_dashboard.round_buffer import RoundBuffer
    from promptpotter.infrastructure.projections.live_state import LiveStateCore


def _trim(text: str, n: int) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def sample_row(s: dict[str, Any]) -> DashboardSample:
    """One buffered sample as the dashboard serves it — the ONE place the tape's facts are
    decided, and where the display trim happens."""
    sid = s.get("sample_id")
    time_s = s.get("time_s")
    cost_s = s.get("cost_s")
    status: SampleStatus = sample_status(s)
    # A verifier-graded row has no label, so the answer/truth pair is both halves of a comparison
    # nobody made — and `prediction` there is the `NO_RESULT` sentinel a ranking mechanism that is
    # not in play left behind. Served EMPTY rather than sentinel-and-blank, so a client can still
    # tell the two apart: `NO_RESULT` beside a real truth is an extraction that broke.
    ground_truth = _trim(s.get("ground_truth") or "", 20)
    graded_by_verifier = is_verifier_graded(ground_truth)
    fitness = s.get("fitness")
    return DashboardSample(
        qi=int(s.get("qi", 0)),
        sample_id=None if sid is None else int(sid),
        status=status,
        # Off the same row `status` was decided from — an errored row carries none, which is
        # what `status == "ERR"` already says.
        fitness=float(fitness) if isinstance(fitness, int | float) else None,
        terminal_node=str(s.get("terminal_node") or ""),
        cached=bool(s.get("cached", False)),
        time_s=float(time_s) if isinstance(time_s, int | float) else None,
        cost_s=float(cost_s) if isinstance(cost_s, int | float) else None,
        predicted="" if graded_by_verifier else _trim(s.get("prediction") or "", 28),
        ground_truth=ground_truth,
        query=_trim(s.get("query") or "", 42),
        input_tokens=s.get("input_tokens"),
        output_tokens=s.get("output_tokens"),
        cache_read_tokens=s.get("cache_read_tokens"),
    )


def _served(cand: dict[str, Any]) -> dict[str, Any]:
    """The candidate's own numbers, best available. Mid-scoring the final ``scores`` are empty, so the scorer's running
    fitness stands in — the same shape, ridden out per sample on ``_running`` — and ``scores`` wins the moment it lands."""
    return cand.get("scores") or cand.get("running") or {}


def build_candidate_rows(
    buffer: RoundBuffer, short_formula_template: str | None
) -> list[LiveCandidate]:
    """This round's candidates in the SAME shape a closed round serves (``rounds[].candidates``), so a reader takes a
    whole row from one half instead of filling one in from the other per field.

    ``scores`` is the ``candidate_scored`` report, folded onto by the election; before it lands, ``running`` is the
    gateway's own per-sample fold. Both carry accuracy's CI, so the whisker widens with its bar. The ``or`` between
    them is a PRECEDENCE, not two spellings of one thing — only ``scores`` carries ``label``, ``candidate_id``
    and ``outcome``. ``label`` is canonical — display sites read it verbatim, and no ``idx + 1``
    arithmetic exists."""
    rows: list[LiveCandidate] = []
    for idx in sorted(buffer.candidates.keys()):
        cand = buffer.candidates[idx]
        served = _served(cand)
        samples = cand.get("samples") or []
        cached = served.get("cached_samples")
        tape = [sample_row(s) for s in samples]
        rows.append(
            LiveCandidate(
                label=candidate_label(buffer.round_num, idx),
                candidate_id=served.get("candidate_id"),
                # The report's once it lands, and until then off the samples, which carry it from
                # the first one — the walk mints the run before it measures anything.
                run_id=served.get("run_id")
                or next((s["run_id"] for s in samples if s.get("run_id")), None),
                accuracy=served.get("accuracy"),
                composite_fitness=served.get("composite_fitness"),
                outcome=served.get("outcome"),
                scored_samples=int(served.get("scored_samples") or len(samples)),
                cached_samples=int(
                    cached if cached is not None else sum(1 for s in samples if s.get("cached"))
                ),
                expected_samples=cand.get("expected_samples"),
                # What measuring this candidate consumed, folded once at `l1/population.py` off
                # the rows themselves. Lands with the score report, like θ and the matched floor
                # below: the buffered samples here carry the flat per-row counts rather than the
                # `pipeline_data` the fold reads, and a second fold over those would be a second
                # spelling of one number.
                input_tokens=served.get("input_tokens"),
                output_tokens=served.get("output_tokens"),
                cache_read_tokens=served.get("cache_read_tokens"),
                evaluators=dict(served.get("evaluators") or {}),
                changes_description=(
                    cand.get("changes_description") or served.get("changes_description") or ""
                ),
                mean_fitness_ci_lo=served.get("mean_fitness_ci_lo"),
                mean_fitness_ci_hi=served.get("mean_fitness_ci_hi"),
                # Everything below lands at the election, folded in by `RoundBuffer.stamp_fit`
                # and `mark_winner` off the one `ElectionRecord` — so the whole verdict is live
                # from the election rather than from the round close, two LLM calls later. Absent
                # before it: the fit needs two arms, and a cold ruler stamps no θ at all.
                theta=served.get("theta"),
                theta_se=served.get("theta_se"),
                theta_caveat=served.get("theta_caveat"),
                reference_accuracy=served.get("reference_accuracy"),
                reference_composite=served.get("reference_composite"),
                reference_lift=served.get("reference_lift"),
                reference_lift_ci_lo=served.get("reference_lift_ci_lo"),
                reference_lift_ci_hi=served.get("reference_lift_ci_hi"),
                is_selected=bool(cand.get("is_selected")),
                prompt_fields=cand.get("prompt_fields"),
                resolved_pipeline_params=cand.get("resolved_pipeline_params"),
                pipeline_overlay=cand.get("pipeline_overlay"),
                samples=tape,
                validation_failures=served.get("validation_failures") or [],
                composite_fitness_formula_short=inline_short_formula_values(
                    short_formula_template, dict(served.get("evaluators") or {})
                ),
            )
        )
    return rows


def build_racing_block(core: LiveStateCore, p_best_top: list[dict[str, Any]]) -> RacingBlock | None:
    """The round's race standing. ``leader_prob`` is the best standing among CANDIDATES — never a max over one
    snapshot's dict, whose other entries are that same candidate's odds against each prior."""
    if not core.current_p_best_id:
        return None
    leader_prob = max(
        [float(row["p_best"]) for row in p_best_top] or list(core.round_p_best.values()) or [0.0]
    )
    return RacingBlock(
        member=core.race_member,
        current_id=core.current_p_best_id,
        n_samples=core.current_p_best_n,
        leader_prob=float(leader_prob),
        posterior_width=float(1.0 - leader_prob),
        top=list(p_best_top),
    )


__all__ = [
    "build_candidate_rows",
    "build_racing_block",
    "sample_row",
]
