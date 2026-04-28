"""tqdm progress-bar lifecycle driven by ``RunListener`` events."""

from __future__ import annotations

from typing import Any

from promptpotter.domain.phases import CampaignPhase, PhaseEvent


class _BarTracker:
    """tqdm bar lifecycle driven by ``RunListener`` events. Optional helper."""

    def __init__(self, sp_budget_ttest: int) -> None:
        self.budget = sp_budget_ttest
        self._pbar: Any = None
        self._cand_idx: int = -1
        self._in_baseline: bool = False

    def close(self) -> None:
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None
        self._cand_idx = -1

    def write(self, line: str) -> None:
        from tqdm.auto import tqdm

        tqdm.write(line)

    def on_phase(self, event: PhaseEvent) -> None:
        if event.event == "exit":
            if event.phase == CampaignPhase.BASELINE:
                self.close()
                self._in_baseline = False
            elif event.phase == CampaignPhase.L1_SCORE:
                self.close()
        elif event.event == "enter" and event.phase == CampaignPhase.BASELINE:
            self._in_baseline = True

    def on_sample_started(self, ci: int, ct: int, qt: int) -> None:
        from tqdm.auto import tqdm

        if self._in_baseline:
            if self._pbar is None:
                self._pbar = tqdm(total=qt or 1, desc="  baseline", unit="q", leave=False, ncols=60)
            return
        if ci != self._cand_idx:
            self.close()
            self._cand_idx = ci
            # Bar tops out at sp_budget_ttest; early t-test elimination leaves
            # it partially filled — which is the signal, not a bug.
            self._pbar = tqdm(
                total=self.budget, desc=f"  cand {ci + 1}/{ct}", unit="q", leave=False, ncols=60
            )

    def on_sample_scored(self) -> None:
        if self._pbar is not None:
            self._pbar.update(1)
