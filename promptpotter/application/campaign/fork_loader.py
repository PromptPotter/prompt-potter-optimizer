"""Fork-from loader — read events.jsonl and reconstruct a seed OptSearchPoint.

A fork is a pointer into the append-only event stream at
``.promptpotter/projects/{backend_id}/obs/langfuse/events.jsonl``. Every
fork-addressable write point (candidate created, query scored, candidate
scored, round winner chosen, critique written, L2/L3 applied) carries a
self-contained ``state_snapshot`` built from ``OptSearchPoint.model_dump``.

This module is a pure reader. It parses a fork spec, streams the event log
for a source cycle, locates the target event, and returns the snapshot dict
plus the event metadata. The caller rehydrates an ``OptSearchPoint`` from
the snapshot and seeds a new cycle.

See ``docs/architecture/optimization.md`` "Forking a campaign" for the
write-point table and addressing grammar.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ForkAddress", "ForkSeed", "events_log_path", "load_fork_seed", "parse_fork_spec"]


# The event-name vocabulary a fork spec can target. Mirrors the table in
# ``docs/architecture/optimization.md``. "node_end" is accepted for future
# NodeEnd-derived forks (the ``state_snapshot`` field on NodeEnd is
# currently optional and empty).
_WRITE_POINT_NAMES: frozenset[str] = frozenset(
    {
        "candidate_created",
        "query_scored",
        "candidate_scored",
        "round_winner_chosen",
        "critique_written",
        "l2_applied",
        "l3_applied",
        "node_end",
    }
)

# Short aliases accepted in specs. Example: ``1:l1_generate:2`` addresses
# the third candidate of round 1 via its CandidateCreated event.
_SPEC_ALIASES: dict[str, str] = {
    "l1_generate": "candidate_created",
    "l1": "candidate_created",
    "query": "query_scored",
    "candidate": "candidate_scored",
    "winner": "round_winner_chosen",
    "critique": "critique_written",
    "l2": "l2_applied",
    "l3": "l3_applied",
}


@dataclass(frozen=True, slots=True)
class ForkAddress:
    """Parsed fork spec.

    One of ``event_index`` (absolute offset) or ``(round_num, write_point)``
    (human form) is populated. Sub-indexes ``candidate_idx`` / ``query_idx``
    refine the human form.
    """

    raw: str
    event_index: int | None = None
    round_num: int | None = None
    write_point: str | None = None
    candidate_idx: int | None = None
    query_idx: int | None = None


@dataclass(frozen=True, slots=True)
class ForkSeed:
    """Result of resolving a fork spec against a source cycle's event log."""

    source_cycle_id: str
    spec: ForkAddress
    event_name: str
    event_index: int
    state_snapshot: dict[str, Any]
    raw_event: dict[str, Any]


def parse_fork_spec(spec: str) -> ForkAddress:
    """Parse a fork spec into a :class:`ForkAddress`.

    Accepted forms:
      * ``@<event_index>`` — absolute offset into ``events.jsonl`` for the
        source cycle (0-indexed, counting only that cycle's events).
      * ``<round>:<write_point>`` — human form, lands on the last event of
        that kind in the round. Write points accept aliases (``l1_generate``
        → ``candidate_created``, ``l2`` → ``l2_applied``, etc.).
      * ``<round>:<write_point>:<i>`` — ``i``th candidate of that write point.
      * ``<round>:<write_point>:<i>:<j>`` — candidate ``i``, query ``j``
        (only meaningful for ``query_scored``).

    Raises :class:`ValueError` on any syntactic mismatch.
    """
    if not spec or not spec.strip():
        raise ValueError("fork spec is empty")
    raw = spec.strip()

    if raw.startswith("@"):
        try:
            idx = int(raw[1:])
        except ValueError as exc:
            raise ValueError(f"invalid absolute fork spec {raw!r}: expected @<int>") from exc
        if idx < 0:
            raise ValueError(f"invalid absolute fork spec {raw!r}: index must be >= 0")
        return ForkAddress(raw=raw, event_index=idx)

    parts = raw.split(":")
    if len(parts) < 2 or len(parts) > 4:
        raise ValueError(
            f"invalid fork spec {raw!r}: expected <round>:<write_point>[:i[:j]] or @<idx>"
        )

    try:
        round_num = int(parts[0])
    except ValueError as exc:
        raise ValueError(f"invalid fork spec {raw!r}: round must be an integer") from exc
    if round_num < 0:
        raise ValueError(f"invalid fork spec {raw!r}: round must be >= 0")

    wp_alias = parts[1].strip().lower()
    wp = _SPEC_ALIASES.get(wp_alias, wp_alias)
    if wp not in _WRITE_POINT_NAMES:
        valid = sorted(_WRITE_POINT_NAMES | set(_SPEC_ALIASES))
        raise ValueError(
            f"invalid fork spec {raw!r}: unknown write point {parts[1]!r}. valid: {valid}"
        )

    cand_idx: int | None = None
    query_idx: int | None = None
    if len(parts) >= 3:
        try:
            cand_idx = int(parts[2])
        except ValueError as exc:
            raise ValueError(f"invalid fork spec {raw!r}: candidate idx must be int") from exc
    if len(parts) == 4:
        try:
            query_idx = int(parts[3])
        except ValueError as exc:
            raise ValueError(f"invalid fork spec {raw!r}: query idx must be int") from exc
        if wp != "query_scored":
            raise ValueError(
                f"invalid fork spec {raw!r}: query_idx only meaningful for query_scored"
            )

    return ForkAddress(
        raw=raw,
        round_num=round_num,
        write_point=wp,
        candidate_idx=cand_idx,
        query_idx=query_idx,
    )


def events_log_path(project_root: str | Path, backend_id: str) -> Path:
    """Return the events.jsonl path for a backend.

    Mirrors ``FileSink`` construction: ``{project_root}/{backend_id}/obs/
    langfuse/events.jsonl``. Note ``project_root`` here is the store base
    dir (same path passed to ``ObservabilityBridge.from_settings``).
    """
    return Path(project_root) / backend_id / "obs" / "langfuse" / "events.jsonl"


def _iter_cycle_events(log_path: Path, cycle_id: str) -> list[tuple[int, dict[str, Any]]]:
    """Stream events.jsonl and return ``[(cycle_event_index, event_dict), ...]``
    for the given cycle. The cycle event index is a 0-indexed position
    within this cycle's slice of the log.
    """
    if not log_path.exists():
        raise FileNotFoundError(f"events.jsonl not found at {log_path}")

    out: list[tuple[int, dict[str, Any]]] = []
    cycle_idx = 0
    with log_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("fork_loader: skipping malformed events.jsonl line")
                continue
            if ev.get("campaign_id") != cycle_id:
                continue
            out.append((cycle_idx, ev))
            cycle_idx += 1
    return out


def _match_event(
    events: list[tuple[int, dict[str, Any]]],
    spec: ForkAddress,
) -> tuple[int, dict[str, Any]]:
    """Select the target event from the cycle's event list.

    For absolute specs, returns the ``event_index``-th event. For human
    specs, filters by ``round`` + ``event`` name + sub-indexes and returns
    the last match (deterministic, since events are append-only).
    """
    if spec.event_index is not None:
        if spec.event_index >= len(events):
            raise LookupError(
                f"fork spec {spec.raw!r}: event_index {spec.event_index} out of range "
                f"({len(events)} events in cycle)"
            )
        return events[spec.event_index]

    matches: list[tuple[int, dict[str, Any]]] = []
    for idx, ev in events:
        if ev.get("event") != spec.write_point:
            continue
        if ev.get("round") != spec.round_num:
            continue
        if spec.candidate_idx is not None and ev.get("candidate_idx") != spec.candidate_idx:
            continue
        if spec.query_idx is not None and ev.get("query_idx") != spec.query_idx:
            continue
        matches.append((idx, ev))

    if not matches:
        raise LookupError(
            f"fork spec {spec.raw!r}: no matching event in cycle "
            f"(write_point={spec.write_point}, round={spec.round_num}, "
            f"candidate_idx={spec.candidate_idx}, query_idx={spec.query_idx})"
        )
    return matches[-1]


def load_fork_seed(
    project_root: str | Path,
    backend_id: str,
    cycle_id: str,
    spec: ForkAddress | str,
) -> ForkSeed:
    """Resolve a fork spec against an existing cycle's events.jsonl.

    ``spec`` may be a parsed :class:`ForkAddress` or the raw string form.
    Raises :class:`FileNotFoundError` if the log does not exist,
    :class:`LookupError` if no event matches, and :class:`ValueError` if
    the matched event has no ``state_snapshot`` payload (invariant broken).
    """
    if isinstance(spec, str):
        spec = parse_fork_spec(spec)

    log_path = events_log_path(project_root, backend_id)
    events = _iter_cycle_events(log_path, cycle_id)
    if not events:
        raise LookupError(f"fork: no events for cycle {cycle_id!r} in {log_path}")

    event_idx, raw_event = _match_event(events, spec)
    snapshot = raw_event.get("state_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError(
            f"fork spec {spec.raw!r}: matched event {raw_event.get('event')!r} "
            "has no state_snapshot — not a fork-addressable write point"
        )

    return ForkSeed(
        source_cycle_id=cycle_id,
        spec=spec,
        event_name=str(raw_event.get("event", "")),
        event_index=event_idx,
        state_snapshot=snapshot,
        raw_event=raw_event,
    )
