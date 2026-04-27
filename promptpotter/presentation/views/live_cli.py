"""CLI live display — ``LiveDisplay`` + ``tqdm`` progress bars.

Shares all rendering with the notebook (boxed candidate cards, rich
phase-event panels, ``[NNN]`` per-query counter) and adds a tqdm bar
per candidate during scoring plus one for baseline. All output goes
through ``tqdm.write`` so it doesn't trample an active bar.
"""

from __future__ import annotations

from typing import Any

from tqdm.auto import tqdm

from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.presentation.views.live_display import LiveDisplay


class CliDisplay(LiveDisplay):
    """Terminal listener for ``optimize`` — shared rendering + tqdm bars."""

    def __init__(self, *, sp_budget_ttest: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Per-SP cap; early-stop fills < cap. Used as tqdm ``total=``.
        self.sp_budget_ttest = sp_budget_ttest
        self._pbar: Any = None
        self._current_cand_idx: int = -1
        self._in_baseline: bool = False

    @staticmethod
    def _write(line: str) -> None:
        # tqdm.write plays nicely with an active progress bar; also works
        # when no bar is active (falls back to stdout).
        tqdm.write(line)

    def _close_pbar(self) -> None:
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None
        self._current_cand_idx = -1

    # ------------------------------------------------------------------
    # RunListener display protocol — bar lifecycle around shared base
    # ------------------------------------------------------------------

    def on_phase(self, event: PhaseEvent, view: dict | None) -> None:
        if event.event == "exit":
            if event.phase == CampaignPhase.BASELINE:
                self._close_pbar()
                self._in_baseline = False
            elif event.phase == CampaignPhase.L1_SCORE:
                self._close_pbar()
        elif event.event == "enter" and event.phase == CampaignPhase.BASELINE:
            self._in_baseline = True
        super().on_phase(event, view)

    def on_sample_started(self, ci: int, ct: int, qi: int, qt: int, query_text: str) -> None:
        if self._in_baseline:
            if self._pbar is None:
                self._pbar = tqdm(
                    total=qt or 1,
                    desc="  baseline",
                    unit="q",
                    leave=False,
                    ncols=60,
                )
            return
        if ci != self._current_cand_idx:
            self._close_pbar()
            self._current_cand_idx = ci
            # Bar tops out at sp_budget_ttest; early t-test elimination leaves
            # it partially filled — which is the signal, not a bug.
            self._pbar = tqdm(
                total=self.sp_budget_ttest,
                desc=f"  cand {ci + 1}/{ct}",
                unit="q",
                leave=False,
                ncols=60,
            )

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        super().on_sample_scored(ci, ct, qi, qt, result)
        if self._pbar is not None:
            self._pbar.update(1)

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pp_override: dict | None,
    ) -> None:
        # Close any still-open bar (e.g. final query of prior candidate) so
        # the header lands above the fresh bar.
        self._close_pbar()
        super().on_candidate_started(idx, total, changes_description, pp_override)

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        self._close_pbar()
        super().on_candidate_scored(idx, total, scores)

    def on_round_complete(self, rr: Any, l1_stall_count: int) -> None:
        self._close_pbar()
        super().on_round_complete(rr, l1_stall_count)
