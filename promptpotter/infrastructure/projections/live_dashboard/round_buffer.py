"""``RoundBuffer`` — the per-round candidate buffer behind
``dashboard.json::current_round.nodes.l1_score``.

Owned by :class:`~promptpotter.infrastructure.projections.live_dashboard.view.LiveDashboardView`;
its ``_handle_snapshot`` fan-out (``candidate_started`` / ``sample_scored`` /
``candidate_scored`` / ``p_best_update``) routes each snapshot kind to one of
this dataclass's mutators. The render functions in ``render.py`` read these
fields verbatim to build the dashboard output shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promptpotter.infrastructure.projections.live_state import top_n_p_best


@dataclass
class RoundBuffer:
    round_num: int = 0
    candidates: dict[int, dict[str, Any]] = field(default_factory=dict)
    p_best_top: list[dict[str, Any]] = field(default_factory=list)

    def reset(self, round_num: int) -> None:
        """L1_GENERATE:enter clears the candidate buffer; historical rounds[] is untouched."""
        self.round_num = round_num
        self.candidates = {}
        self.p_best_top = []

    def slot(self, idx: int, total: int = 0) -> dict[str, Any]:
        """Lazy-init a candidate slot. Sample / score / p_best callbacks may fire
        before ``candidate_started`` seeds the slot, so all mutators funnel here.

        ``changes_description`` holds the human-readable mutation text; the
        canonical display ``label`` is composed in ``build_l1_score_block``
        from the slot's round + idx via :func:`candidate_label`.
        """
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
        """Seed a slot so CURRENT shows labelled pending rows before scoring lands.

        ``prompt_fields`` + ``pp_override`` are the candidate's evolved
        searchpoint (``OptSearchPoint.prompt_field_dict()`` + pipeline delta) —
        the seed-able half the steer panel forks from. Surfacing them live makes
        an in-flight candidate steerable without its (not-yet-written) round file.
        ``resolved_pipeline_params`` is the config-only resolved config the
        OBSERVE view reads live (the in-flight peer of the round-file field).
        """
        entry = self.slot(idx, total)
        entry["total"] = total
        entry["changes_description"] = changes_description or ""
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
                "terminated_at": pd.get("terminated_at") or "",
                "input_tokens": pd.get("input_tokens") if in_tok is None else in_tok,
                "output_tokens": pd.get("output_tokens") if out_tok is None else out_tok,
            }
        )

    def set_candidate_scores(self, idx: int, total: int, scores: dict[str, Any]) -> None:
        """Store the score report verbatim — single source of truth shared with
        ``round_result.candidate_scores`` (same dict instance)."""
        self.slot(idx, total)["scores"] = scores

    def update_p_best(
        self,
        idx: int,
        total: int,
        current_id: str,
        n_samples: int,
        p_best: float,
    ) -> None:
        """Merge one candidate's P(best) into its slot, then rebuild the top-5 leaderboard.

        Stores this candidate's ``p_best``, its signed delta vs the prior query and a capped
        trajectory; then refreshes the round-wide top-5 consumed by ``build_pobb_block``
        **by aggregating across the round's candidate slots**. The leaderboard was previously
        read straight off this one candidate's ``PoBBSnapshot`` dict, so it listed the priors
        that candidate was measured against as if they were its rivals' standings, and each
        new snapshot overwrote it — the last candidate to score decided the display.
        """
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
