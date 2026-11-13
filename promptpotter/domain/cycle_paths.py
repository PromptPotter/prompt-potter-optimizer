"""A cycle's address is a ``CyclePath``, never an id: ids repeat across sibling ``.inner`` sandboxes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, NewType, get_args

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import BadRequestError

CycleDir = NewType("CycleDir", Path)
WorkspaceDir = NewType("WorkspaceDir", Path)

# One author for both languages: `scripts/build_ts_types.py::_emit_cycle_path_grammar` emits all three.
HOP_SEP = "~"
UNIT_SEP = "::"
# Fullmatched, case-folded; no escapes, so the browser's is byte-identical. A trailing dot (so every
# all-dots traversal segment) and a Windows device name each open another file than the one spelled.
ID_COMPONENT_PATTERN = (
    r"(?!(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?![a-zA-Z0-9_-]))[a-zA-Z0-9_.-]*[a-zA-Z0-9_-]"
)

ID_COMPONENT_RE = re.compile(ID_COMPONENT_PATTERN, re.IGNORECASE)

# Admit `~` or `:` into the charset and a deep path splits into a DIFFERENT well-formed address.
if any(ID_COMPONENT_RE.fullmatch(ch) for ch in HOP_SEP + UNIT_SEP):
    raise RuntimeError(
        f"cycle-path grammar is not round-trippable: a separator in {HOP_SEP + UNIT_SEP!r} "
        f"matches the id charset {ID_COMPONENT_PATTERN!r}, so encode/decode would silently "
        "resolve one address as another."
    )


class CycleHop(FrozenModel):
    """One ``(campaign, cycle)`` step of a cycle path; neither id names a cycle alone."""

    campaign_id: str
    cycle_id: str


CyclePath = tuple[CycleHop, ...]


@dataclass(frozen=True, slots=True)
class Cut:
    """``offset`` indexes the cycle's OWN records, never an inherited prefix; ``None`` is the head."""

    cycle: CycleDir
    hop: CycleHop
    offset: int | None = None


def encode_cycle_path(path: CyclePath) -> str:
    """The SAME codec as the wire's ``descend`` tail and the webapp's encoder."""
    return HOP_SEP.join(f"{hop.campaign_id}{UNIT_SEP}{hop.cycle_id}" for hop in path)


def decode_cycle_path(encoded: str | None) -> CyclePath:
    """Absent decodes to ``()`` (no hops below the root); component VALIDATION is ``descend_store``'s."""
    if not encoded:
        return ()
    hops: list[CycleHop] = []
    for seg in encoded.split(HOP_SEP):
        campaign, sep, cycle = seg.partition(UNIT_SEP)
        if not sep or not campaign or not cycle:
            raise BadRequestError(
                f"Malformed cycle-path hop: {seg!r} (expected 'campaign{UNIT_SEP}cycle')."
            )
        hops.append(CycleHop(campaign_id=campaign, cycle_id=cycle))
    return tuple(hops)


# A subject's mask: the lens it is graded under and the samples it is restricted to.
LensKind = Literal["score", "dials", "abort"]
_LENS_KINDS: dict[str, LensKind] = {kind: kind for kind in get_args(LensKind)}


class Lens(NamedTuple):
    kind: LensKind
    body: str

    @property
    def spelling(self) -> str:
        return f"{self.kind}:{self.body}"


def parse_lens(value: str, *, allow_abort: bool) -> Lens:
    """A comparable LEVEL refuses ``abort:``: a gate switched off changes which candidates ran, not a score."""
    name, sep, body = value.partition(":")
    kind = _LENS_KINDS.get(name) if sep else None
    if kind is None or (kind == "abort" and not allow_abort):
        expected = (
            "'score:<formula>', 'dials:<term=weight,…>' or 'abort:<variant>'"
            if allow_abort
            else "'score:<formula>' or 'dials:<term=weight,…>'; an abort lens is a lineage-tree "
            "question, not a comparable level"
        )
        raise BadRequestError(f"Unknown lens: {value!r} (expected {expected})")
    return Lens(kind, body)


def parse_sample_ids(text: str | None) -> frozenset[int] | None:
    if not text or not text.strip():
        return None
    try:
        ids = frozenset(int(tok) for tok in text.split(",") if tok.strip())
    except ValueError as exc:
        raise BadRequestError(f"Invalid samples list: {text!r} ({exc})") from exc
    return ids or None


SubjectKind = Literal["campaign", "course", "candidate"]
# How many `/`-joined ids each kind's evidence spelling takes.
SUBJECT_ARITY: dict[SubjectKind, int] = {"campaign": 1, "course": 2, "candidate": 3}

_POOL_SEGMENTS = frozenset({"in"})
_SUBJECT_SEGMENTS = _POOL_SEGMENTS | {"lens", "samples"}


def _segments(
    spec: str, segments: list[str], *, allowed: frozenset[str]
) -> tuple[CyclePath, Lens | None, frozenset[int] | None]:
    """The ``;name=value`` segments after an address: its sandbox chain, then its mask."""
    inside: CyclePath = ()
    lens: Lens | None = None
    samples: frozenset[int] | None = None
    for segment in segments:
        name, _, value = segment.partition("=")
        if name not in allowed:
            raise BadRequestError(
                f"Unknown segment {segment!r} on {spec!r} "
                f"(expected {', '.join(f'{a}=' for a in sorted(allowed))})."
            )
        if name == "in":
            inside = decode_cycle_path(value)
        elif name == "lens":
            lens = parse_lens(value, allow_abort=False)
        else:
            samples = parse_sample_ids(value)
    return inside, lens, samples


def parse_pool(spec: str) -> tuple[str, CyclePath]:
    """``<name>[;in=<c::y~…>]``: a dataset or campaign name, in the sandbox chain it is read in."""
    name, *segments = spec.split(";")
    if not name:
        raise BadRequestError(f"Address {spec!r} names nothing before its segments.")
    return name, _segments(spec, segments, allowed=_POOL_SEGMENTS)[0]


class Subject(NamedTuple):
    """One address of the record: a campaign, or a course and what in it (a round, a candidate,
    a line of its ledger, ``at``), in the L4 sandbox chain it is read in (``inside``), under the
    mask it is read through (``lens``, ``samples``).

    Every wire spelling of an address is one of its encodings, each read back here: the evidence
    subject string (`parse` / `key`), the hierarchy address (`parse_address` / `address`) and a
    per-cycle read's or command's ``descend`` tail (`below` / `descend`).
    """

    campaign_id: str
    cycle_id: str = ""
    candidate_id: str = ""
    inside: CyclePath = ()
    lens: Lens | None = None
    samples: frozenset[int] | None = None
    round: int | None = None
    at: int | None = None

    @classmethod
    def of(cls, path: CyclePath) -> Subject:
        """The course at the end of *path*, in the chain above it."""
        *inside, hop = path
        return cls(hop.campaign_id, hop.cycle_id, inside=tuple(inside))

    @classmethod
    def below(cls, campaign_id: str, cycle_id: str, descend: str | None) -> Subject:
        """A per-cycle address: the root hop, then the ``descend`` tail of hops below it."""
        root = CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
        return cls.of((root, *decode_cycle_path(descend)))

    @property
    def kind(self) -> SubjectKind:
        if self.candidate_id:
            return "candidate"
        return "course" if self.cycle_id else "campaign"

    @property
    def path(self) -> CyclePath:
        """The chain down to the course; a campaign subject names no cycle, so it has none."""
        if not self.cycle_id:
            raise BadRequestError(
                f"Campaign {self.campaign_id!r} names no cycle; address a course."
            )
        return (*self.inside, CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id))

    @property
    def descend(self) -> str | None:
        """The ``descend`` tail: every hop below the root's, unset where there is none."""
        return encode_cycle_path(self.path[1:]) or None

    @property
    def command_fields(self) -> dict[str, str]:
        """A cycle command's address fields: the root hop and the tail below it."""
        root = self.path[0]
        fields = {"campaign_id": root.campaign_id, "cycle_id": root.cycle_id}
        if (descend := self.descend) is not None:
            fields["descend"] = descend
        return fields

    @classmethod
    def parse(cls, spec: str) -> Subject:
        """``kind:<campaign>[/<cycle>[/<candidate>]][;in=<c::y~…>][;lens=score:…|dials:…][;samples=1,2,3]``."""
        # `;` cannot appear in a safe-AST formula, so a lens needs no escaping.
        address, *segments = spec.split(";")
        kind, sep, rest = address.partition(":")
        arity = SUBJECT_ARITY.get(kind)  # type: ignore[call-overload]
        if not sep or arity is None:
            raise BadRequestError(
                f"Unknown subject kind in {spec!r} (expected one of {sorted(SUBJECT_ARITY)}, "
                "as `kind:<campaign>[/<cycle>[/<candidate>]]`)."
            )
        parts = rest.split("/")
        if len(parts) != arity or not all(parts):
            raise BadRequestError(
                f"Subject {spec!r} addresses {len([p for p in parts if p])} id(s); a "
                f"{kind!r} subject takes exactly {arity}."
            )
        inside, lens, samples = _segments(spec, segments, allowed=_SUBJECT_SEGMENTS)
        if lens and kind != "course":
            raise BadRequestError(
                f"A {kind!r} subject takes no lens: an alternative formula re-decides ELECTIONS, "
                "and only a course has any. Address the branch instead."
            )
        ids = [*parts, "", ""]
        return cls(ids[0], ids[1], ids[2], inside=inside, lens=lens, samples=samples)

    @property
    def key(self) -> str:
        """Its evidence spelling, which `parse` reads back."""
        ids = [p for p in (self.campaign_id, self.cycle_id, self.candidate_id) if p]
        segments = [f"in={encode_cycle_path(self.inside)}"] if self.inside else []
        if self.lens:
            segments.append(f"lens={self.lens.spelling}")
        if self.samples:
            segments.append("samples=" + ",".join(str(s) for s in sorted(self.samples)))
        return ";".join([f"{self.kind}:{'/'.join(ids)}", *segments])

    @classmethod
    def parse_address(cls, address: str) -> Subject:
        """A hierarchy address: ``[<chain>~]<campaign>``, or a course's cycle path then
        ``/r<round>[/<candidate>]`` or ``/@<offset>``."""
        head, slash, rest = address.partition("/")
        *above, last = head.split(HOP_SEP)
        inside = decode_cycle_path(HOP_SEP.join(above))
        campaign_id, sep, cycle_id = last.partition(UNIT_SEP)
        if not campaign_id or (sep and not cycle_id) or (slash and not sep):
            raise BadRequestError(f"Malformed hierarchy address: {address!r}.")
        subject = cls(campaign_id, cycle_id, inside=inside)
        if not slash:
            return subject
        step, _, candidate = rest.partition("/")
        try:
            if step.startswith("@") and not candidate:
                return subject._replace(at=int(step[1:]))
            if step.startswith("r"):
                return subject._replace(round=int(step[1:]), candidate_id=candidate)
        except ValueError:
            pass
        raise BadRequestError(f"Malformed hierarchy address: {address!r}.")

    @property
    def address(self) -> str:
        """Its hierarchy address, which `parse_address` reads back."""
        if not self.cycle_id:
            above = (encode_cycle_path(self.inside),) if self.inside else ()
            return HOP_SEP.join((*above, self.campaign_id))
        course = encode_cycle_path(self.path)
        if self.at is not None:
            return f"{course}/@{self.at}"
        if self.round is None:
            if self.candidate_id:
                raise ValueError(f"candidate {self.candidate_id!r} names no round to address")
            return course
        stepped = f"{course}/r{self.round}"
        return f"{stepped}/{self.candidate_id}" if self.candidate_id else stepped
