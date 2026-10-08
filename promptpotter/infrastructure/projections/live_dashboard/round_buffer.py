"""The per-round candidate buffer behind ``current_round.candidates``. The view's snapshot fan-out routes each kind to
one mutator here, and the render functions read these fields verbatim."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from promptpotter.domain.results import OverlapReading
from promptpotter.domain.results_health import terminal_node
from promptpotter.domain.run_records import LedgerFit
from promptpotter.domain.scoring import QueryMeasurement, recorded_cost_s, recorded_elapsed_s
from promptpotter.domain.spend import TokenAccount


@dataclass
class RoundBuffer:
    round_num: int = 0
    candidates: dict[int, dict[str, Any]] = field(default_factory=dict)
    # The round's race: each candidate's latest P(best) by id, and the eliminator's last reading.
    # Candidate ids are round-scoped, so the map never outlives the round number.
    race_standings: dict[str, float] = field(default_factory=dict)
    race_member: str = ""
    race_current_id: str = ""
    race_n_samples: int = 0
    # The round's own reading, as opposed to what its candidates measured — bought at the
    # election, so it arrives in one stamp rather than converging.
    overlap: OverlapReading | None = None

    def reset(self, round_num: int) -> None:
        """A new round number clears the candidate buffer; historical rounds[] is untouched."""
        self.round_num = round_num
        self.candidates = {}
        self.race_standings = {}
        self.race_member = ""
        self.race_current_id = ""
        self.race_n_samples = 0
        self.overlap = None

    def stamp_overlap(self, overlap: OverlapReading | None) -> None:
        """The best-so-far line on its shared cells, off the ``RoundResult`` the election record carries
        live. Measured just before that record fires (``runner/overlap.py``), and what answers
        "better than C0" when the δ scale underneath θ has collapsed."""
        self.overlap = overlap

    def slot(self, idx: int, total: int = 0) -> dict[str, Any]:
        """Lazy-init a candidate slot: sample / score callbacks may fire BEFORE ``candidate_started`` seeds it, so all
        mutators funnel here. The canonical display ``label`` is composed downstream, not stored here."""
        return self.candidates.setdefault(
            idx,
            {
                "idx": idx,
                "total": total,
                "changes_description": "",
                "samples": [],
                "scores": None,
            },
        )

    def seed_candidate(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pipeline_overlay: dict[str, Any] | None,
        prompt_fields: dict[str, Any] | None,
        resolved_pipeline_params: dict[str, Any] | None,
    ) -> None:
        """Seed a slot so CURRENT shows labelled pending rows before scoring lands. ``prompt_fields`` + ``pipeline_overlay`` are the
        seed-able half the steer panel forks from — surfacing them live makes an in-flight candidate steerable."""
        entry = self.slot(idx, total)
        entry["total"] = total
        entry["changes_description"] = changes_description
        entry["pipeline_overlay"] = pipeline_overlay
        entry["prompt_fields"] = prompt_fields
        entry["resolved_pipeline_params"] = resolved_pipeline_params

    def append_sample(
        self,
        ci: int,
        ct: int,
        qi: int,
        qt: int,
        result: dict[str, Any],
    ) -> None:
        pd = result.get("pipeline_data") or {}
        query_time = recorded_elapsed_s(cast("QueryMeasurement", result))
        # Both facts, never one picked here: a replay's elapsed is a true 0.0 and this is what the
        # cell took when it was measured. Which one a column SHOWS is `scoring.py::shown_seconds`.
        work_time = recorded_cost_s(cast("QueryMeasurement", result))
        # The row's whole token account, from the one place that carries it.
        account = TokenAccount.from_step_tokens(pd)
        # The scorer rides the candidate's running fitness (composite/accuracy/
        # hits/total over samples-so-far) out on the sample. Store it on the slot
        # so the live row serves a moving fitness before the final ``scores`` land.
        running = result.get("_running")
        if isinstance(running, dict):
            self.slot(ci, ct)["running"] = running
        # The walk's length — a live row's denominator, known nowhere else until the score
        # report lands.
        self.slot(ci, ct)["expected_samples"] = qt
        self.slot(ci, ct)["samples"].append(
            {
                "qi": qi,
                "qt": qt,
                "sample_id": result.get("sample_id"),
                # The walk's archive run (`query_loop._with_running`) — the candidate row reads its
                # cell address off it before the score report carries one.
                "run_id": result.get("run_id"),
                "fitness": result.get("fitness"),
                "cached": bool(result.get("cached", False)),
                "query": result.get("query") or "",
                # A scored row spells it ``predicted`` (``round_NNNN.json::results[]``); the
                # live-sample dict's outbound key is ``prediction``.
                "prediction": result.get("predicted") or "",
                "ground_truth": result.get("ground_truth") or "",
                "time_s": None if query_time is None else round(query_time, 2),
                "cost_s": None if work_time is None else round(work_time, 2),
                # Two channels, deliberately: `error` is the human message the tape RENDERS,
                # `error_category` the typed one `is_error_result` ASKS (`shared/errors.py`).
                "error": result.get("error"),
                "error_category": result.get("error_category"),
                # A third state beside scored and errored (`domain/scoring.py::is_unscored`). It
                # rides the buffer because `blocks.py` decides the row's status from this dict
                # alone, and without it an ungraded row arrives carrying no `fitness` and renders
                # MISS — the browser then reporting the formula's silence as the arm's failure,
                # while the CLI tape beside it reads UNSC off the same row.
                "unscored": result.get("unscored"),
                "terminal_node": terminal_node(result),
                "input_tokens": account.input if account else None,
                "output_tokens": account.output if account else None,
                "cache_read_tokens": account.cache_read if account else None,
            }
        )

    def set_candidate_scores(self, idx: int, total: int, scores: dict[str, Any]) -> None:
        """Bank the score report as it stood at ``candidate_scored``.

        A COPY of ``round_result.candidate_scores``, never the same object: the payload arrives as
        ``model_dump()``, and the election replaces those entries with ``model_copy(update=…)``
        results this buffer never sees. ``stamp_fit`` is how those later stamps arrive."""
        self.slot(idx, total)["scores"] = scores

    def stamp_fit(self, fit: Mapping[str, LedgerFit]) -> None:
        """Fold the election's per-arm stamps onto the rows ``set_candidate_scores`` banked.

        Matched on ``label``, for the reason ``mark_winner`` gives. FOLDED rather than kept beside,
        so the slot stays this candidate's current numbers and no reader picks between two of them.
        ``exclude_none`` so a cold ruler's absent θ cannot blank a value already banked."""
        for entry in self.candidates.values():
            scores = entry.get("scores")
            if not isinstance(scores, dict):
                continue
            stamped = fit.get(str(scores.get("label") or ""))
            if stamped is not None:
                scores.update(stamped.model_dump(exclude_none=True))

    def mark_selected(self, selected_labels: Sequence[str]) -> None:
        """Mark the selection from the ``ElectionRecord`` — its OWN record, at its own coordinate.
        Every other slot is re-stamped ``False`` in the same pass, so a re-fire cannot leave a
        stale mark beside the new one.

        Matched on ``label``, which is what that record carries and why: a resume re-mints
        candidate ids, so the id is not stable across one. Within a round the label is the canonical
        ``C{round}.{n}`` and unique — never ``changes_description``, which is prose and can repeat.

        A HELD round selects nobody, and the record says so by carrying NO label rather than the
        retained parent's, which belongs to no slot of this round."""
        chosen = set(selected_labels)
        for entry in self.candidates.values():
            label = str((entry.get("scores") or {}).get("label") or "")
            entry["is_selected"] = label in chosen

    def record_race_standing(
        self, member: str, current_id: str, n_samples: int, p_best: float
    ) -> None:
        """One candidate's P(best) reading from eliminator ``member``."""
        self.race_standings[current_id] = p_best
        self.race_member = member
        self.race_current_id = current_id
        self.race_n_samples = n_samples


__all__ = ["RoundBuffer"]
