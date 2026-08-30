"""The per-round candidate buffer behind ``current_round.nodes.l1_score``. The view's snapshot fan-out routes each kind to
one mutator here, and the render functions read these fields verbatim."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from promptpotter.domain.results import OverlapReading
from promptpotter.domain.run_records import LedgerFit
from promptpotter.domain.scoring import QueryMeasurement, recorded_elapsed_s
from promptpotter.infrastructure.projections.live_state import top_n_p_best


@dataclass
class RoundBuffer:
    round_num: int = 0
    candidates: dict[int, dict[str, Any]] = field(default_factory=dict)
    p_best_top: list[dict[str, Any]] = field(default_factory=list)
    # The round's own reading, as opposed to what its candidates measured — bought at the
    # election, so it arrives in one stamp rather than converging.
    overlap: OverlapReading | None = None

    def reset(self, round_num: int) -> None:
        """A new round number clears the candidate buffer; historical rounds[] is untouched."""
        self.round_num = round_num
        self.candidates = {}
        self.p_best_top = []
        self.overlap = None

    def stamp_overlap(self, overlap: OverlapReading | None) -> None:
        """The parent line on its shared cells, off the ``RoundResult`` the election record carries
        live. Measured just before that record fires (``l1/score/overlap.py``), and what answers
        "better than C0" when the δ scale underneath θ has collapsed."""
        self.overlap = overlap

    def slot(self, idx: int, total: int = 0) -> dict[str, Any]:
        """Lazy-init a candidate slot: sample / score / p_best callbacks may fire BEFORE ``candidate_started`` seeds it, so all
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
        pp_override: dict[str, Any] | None,
        prompt_fields: dict[str, Any] | None,
        resolved_pipeline_params: dict[str, Any] | None,
    ) -> None:
        """Seed a slot so CURRENT shows labelled pending rows before scoring lands. ``prompt_fields`` + ``pp_override`` are the
        seed-able half the steer panel forks from — surfacing them live makes an in-flight candidate steerable."""
        entry = self.slot(idx, total)
        entry["total"] = total
        entry["changes_description"] = changes_description
        entry["pp_override"] = pp_override
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
        # Tokens may live on result or pd; prefer result, preserve 0 vs None.
        in_tok = result.get("input_tokens")
        out_tok = result.get("output_tokens")
        # The scorer rides the candidate's running fitness (composite/accuracy/
        # hits/total over samples-so-far) out on the sample. Store it on the slot
        # so the live l1_score block serves a moving fitness before the final
        # ``scores`` land (folder-UI parity for no-browser readers).
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
                "fitness": result.get("fitness"),
                "cached": bool(result.get("cached", False)),
                "query": result.get("query") or "",
                # Scored result dicts carry the field as ``predicted`` (past
                # tense, matching round_NNNN.json::results[]). Reading
                # ``prediction`` here returned None on every sample → the live
                # tape rendered every row as an empty prediction. The compact
                # ``render.py::fmt_sample_line`` reader stays on ``prediction`` because
                # that is the live-sample dict's outbound key — the mismatch was
                # only on the inbound source name.
                "prediction": result.get("predicted") or "",
                "ground_truth": result.get("ground_truth") or "",
                "time_s": None if query_time is None else round(query_time, 2),
                # Two channels, deliberately: `error` is the human message the tape RENDERS,
                # `error_category` the typed one `is_error_result` ASKS (`shared/errors.py`).
                "error": result.get("error"),
                "error_category": result.get("error_category"),
                "terminal_node": pd.get("terminal_node") or "",
                "input_tokens": pd.get("input_tokens") if in_tok is None else in_tok,
                "output_tokens": pd.get("output_tokens") if out_tok is None else out_tok,
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

    def mark_winner(self, winner_label: str) -> None:
        """Crown the elected candidate from the ``ElectionRecord`` — the crown's OWN record, at its
        own coordinate. Every other slot is re-stamped ``False`` in the same pass, so a re-fire
        cannot leave a stale crown beside the new one.

        Matched on ``label``, which is what that record carries and why: a resume re-mints
        candidate ids, so the id is not stable across one. Within a round the label is the canonical
        ``C{round}.{n}`` and unique, so this is an identity match, not the prose one
        ``is_round_winner`` warns off — ``changes_description`` is the prose, and it can repeat.

        A HELD round crowns nobody, and the record says so by carrying an EMPTY label rather than
        the retained parent's, which belongs to no slot of this round."""
        for entry in self.candidates.values():
            label = str((entry.get("scores") or {}).get("label") or "")
            entry["is_winner"] = bool(winner_label) and label == winner_label

    def update_p_best(
        self,
        idx: int,
        total: int,
        current_id: str,
        n_samples: int,
        p_best: float,
    ) -> None:
        """Merge one candidate's P(best), then rebuild the top-5 **by aggregating across the round's slots**. Reading it off this
        one candidate's snapshot lists the priors it was measured against as rivals, and the last to score decides it."""
        cand = self.slot(idx, total)
        current = float(p_best)
        prev = float(cand.get("p_best", current))
        history: list[float] = list(cand.get("p_best_history") or [])
        history.append(current)
        # Cap history at 64 entries — round size rarely exceeds 40.
        if len(history) > 64:
            history = history[-64:]
        cand["p_best"] = current
        cand["p_best_id"] = current_id
        cand["p_best_delta"] = current - prev
        cand["p_best_history"] = history
        cand["p_best_n_samples"] = n_samples

        # Round-wide leaderboard (top-5 by P(best)) — over the candidates this round has
        # readings for, which is the only population the question is about.
        standings = {
            str(c["p_best_id"]): float(c["p_best"])
            for c in self.candidates.values()
            if c.get("p_best_id")
        }
        self.p_best_top = [{"id": cid, "p_best": p} for cid, p in top_n_p_best(standings)]


__all__ = ["RoundBuffer"]
