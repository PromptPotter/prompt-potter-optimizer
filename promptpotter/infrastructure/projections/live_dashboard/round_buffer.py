from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from promptpotter.domain.results import ScoredCandidate, ScoreSummary
from promptpotter.domain.run_records import CandidateStartedRecord, LedgerFit
from promptpotter.domain.scoring import Grade, MeasuredCell


class WalkedSample(NamedTuple):
    qi: int
    facts: MeasuredCell
    grade: Grade


@dataclass
class ArmSlot:
    """Sample and score records may land BEFORE ``candidate_started`` seeds the slot."""

    idx: int
    total: int = 0
    candidate_id: str = ""
    changes_description: str = ""
    pipeline_overlay: dict[str, Any] | None = None
    prompt_fields: dict[str, Any] | None = None
    resolved_pipeline_params: dict[str, Any] | None = None
    samples: list[WalkedSample] = field(default_factory=list)
    expected_samples: int | None = None
    running: ScoreSummary | None = None
    scores: ScoredCandidate | None = None


@dataclass
class RoundBuffer:
    round_num: int = 0
    candidates: dict[int, ArmSlot] = field(default_factory=dict)
    race_standings: dict[str, float] = field(default_factory=dict)
    race_member: str = ""
    race_current_id: str = ""
    race_n_samples: int = 0
    # ``None`` until the election is held; an empty tuple is a round that HELD.
    elected: tuple[str, ...] | None = None

    def reset(self, round_num: int) -> None:
        self.round_num = round_num
        self.candidates = {}
        self.elected = None
        self.race_standings = {}
        self.race_member = ""
        self.race_current_id = ""
        self.race_n_samples = 0

    def slot(self, idx: int, total: int = 0) -> ArmSlot:
        return self.candidates.setdefault(idx, ArmSlot(idx=idx, total=total))

    def seed_candidate(self, record: CandidateStartedRecord) -> None:
        entry = self.slot(record.candidate_idx, record.candidate_total)
        entry.total = record.candidate_total
        entry.changes_description = record.changes_description
        entry.pipeline_overlay = record.pipeline_overlay
        entry.prompt_fields = record.prompt_fields
        entry.resolved_pipeline_params = record.resolved_pipeline_params

    def append_sample(
        self,
        ci: int,
        ct: int,
        qi: int,
        qt: int,
        facts: MeasuredCell,
        grade: Grade,
        running: ScoreSummary | None,
    ) -> None:
        entry = self.slot(ci, ct)
        if running is not None:
            entry.running = running
        entry.expected_samples = qt
        # Held with its grade: without it `blocks.py::sample_row` renders an ungraded cell MISS.
        entry.samples.append(WalkedSample(qi, facts, grade))

    def set_candidate_scores(self, idx: int, total: int, scores: ScoredCandidate) -> None:
        self.slot(idx, total).scores = scores

    def stamp_fit(self, fit: Mapping[str, LedgerFit]) -> None:
        for entry in self.candidates.values():
            if entry.scores is None or (stamped := fit.get(entry.scores.label)) is None:
                continue
            # An unset stamp is skipped: a cold ruler's absent θ must not blank a banked value.
            entry.scores = entry.scores.model_copy(
                update={k: v for k, v in stamped if v is not None}
            )

    def mark_selected(self, selected_labels: Sequence[str]) -> None:
        self.elected = tuple(selected_labels)

    def record_race_standing(
        self, member: str, current_id: str, n_samples: int, p_best: float
    ) -> None:
        self.race_standings[current_id] = p_best
        self.race_member = member
        self.race_current_id = current_id
        self.race_n_samples = n_samples


__all__ = ["ArmSlot", "RoundBuffer", "WalkedSample"]
