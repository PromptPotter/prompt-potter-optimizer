"""The per-round candidate buffer behind ``current_round.nodes.l1_score``. The view's snapshot fan-out routes each kind to
one mutator here, and the render functions read these fields verbatim."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promptpotter.domain.results import is_round_winner
from promptpotter.infrastructure.projections.live_state import top_n_p_best


@dataclass
class RoundBuffer:
    round_num: int = 0
    candidates: dict[int, dict[str, Any]] = field(default_factory=dict)
    p_best_top: list[dict[str, Any]] = field(default_factory=list)

    def reset(self, round_num: int) -> None:
        """A new round number clears the candidate buffer; historical rounds[] is untouched."""
        self.round_num = round_num
        self.candidates = {}
        self.p_best_top = []

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
        query_time = float(pd.get("total_time", 0.0) or 0.0)
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
                # ``_fmt_sample_line`` reader stays on ``prediction`` because
                # that is the live-sample dict's outbound key — the mismatch was
                # only on the inbound source name.
                "prediction": result.get("predicted") or "",
                "ground_truth": result.get("ground_truth") or "",
                "time_s": round(query_time, 2),
                "terminal_node": pd.get("terminal_node") or "",
                "input_tokens": pd.get("input_tokens") if in_tok is None else in_tok,
                "output_tokens": pd.get("output_tokens") if out_tok is None else out_tok,
            }
        )

    def set_candidate_scores(self, idx: int, total: int, scores: dict[str, Any]) -> None:
        """Store the score report verbatim — single source of truth shared with
        ``round_result.candidate_scores`` (same dict instance)."""
        self.slot(idx, total)["scores"] = scores

    def mark_winner(self, winner_candidate_id: str) -> None:
        """Crown the elected candidate, at the ELECTION rather than at round close. Matched on
        ``candidate_id``: slot order is the launch order, not the election's. Every other slot
        is re-stamped ``False`` in the same pass, so a re-fire cannot leave a stale crown beside
        the new one.

        A HELD round crowns nobody because the id it carries is the RETAINED INCUMBENT's, which
        belongs to no slot of this round — NOT because that id is empty, which it is not. The
        no-crown case therefore rests on the match failing, which is exactly why this keys on
        identity: a positional or prose match could accidentally succeed."""
        for entry in self.candidates.values():
            cid = str((entry.get("scores") or {}).get("candidate_id") or "")
            entry["is_winner"] = is_round_winner(cid, winner_candidate_id)

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
