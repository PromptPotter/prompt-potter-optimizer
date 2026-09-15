from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from promptpotter.application.evidence.metric_catalogue import MetricSpec, merge_cells
from promptpotter.application.evidence.subjects import SubjectReading
from promptpotter.domain.strict_model import StrictModel

_POOLABLE_UNITS: frozenset[str] = frozenset({"seconds", "usd", "tokens", "rounds"})
"""Metric units whose meaning does not depend on WHICH dataset produced the number.

A second is a second on any bank, so a marginal that pools an agent episode on one dataset with a
call on another still answers "how long did this level take". A ``level`` or a ``delta`` is not:
accuracy on two banks answers two different questions, and averaging them reports a number about
no exam anyone sat. This is the same distinction ``Comparability`` draws for the roster — *"the
roster and spend still compare, the numbers do not"* — applied to the one place a factorial read
would otherwise smuggle it past, which is the marginal."""


class FactorLevel(StrictModel):
    """One level of one factor, and what the subjects at that level measured together.

    ``value`` is the MARGINAL — pooled over every cell every subject at this level scored, not a
    mean of their means, so a subject that measured twice as many cells carries twice the weight it
    earned. ``None`` where the factor is not poolable, or where a level holds no scored cell; a
    surface renders that as absent rather than as zero."""

    level: str
    subjects: list[str]
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int


class FactorReading(StrictModel):
    """One factor the selection varies on, with its levels — the axis half of a factorial read.

    Factors are DISCOVERED, never declared: a key that every subject answers identically is not a
    factor, it is a constant, and the operator should not have to name what the roster already
    shows. Discovery spans the dataset each subject ran on and the resolved config it ran under,
    which are two keyspaces and one question.

    ``poolable`` is the verdict on the ``value`` column, and it is a property of the METRIC against
    the roster rather than of this factor: see :data:`_POOLABLE_UNITS`. False leaves the levels and
    their membership intact — grouping is always honest, only the aggregate is not — so a surface
    can still lay the grid out and say why the cells are blank.

    ``confounded_with`` is the one that decides whether a marginal may be BELIEVED, and it is why
    this is a reading rather than a group-by. A roster assembled from campaigns that already ran is
    observational, not a designed grid: nothing balanced it, so two factors can cut the subjects
    into the identical partition and their marginals are then the same number wearing two names.
    Measured, on five banked campaigns: ``agent.model`` split 292.2s against 58.5s and was aliased
    exactly by ``dataset``, so the "slower model" was the harbor bank. Naming the alias is the
    whole defence — a surface can gray the column, and a reader who fixes one of the pair gets an
    honest contrast on the other."""

    key: str
    # Which keyspace the factor came out of. Derived from the key rather than declared: a
    # ``node.param`` dot is what `build_candidate_flat` already uses to keep the two apart. It
    # matters because they are different KINDS of thing — a config leaf is what an experiment
    # varies deliberately, a prompt field is usually what the optimizer is varying FOR you, and
    # offering both as grid axes with no distinction invites reading a treatment as a control.
    kind: Literal["dataset", "config", "prompt_field"]
    levels: list[FactorLevel]
    poolable: bool
    # Other factors that cut this selection into the IDENTICAL partition of subjects, so no
    # evidence here can separate them. Symmetric, so both members name each other.
    confounded_with: list[str]
    note: str


class FactorCell(StrictModel):
    """One cell of a 2-D projection — every subject sharing a (row, column) coordinate, pooled.

    Served rather than left to the browser for the reason every other number here is: a cell
    holding three subjects IS an aggregate, and a surface that averaged them would re-answer under
    its own guess at the weighting (``webapp/CLAUDE.md`` § Scoring authority)."""

    row: str
    col: str
    subjects: list[str]
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int


class FactorGridReading(StrictModel):
    """The 2-D face of the factor cube that the reader asked for.

    A face rather than the cube, permanently. A third factor does not need a third visual
    dimension; it needs a decision about what happens to it, and there are exactly two — it is
    MARGINALISED (pooled into the values here) or it is FIXED (dropped from the selection). Both
    leave a flat grid, so five factors cost the same surface as two and no display dimension is
    ever added — which is also why a fourth factor raises no new question. ``marginalised`` names
    what was collapsed, because a cell silently pooling over an axis the reader forgot about is the
    one way this panel could mislead.

    A projection is REQUESTED rather than served for every pair: a roster varying on fifteen keys
    holds 105 of them, and the reader is looking at one."""

    row_key: str
    col_key: str
    # Only coordinates a subject actually occupies. A ragged grid's empty cell is the ABSENCE of a
    # run, and minting a row for it would report a combination nobody measured.
    cells: list[FactorCell]
    marginalised: list[str]
    poolable: bool
    note: str


_DATASET_FACTOR = "dataset"
_UNSET_LEVEL = "(unset)"


def levels_by_subject(
    rows: list[SubjectReading], configs: Mapping[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """``{subject key: {factor: level}}`` over the keys this selection actually VARIES on.

    Two keyspaces, one question: the dataset a subject ran on, and every ``node.param`` / prompt
    field of the config it ran under. A key every subject answers the same way is dropped — it is
    a constant, and a grid axis with one column is a column.

    A key only SOME subjects carry is kept, at :data:`_UNSET_LEVEL` for the rest. That is the third
    band the compare pane already draws rather than a hole to fill: "this arm has no such param" is
    a finding about the arm, and collapsing it into "same as the others" is how a factorial read
    would report a difference it never ran."""
    candidates: dict[str, dict[str, str]] = {}
    for row in rows:
        flat = {_DATASET_FACTOR: row.dataset_name or _UNSET_LEVEL}
        flat.update(configs.get(row.key) or {})
        candidates[row.key] = flat

    keys = {k for flat in candidates.values() for k in flat}
    varying = sorted(
        k for k in keys if len({flat.get(k, _UNSET_LEVEL) for flat in candidates.values()}) > 1
    )
    return {
        key: {k: flat.get(k, _UNSET_LEVEL) for k in varying} for key, flat in candidates.items()
    }


def _poolable(rows: list[SubjectReading], spec: MetricSpec) -> bool:
    """Whether this metric's readings may be POOLED across the selection. A property of the metric
    against the roster, never of the factor being read — see :data:`_POOLABLE_UNITS`."""
    return spec.unit in _POOLABLE_UNITS or len({r.dataset_name for r in rows}) == 1


def _pool_note(spec: MetricSpec, poolable: bool) -> str:
    """Why the values are absent, for a surface that still has a grouping to lay out."""
    if poolable:
        return ""
    return (
        f"{spec.axis_label} is measured per dataset, and this selection spans more than one — "
        "the levels and their membership are read as usual, but pooling them would average "
        "answers to different questions. Fix the dataset factor, or read a metric in seconds, "
        "dollars or tokens, which mean the same thing on any bank."
    )


def _pool(
    members: list[str], by_key: Mapping[str, SubjectReading], poolable: bool
) -> tuple[float | None, float | None, float | None, int]:
    """Pool the CELLS of every subject in one group, never their means — ``{subject|cell: value}``,
    so two subjects that happen to share a cell id on different datasets stay two readings rather
    than one overwriting the other, and a group measured on more cells earns the tighter interval
    instead of being averaged down to a peer that measured three.

    The ONE pooling path. A level marginal and a grid cell are the same arithmetic over different
    groupings, and a second spelling would be a second chance to weight it differently."""
    return merge_cells(
        {
            f"{s}|{cell}": v
            for s in members
            for cell, v in (by_key[s].values if s in by_key else {}).items()
        }
        if poolable
        else {}
    )


def factors(
    rows: list[SubjectReading],
    levels: Mapping[str, dict[str, str]],
    spec: MetricSpec,
) -> list[FactorReading]:
    """The factorial read: each varying key, its levels, and the marginal at each level.

    Which factor to put on a grid, and which to fix or marginalise away, is the READER's choice and
    is why every factor is served rather than the two that fit on a screen. Serving them all is
    also what makes 3 factors cost no more surface than 2: collapsing an axis is choosing a
    marginal that is already here, not asking for a different read."""
    poolable = _poolable(rows, spec)
    note = _pool_note(spec, poolable)
    by_key = {r.key: r for r in rows}
    keys = sorted({k for lv in levels.values() for k in lv})

    # The partition each factor cuts the roster into, as a canonical frozenset of subject groups.
    # Two factors with the same partition are indistinguishable HERE however different they are in
    # principle — this compares what the evidence can separate, never what the names mean.
    partitions: dict[str, frozenset[frozenset[str]]] = {}
    for key in keys:
        buckets: dict[str, set[str]] = {}
        for subject, subject_levels in levels.items():
            buckets.setdefault(subject_levels[key], set()).add(subject)
        partitions[key] = frozenset(frozenset(v) for v in buckets.values())

    out: list[FactorReading] = []
    for key in keys:
        grouped: dict[str, list[str]] = {}
        for subject, subject_levels in levels.items():
            grouped.setdefault(subject_levels[key], []).append(subject)
        readings: list[FactorLevel] = []
        for level in sorted(grouped):
            members = sorted(grouped[level])
            value, lo, hi, n_cells = _pool(members, by_key, poolable)
            readings.append(
                FactorLevel(
                    level=level,
                    subjects=members,
                    value=value,
                    ci_lo=lo,
                    ci_hi=hi,
                    n_cells=n_cells,
                )
            )
        out.append(
            FactorReading(
                key=key,
                kind=(
                    "dataset"
                    if key == _DATASET_FACTOR
                    else "config"
                    if "." in key
                    else "prompt_field"
                ),
                levels=readings,
                poolable=poolable,
                confounded_with=sorted(
                    other for other in keys if other != key and partitions[other] == partitions[key]
                ),
                note=note,
            )
        )
    return out


def grid_reading(
    rows: list[SubjectReading],
    levels: Mapping[str, dict[str, str]],
    spec: MetricSpec,
    axes: tuple[str, str],
) -> FactorGridReading:
    """Pool every (row, column) coordinate of the requested projection, over the same ``_pool`` the
    level marginals use — so a row of the grid and that level's marginal are one arithmetic read at
    two groupings, and cannot disagree."""
    row_key, col_key = axes
    if row_key == col_key:
        raise ValueError(
            f"{row_key!r} cannot be both axes of a grid — only its diagonal could hold a subject. "
            "Name two different factors."
        )
    keys = sorted({k for lv in levels.values() for k in lv})
    for key in axes:
        if key not in keys:
            raise ValueError(
                f"{key!r} is not a factor of this selection: every subject answers it the same "
                "way, so it is a constant with one column and cannot be an axis. Varying here: "
                f"{', '.join(keys) or 'nothing'}."
            )
    poolable = _poolable(rows, spec)
    by_key = {r.key: r for r in rows}
    grouped: dict[tuple[str, str], list[str]] = {}
    for subject, subject_levels in levels.items():
        grouped.setdefault((subject_levels[row_key], subject_levels[col_key]), []).append(subject)

    cells: list[FactorCell] = []
    for coord in sorted(grouped):
        members = sorted(grouped[coord])
        value, lo, hi, n_cells = _pool(members, by_key, poolable)
        cells.append(
            FactorCell(
                row=coord[0],
                col=coord[1],
                subjects=members,
                value=value,
                ci_lo=lo,
                ci_hi=hi,
                n_cells=n_cells,
            )
        )
    return FactorGridReading(
        row_key=row_key,
        col_key=col_key,
        cells=cells,
        marginalised=[k for k in keys if k not in axes],
        poolable=poolable,
        note=_pool_note(spec, poolable),
    )


__all__ = [
    "FactorCell",
    "FactorGridReading",
    "FactorLevel",
    "FactorReading",
    "factors",
    "grid_reading",
    "levels_by_subject",
]
