"""Champion election — promote a state, and coronate a challenger head-to-head.

The reigning champion is the meta-prompt state the shipped optimizer starts from. It is
recorded in ``datasets/_optimizer_meta/champion.json`` (pointer + its prompt_state +
evidence); the champion registry marks its row ``champion``.

A **coronation** is a paired head-to-head between a challenger and the reigning champion.
Because both rows carry a per-cell anchor-to-origin effect, the challenger−champion
difference per cell is just ``challenger.mean_d − champion.mean_d`` (origin cancels) — so
a coronation is computable from the registry alone, no new run, whenever they share cells.
The champion is dethroned only when the pooled CI clears zero (ties keep the incumbent —
stability = distributability).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from promptpotter.application.meta_champion.reducer import (
    CandidateRow,
    champion_pointer_path,
    read_registry,
)
from promptpotter.shared.statistics import paired_diff_posterior


class ChampionPointer(BaseModel):
    """The reigning champion — the distributable pick."""

    model_config = ConfigDict(extra="forbid")

    state_hash: str
    label: str
    prompt_state: dict[str, dict[str, str]]
    anchor_effect: float
    n_cells: int
    since: str


class CoronationOutcome(BaseModel):
    """The paired verdict of a challenger vs the reigning champion."""

    model_config = ConfigDict(extra="forbid")

    challenger: str
    champion: str | None
    effect: float  # pooled (challenger − champion) across shared cells
    ci_lo: float
    ci_hi: float
    n_shared_cells: int
    decision: str  # crowned | held | insufficient
    detail: str


def read_champion_pointer() -> ChampionPointer | None:
    path = champion_pointer_path()
    if not path.is_file():
        return None
    try:
        return ChampionPointer.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_champion_pointer(pointer: ChampionPointer) -> Path:
    path = champion_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(pointer.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _find_row(base_dir: Path, state_hash: str) -> CandidateRow | None:
    registry = read_registry(base_dir)
    if registry is None:
        return None
    return next((r for r in registry.candidates if r.state_hash == state_hash), None)


def promote_champion(base_dir: Path, state_hash: str) -> tuple[ChampionPointer | None, str]:
    """Elect ``state_hash`` as the reigning champion; write the pointer. Returns
    ``(pointer, message)`` — ``pointer`` is ``None`` when the state is unknown."""
    row = _find_row(base_dir, state_hash)
    if row is None:
        return None, (
            f"champion promote: state {state_hash!r} not in the registry. "
            "Run `champion refresh` first, or check the hash."
        )
    pointer = ChampionPointer(
        state_hash=row.state_hash,
        label=row.label,
        prompt_state=row.prompt_state,
        anchor_effect=row.anchor_effect,
        n_cells=row.n_cells,
        since=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    path = _write_champion_pointer(pointer)
    return (
        pointer,
        f"Champion is now {row.state_hash} (anchor {row.anchor_effect:+.4f}). Pointer: {path}",
    )


def coronate(
    base_dir: Path, challenger_hash: str, *, promote_on_win: bool = True
) -> CoronationOutcome:
    """Paired head-to-head of ``challenger_hash`` vs the reigning champion, from the
    registry's per-cell anchor effects. Crowns the challenger (writes the pointer) when
    the pooled CI clears zero and ``promote_on_win``."""
    challenger = _find_row(base_dir, challenger_hash)
    if challenger is None:
        return CoronationOutcome(
            challenger=challenger_hash,
            champion=None,
            effect=0.0,
            ci_lo=0.0,
            ci_hi=0.0,
            n_shared_cells=0,
            decision="insufficient",
            detail=f"challenger {challenger_hash!r} not in the registry",
        )
    pointer = read_champion_pointer()
    if pointer is None:
        # No incumbent — the first promotion is uncontested.
        if promote_on_win:
            promote_champion(base_dir, challenger_hash)
        return CoronationOutcome(
            challenger=challenger_hash,
            champion=None,
            effect=challenger.anchor_effect,
            ci_lo=challenger.ci_lo,
            ci_hi=challenger.ci_hi,
            n_shared_cells=challenger.n_cells,
            decision="crowned" if promote_on_win else "held",
            detail="no incumbent — challenger crowned uncontested",
        )
    champion = _find_row(base_dir, pointer.state_hash)
    ch_by_cell = {c.cell: c.mean_d for c in challenger.per_cell_effects}
    cm_by_cell = {c.cell: c.mean_d for c in (champion.per_cell_effects if champion else [])}
    shared = sorted(c for c in ch_by_cell if c in cm_by_cell)
    if not shared:
        return CoronationOutcome(
            challenger=challenger_hash,
            champion=pointer.state_hash,
            effect=0.0,
            ci_lo=0.0,
            ci_hi=0.0,
            n_shared_cells=0,
            decision="insufficient",
            detail=(
                "challenger and champion share no measured cells — run both on the "
                "canonical panel (`new promptpotter-self`) before coronating"
            ),
        )
    effect, se, n = paired_diff_posterior(
        [ch_by_cell[c] for c in shared], [cm_by_cell[c] for c in shared]
    )
    ci_lo, ci_hi = effect - 1.96 * se, effect + 1.96 * se
    if ci_lo > 0:
        decision = "crowned"
        detail = f"pooled (challenger - champion) {effect:+.4f} clears 0 over {n} cells"
        if promote_on_win:
            promote_champion(base_dir, challenger_hash)
    else:
        decision = "held"
        detail = (
            f"pooled {effect:+.4f} [{ci_lo:+.3f}, {ci_hi:+.3f}] does not clear 0 — incumbent holds"
        )
    return CoronationOutcome(
        challenger=challenger_hash,
        champion=pointer.state_hash,
        effect=effect,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_shared_cells=n,
        decision=decision,
        detail=detail,
    )


__all__ = [
    "ChampionPointer",
    "CoronationOutcome",
    "champion_pointer_path",
    "coronate",
    "promote_champion",
    "read_champion_pointer",
]
