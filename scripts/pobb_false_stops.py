"""PoBB's false stops on equal arms: the table of `paper/notes/stopping-rule-false-stops.md`.

`python scripts/pobb_false_stops.py [--runs 200] [--cells 20] [--arms 6] [--seed 0]`, offline and
seeded. Each run draws one round: a parent and `--arms` candidates of ONE true ability, on cells of
spread difficulty, each a 0/1 verdict. The parent reads every cell; each candidate walks them
hardest-first (the parent's misses first, as `rasch.py::build_round_order` orders a round) through
the shipped `PoBBCheck` on a cold ruler, one after another, a locked-in arm joining the priors the
next arm races (no backfill). No arm is better than another, so every lock-in is false and every
ε cut is a futility stop on a tie.

Two rules on the same draws: the landed one (the shipped `pobb` knobs) and main's posterior bar
before it (`p_best >= theta_election.admission`, read after every cell), emulated off the race's
own snapshot with the landed lock-in switched off. A last run gives one arm a true lift of
`--lift` logits and counts the runs it locks in within the panel.
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field
from typing import cast

from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.optimizers.nodes import RaceSnapshot
from promptpotter.application.optimizers.potter.knobs import ElectionKnobs, PoBBKnobs
from promptpotter.application.optimizers.potter.pobb.checks import PoBBCheck
from promptpotter.domain.campaign_config import OptimizerChoice
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.scoring import GradedCell

# The spread of cell difficulty, in logits; the bench's `elimination_n_min` default.
_DELTA_SD = 1.5
_N_MIN = 6
_PARENT = ArmKey("parent")


@dataclass
class Tally:
    rounds: int = 0
    rounds_locked: int = 0
    arms: int = 0
    locked: int = 0
    cut: int = 0
    cells: int = 0
    locked_by_arm: dict[int, int] = field(default_factory=dict)

    def row(self, name: str) -> str:
        wrong = (self.locked + self.cut) / self.arms
        return (
            f"| {name} | {self.rounds_locked / self.rounds:.1%} | {self.locked / self.arms:.1%} | "
            f"{self.cut / self.arms:.1%} | {wrong:.1%} | {self.cells / self.arms:.1f} |"
        )


def _cell(key: str, solved: bool) -> GradedCell:
    grade = 1.0 if solved else 0.0
    return GradedCell.from_wire({"sample_key": key, "fitness": grade, "objective": grade})


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _walk(
    knobs: PoBBKnobs,
    bar: float | None,
    order: list[str],
    parent: list[GradedCell],
    arms: list[dict[str, bool]],
) -> list[tuple[str | None, int]]:
    """Each arm's stop (``locked_in`` / ``eliminated`` / ``None``) and the cells it bought. *bar*
    set: main's lock-in, the posterior bar read off each cell's snapshot."""
    check = PoBBCheck(knobs, n_min=_N_MIN, n_samples=len(order), ruler=None, parent=_PARENT)
    check.register_completed(parent, key=_PARENT)
    ends: list[tuple[str | None, int]] = []
    for at, solves in enumerate(arms):
        key = ArmKey(f"arm{at}")
        seen: list[RaceSnapshot] = []
        check.set_current(key, on_snapshot=seen.append)
        rows: list[GradedCell] = []
        outcome: str | None = None
        for cell in order:
            rows.append(_cell(cell, solves[cell]))
            signal = check.check(rows)
            if bar is not None and seen and seen[-1].p_best >= bar and len(rows) >= _N_MIN:
                outcome = "locked_in"
            elif signal is not None:
                outcome = signal.outcome
            if outcome is not None:
                break
        ends.append((outcome, len(rows)))
        if outcome != "eliminated":
            check.register_completed(rows, key=key)
    return ends


def _round(
    rng: random.Random, cells: int, arms: int, lift: float
) -> tuple[list[str], list[GradedCell], list[dict[str, bool]]]:
    delta = {f"c{i}": rng.gauss(0.0, _DELTA_SD) for i in range(cells)}
    parent = {key: rng.random() < _logistic(-d) for key, d in delta.items()}
    # Hardest-first: the parent's misses, then its hits, each hardest first.
    order = sorted(delta, key=lambda k: (parent[k], -delta[k]))
    walked = [
        {key: rng.random() < _logistic((lift if at == 0 else 0.0) - d) for key, d in delta.items()}
        for at in range(arms)
    ]
    return order, [_cell(key, parent[key]) for key in order], walked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--cells", type=int, default=20)
    parser.add_argument("--arms", type=int, default=6)
    parser.add_argument("--lift", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--alpha", type=float, default=None, help="an arm's level; the manifest's")
    args = parser.parse_args()

    selected = select_optimizer(OptimizerChoice(optimizer="potter"))
    landed = cast("PoBBKnobs", selected.knobs("pobb"))
    if args.alpha is not None:
        landed = landed.model_copy(update={"lock_in_alpha": args.alpha})
    admission = cast("ElectionKnobs", selected.knobs("theta_election")).admission
    rules = {
        f"Landed: θ-lift mixture LR, alpha/K = {landed.lock_in_alpha:g} an arm": (landed, None),
        f"Main: p_best >= {admission:g}, read after every cell": (
            landed.model_copy(update={"lock_in": False}),
            admission,
        ),
    }

    print(
        f"Equal arms, {args.runs} rounds of {args.arms} candidates, {args.cells} cells walked "
        f"hardest-first, cold ruler, ε {landed.epsilon:g}, n_min {_N_MIN}, seed {args.seed}.\n"
    )
    print(
        "| Rule | Rounds with a false lock | False locks per arm | False cuts per arm "
        "| Wrong stops per arm | Mean cells per arm |"
    )
    print("|---|---|---|---|---|---|")
    for name, (knobs, bar) in rules.items():
        rng = random.Random(args.seed)
        tally = Tally()
        for _ in range(args.runs):
            ends = _walk(knobs, bar, *_round(rng, args.cells, args.arms, 0.0))
            tally.rounds += 1
            tally.rounds_locked += any(end == "locked_in" for end, _ in ends)
            for end, bought in ends:
                tally.arms += 1
                tally.locked += end == "locked_in"
                tally.cut += end == "eliminated"
                tally.cells += bought
        print(tally.row(name))

    print(f"\nOne arm (walked first) +{args.lift:g} logits, the rest equal:\n")
    print("| Rule | True arm locked in | Rounds with a false lock |")
    print("|---|---|---|")
    for name, (knobs, bar) in rules.items():
        rng = random.Random(args.seed + 1)
        hits = false = 0
        for _ in range(args.runs):
            ends = _walk(knobs, bar, *_round(rng, args.cells, args.arms, args.lift))
            hits += ends[0][0] == "locked_in"
            false += any(end == "locked_in" for end, _ in ends[1:])
        print(f"| {name} | {hits / args.runs:.1%} | {false / args.runs:.1%} |")


if __name__ == "__main__":
    main()
