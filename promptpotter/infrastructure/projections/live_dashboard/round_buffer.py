from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from promptpotter.domain.paired_reading import ArmKey, ArmName
from promptpotter.domain.results import ArmState, Election, ScoreSummary, arm_name
from promptpotter.domain.run_records import (
    AnnouncedArms,
    ArmRecord,
    RoundEnteredRecord,
    SampleScoredRecord,
)
from promptpotter.domain.scoring import Grade, MeasuredCell


class WalkedSample(NamedTuple):
    position: int
    facts: MeasuredCell
    grade: Grade


@dataclass
class RoundBuffer:
    round_num: int = 0
    # Every round's: a cell names an arm announced before the buffer reached its round.
    announced: AnnouncedArms = field(default_factory=AnnouncedArms)
    # Each arm of THIS round, in announcement order, with the cells it walked.
    samples: dict[ArmKey, list[WalkedSample]] = field(default_factory=dict)
    race_standings: dict[str, float] = field(default_factory=dict)
    race_member: str = ""
    race_current_id: str = ""
    race_n_samples: int = 0
    # ``None`` until the election is held.
    election: Election | None = None

    def reset(self, round_num: int) -> None:
        self.round_num = round_num
        self.samples = {key: [] for rnd, key in self.announced if rnd == round_num}
        self.election = None
        self.race_standings = {}
        self.race_member = ""
        self.race_current_id = ""
        self.race_n_samples = 0

    def enter(self, record: RoundEnteredRecord) -> None:
        self.announced.enter(record)
        self.reset(record.round)

    def announce(self, record: ArmRecord) -> None:
        self.announced.announce(record)
        if record.round == self.round_num:
            self.samples.setdefault(record.arm, [])

    def take(self, record: SampleScoredRecord) -> ArmKey | None:
        arm = self.announced.take(record)
        if arm is not None and record.round == self.round_num:
            # Held with its grade: without it `blocks.py::sample_row` renders an ungraded cell MISS.
            result = record.result
            self.samples[arm].append(WalkedSample(record.position, result.facts, result.grade))
        return arm

    def arms(self) -> list[tuple[ArmKey, ArmState]]:
        return [(key, self.announced.state(self.round_num, key)) for key in self.samples]

    def fold(self, key: ArmKey) -> ScoreSummary | None:
        return self.announced.fold(self.round_num, key)

    def name(self, key: ArmKey) -> ArmName | None:
        if key not in self.samples:
            return None
        return arm_name(key, self.announced.state(self.round_num, key), self.round_num)

    def label(self, key: ArmKey) -> str:
        return "" if (name := self.name(key)) is None else name.label

    def record_race_standing(
        self, member: str, current_id: str, n_samples: int, p_best: float
    ) -> None:
        self.race_standings[current_id] = p_best
        self.race_member = member
        self.race_current_id = current_id
        self.race_n_samples = n_samples
