from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import Field

from promptpotter.application.mask.record import Lens, parse_lens, parse_sample_ids
from promptpotter.domain.cycle_paths import (
    CycleHop,
    CyclePath,
    decode_cycle_path,
    encode_cycle_path,
)
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.run_records import OPERATOR_ORIGIN_SOURCES, CandidateState
from promptpotter.domain.strict_model import StrictModel

SubjectKind = Literal["campaign", "course", "candidate"]

_SUBJECT_ARITY: dict[SubjectKind, int] = {"campaign": 1, "course": 2, "candidate": 3}
SUBJECT_KIND_LABELS: dict[SubjectKind, str] = {
    "campaign": "origin",
    "course": "branch head",
    "candidate": "searchpoint",
}
assert SUBJECT_KIND_LABELS.keys() == _SUBJECT_ARITY.keys()


class SubjectSpec(NamedTuple):
    kind: SubjectKind
    campaign_id: str
    cycle_id: str = ""
    candidate_id: str = ""
    inside: CyclePath = ()
    lens: Lens | None = None
    samples: frozenset[int] | None = None

    @property
    def key(self) -> str:
        addressed = [p for p in (self.campaign_id, self.cycle_id, self.candidate_id) if p]
        parts = [f"{self.kind}:{'/'.join(addressed)}"]
        if self.inside:
            parts.append(f"in={encode_cycle_path(self.inside)}")
        if self.lens:
            parts.append(f"lens={self.lens.spelling}")
        if self.samples:
            parts.append("samples=" + ",".join(str(s) for s in sorted(self.samples)))
        return ";".join(parts)


def parse_subject(spec: str) -> SubjectSpec:
    """``kind:<campaign>[/<cycle>[/<candidate>]][;in=<c::y~…>][;lens=score:…|dials:…][;samples=1,2,3]``."""
    # `;` cannot appear in a safe-AST formula, so a lens needs no escaping.
    address, *segments = spec.split(";")
    kind, sep, rest = address.partition(":")
    if not sep or kind not in _SUBJECT_ARITY:
        raise ValueError(
            f"Unknown subject kind in {spec!r} (expected one of {sorted(_SUBJECT_ARITY)}, "
            "as `kind:<campaign>[/<cycle>[/<candidate>]]`)."
        )
    parts = rest.split("/")
    arity = _SUBJECT_ARITY[kind]
    if len(parts) != arity or not all(parts):
        raise ValueError(
            f"Subject {spec!r} addresses {len([p for p in parts if p])} id(s); a "
            f"{kind!r} subject takes exactly {arity}."
        )
    lens: Lens | None = None
    samples, inside = None, CyclePath()
    for segment in segments:
        name, _, value = segment.partition("=")
        if name == "in":
            inside = decode_cycle_path(value)
        elif name == "lens":
            lens = parse_lens(value, allow_abort=False)
        elif name == "samples":
            samples = parse_sample_ids(value)
        else:
            raise ValueError(
                f"Unknown subject segment {segment!r} on {spec!r} "
                "(expected 'in=', 'lens=' or 'samples=')."
            )
    if lens and kind != "course":
        raise ValueError(
            f"A {kind!r} subject takes no lens: an alternative formula re-decides ELECTIONS, and "
            "only a course has any. Address the branch instead."
        )
    ids = [*parts, "", ""]
    return SubjectSpec(kind, ids[0], ids[1], ids[2], inside=inside, lens=lens, samples=samples)


def authorship_of(source: str, issued_by: str) -> str:
    if source not in OPERATOR_ORIGIN_SOURCES:
        return source
    return f"operator:{issued_by}"


class SubjectMask(StrictModel):
    """The mask this channel is read under, echoed back. Served rather than left implicit in the
    key, so a chart legend can say what a channel IS without re-splitting an address."""

    lens: str | None
    samples: list[int] | None


class ScenarioReading(StrictModel):
    """What the mask did to this branch: how far it agrees with the record, and the round it stops.

    The chain ENDS where the two readings part (`mask/scenario.py`), so the pair of winners below is
    one round's disagreement — both read at ``first_divergent_round``, or at the branch's last round
    where there is none. Reading a mid-chain counterfactual against the branch's final crown instead
    would compare two different rounds and report the gap between them as a change.

    ``note`` carries the caveat as a SERVED FACT, the way ``Comparability.note`` does — because the
    honest limit of a lens is not something a surface can be trusted to remember.
    """

    recorded_winner_id: str | None
    scenario_winner_id: str | None
    winner_changed: bool
    first_divergent_round: int | None
    invariant_rounds: int
    total_rounds: int
    n_samples_scored: int
    note: str


class WinnerChainPoint(StrictModel):
    """One step of the branch standing behind a subject — the winner chain from the origin up to
    its head, each point read on ITS OWN cells under the selected metric. Opt-in
    (``include_winner_chain``), because every point past the origin opens a round file.

    **Named for the chain, never "trajectory".** The subsets move between rounds, so this reads
    each point on the evidence that point actually had; the round's own `overlap` line is the
    OPPOSITE basis — that same chain on one shared set of cells. Two readings of one sequence
    that disagree by construction, and under one word a reader could tell them apart from
    neither name. The word survives where a series genuinely is one (`p_best_trajectory`,
    `parent_level_trajectory`, the Sample-trajectory grid)."""

    candidate_id: str
    round: int
    label: str
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int


class SubjectReading(StrictModel):
    """One subject, read under the selected metric — its identity, its per-cell values and the one
    estimate they merge to. ONE row, because a roster row and a metric reading that live in separate
    lists can disagree about the same subject, and under the default metric they held the same
    number reached two ways.

    ``values`` is keyed by the cell's QUERY, the identity that survives across campaigns; a cell the
    metric cannot read is ABSENT from it and named in ``unscorable_cells`` rather than scored — the
    two absences are different facts and a surface renders them as different glyphs.
    ``ci_lo``/``ci_hi`` are ``None`` below two scored cells — one reading has no spread, and a
    bracket drawn from it is a fiction.
    """

    key: str
    kind: SubjectKind
    inside: list[CycleHop]
    campaign_id: str
    cycle_id: str
    candidate_id: str
    label: str
    dataset_name: str
    created_at: str
    # `None` is UNKNOWN (an unstamped ruler): never render it as `True`.
    comparable: bool | None
    comparable_note: str
    mask: SubjectMask | None
    scenario: ScenarioReading | None
    winner_chain: list[WinnerChainPoint] | None
    config: dict[str, str] | None
    # `None` where round 0 carries no hashes: an unknown arm groups with nothing.
    arm_id: str | None
    # `""` where the ledger names no source for the point, which groups with nothing.
    authorship: str
    human_intervened: bool
    status: CandidateState = Field(
        description="Where the resolved searchpoint stands, in the lineage tree's own words. "
        "`minted` = it has no score report yet: its walk is still open (or ended with its "
        "producer), so `n_cells`, `values` and `cell_means` cover only the cells it has scored SO "
        "FAR and every reading here is a prefix, of `expected_samples`. `measured` = its walk "
        "ended and these are all its cells.",
    )
    expected_samples: int | None = Field(
        description="How many cells the point's walk was sized for — the denominator `n_cells` is "
        "read against. The score report's own once it lands; before that, what the walk last "
        "announced, which a racing arm raises block by block. Null until its first cell lands.",
    )
    # `None` where the point carries no score report — never 0, which means "it earned every cell".
    cached_samples: int | None
    instrument_id: str
    ability: AbilityReading | None
    round: int
    cycle_spend_usd: float | None
    cycle_rounds_scored: int
    spend_to_round: dict[str, float]
    # A channel none of its cells carries is absent, never 0.
    cell_means: dict[str, float]
    values: dict[str, float]
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int
    unscorable_cells: list[str]
    levels: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "SUBJECT_KIND_LABELS",
    "ScenarioReading",
    "SubjectKind",
    "SubjectMask",
    "SubjectReading",
    "SubjectSpec",
    "WinnerChainPoint",
    "authorship_of",
    "parse_subject",
]
