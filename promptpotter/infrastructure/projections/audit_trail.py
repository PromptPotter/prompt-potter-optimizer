"""AuditTrailView — per-cycle round-recorder, flushes to ``.runtime/cache/rounds/round_NNNN.json``.

Per-cycle scope: a fork's recorder MUST point at the fork's own
``.runtime/cache/rounds/`` directory, never at the parent's. The constructor
takes ``CycleDir`` (not a raw ``Path``) so the routing is explicit at every
construction site, and ``from_cycle_dir`` derives the standard subpath.
A runtime assertion in ``__init__`` rejects any path that doesn't end in
``/.runtime/cache/rounds`` to catch ad-hoc constructions.

Pure derived view of the ledger: round boundaries arrive via
``PhaseRecord("round","enter"|"complete")``; per-node LLM I/O arrives
via ``LLMCallRecord`` (single-writer invariant — the four optimizer
LLM calls go through ``llm_call.py::run_optimizer_node`` which
appends one record per call). The only direct in-process method is
``set_l1_score`` from the live dashboard, which composes scoring
phase data this projection wouldn't otherwise see.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import LLMCallRecord, PhaseRecord
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.store.base import write_json

logger = logging.getLogger(__name__)

__all__ = ["AuditTrailView", "audit_rounds_dir", "load_round_audits"]


_ROUNDS_SUBPATH = (".runtime", "cache", "rounds")


def audit_rounds_dir(cycle_dir: Path) -> Path:
    """``{cycle_dir}/.runtime/cache/rounds`` — per-round audit folder."""
    return cycle_dir.joinpath(*_ROUNDS_SUBPATH)


def load_round_audits(cycle_dir: Path, rounds: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """Load ``round_NNNN.json`` for each round; ``None`` on missing/corrupt.

    Output is parallel to *rounds* — slot ``N`` is the audit dict (or ``None``)
    for ``rounds[N]``. Corrupt JSON or absent file is non-fatal: the matching
    slot is ``None`` and the operator-facing render degrades gracefully.
    """
    rd = audit_rounds_dir(cycle_dir)
    out: list[dict[str, Any] | None] = []
    for round_data in rounds:
        round_num = int(round_data.get("round") or 0)
        path = rd / f"round_{round_num:04d}.json"
        if path.is_file():
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
                continue
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("audit load failed: %s — %s", path.name, exc)
        out.append(None)
    return out


def _action_to_node_block(action: dict[str, Any]) -> dict[str, Any]:
    """Project an LLM action dict into the ``nodes[*]`` block shape."""
    input_block: dict[str, Any] = {}
    template_fields = action.get("template_fields")
    if template_fields is not None:
        input_block["template_fields"] = template_fields
    variables = action.get("variables")
    if variables is not None:
        input_block["variables"] = variables
    template_name = action.get("template_name")
    if template_name is not None:
        input_block["template_name"] = template_name
    if not input_block and "messages" in action:
        input_block["messages"] = action["messages"]

    block: dict[str, Any] = {
        "input": input_block,
        "output": {"response": action.get("response")},
    }
    if "usage" in action:
        block["usage"] = action["usage"]
    if "model" in action:
        block["model"] = action["model"]
    if "config" in action:
        block["config"] = action["config"]
    if "duration_s" in action:
        block["duration_s"] = action["duration_s"]
    if "timestamp" in action:
        block["timestamp"] = action["timestamp"]
    # Schema-repair retry count: only surface when non-zero so the audit
    # stays terse for the common (clean parse) case but the signal is
    # immediately visible when the L1 prompt produced bad JSON.
    repairs = action.get("schema_repair_attempts")
    if repairs:
        block["schema_repair_attempts"] = repairs
    return block


class AuditTrailView(DerivedView):
    """Accumulates node I/O within a round, writes ``round_NNNN.json`` on flush.

    Construct via :meth:`from_cycle_dir` so the ``rounds_dir`` is derived
    from the cycle dir's ``.runtime/cache/rounds`` subpath in one place. The
    direct ``__init__`` is supported for tests that already hold a
    rounds-dir path; both code paths assert the path lives under
    ``.runtime/cache/rounds`` so a fork can never accidentally point at the
    parent's tree.
    """

    def __init__(self, rounds_dir: Path) -> None:
        if rounds_dir.parts[-len(_ROUNDS_SUBPATH) :] != _ROUNDS_SUBPATH:
            raise ValueError(
                f"AuditTrailView rounds_dir must end in {'/'.join(_ROUNDS_SUBPATH)}; "
                f"got {rounds_dir}"
            )
        self.rounds_dir = rounds_dir
        self._current_round: int = 0
        self._nodes: dict[str, dict[str, Any]] = {}
        # Sticky mirror for the dashboard: each phase-keyed slot keeps its
        # most-recent fire across round boundaries. Per-key overwrite only;
        # never wiped by begin_round / flush. Each block carries a
        # ``"round"`` tag so the reader can tell which round produced it.
        self._sticky_nodes: dict[str, dict[str, Any]] = {}
        self._l1_score: dict[str, Any] | None = None
        # Round-boundary timestamps sourced from PhaseRecord.timestamp on
        # the round-enter / round-complete events — pure derived view, no
        # wall-clock observation in this projection.
        self._started_at: str = ""
        self._finished_at: str = ""
        # Set by the runner before ``drain()`` when the cycle is being torn
        # down on Ctrl+C; threads ``"interrupted": True`` onto the payload of
        # the round that never received a ``round:complete``.
        self._cycle_was_interrupted: bool = False

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> AuditTrailView:
        """Build a projection rooted at ``{cycle_dir}/.runtime/cache/rounds``."""
        return cls(Path(cycle_dir).joinpath(*_ROUNDS_SUBPATH))

    def begin_round(self, round_num: int, started_at: str = "") -> None:
        """Start recording a new round. Flushes any pending state from
        the previous round before resetting.

        L2 / L3 LLM calls that arrive AFTER ``round:complete`` and BEFORE
        the next ``round:enter`` land in the previous round's ``_nodes``
        buffer (the projection has no boundary to attach them to except
        the active round). Flushing here means those records merge into
        the just-closed round's ``round_NNNN.json`` rather than being
        discarded.

        ``started_at`` should be the ``PhaseRecord.timestamp`` of the
        round-enter event. The headless fallback (no ledger) leaves
        the field empty; downstream readers tolerate that.
        """
        if self._nodes or self._l1_score:
            self.flush()
        self._current_round = round_num
        self._nodes = {}
        self._l1_score = None
        self._started_at = started_at
        self._finished_at = ""

    def rehydrate_sticky(self) -> None:
        """Pre-populate ``_sticky_nodes`` from the highest existing round file so resumed-cycle dashboards show prior history before the first new write."""
        if self._sticky_nodes:
            return
        if not self.rounds_dir.is_dir():
            return
        round_re = re.compile(r"^round_(\d+)\.json$")
        candidates = []
        for path in self.rounds_dir.iterdir():
            m = round_re.match(path.name)
            if m:
                candidates.append((int(m.group(1)), path))
        if not candidates:
            return
        round_num, path = max(candidates, key=lambda c: c[0])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("AuditTrailView: failed to rehydrate from %s: %s", path.name, exc)
            return
        nodes = payload.get("nodes") or {}
        for key, block in nodes.items():
            if key == "l1_score":
                # l1_score is composed by the live dashboard from per-round
                # candidates; not a sticky-node slot.
                continue
            if isinstance(block, dict):
                self._sticky_nodes[key] = {**block, "round": round_num}

    def set_l1_score(self, block: dict[str, Any]) -> None:
        """Deposit the scoring-phase block built by the live dashboard projection."""
        self._l1_score = block

    def _record_node(self, node_type: str, block: dict[str, Any]) -> None:
        """Internal — store a node block keyed by phase, mirror to sticky cache."""
        self._nodes[node_type] = block
        self._sticky_nodes[node_type] = {**block, "round": self._current_round}

    def snapshot_nodes(self) -> dict[str, dict[str, Any]]:
        """PhaseRecord-keyed sticky snapshot for ``dashboard.json::current_round`` — slots overwritten only when the same phase re-fires (excludes ``l1_score``, composed by the live dashboard)."""
        return dict(self._sticky_nodes)

    # -- Ledger subscription (PhaseRecord 3) ----------------------------------------

    # Round boundaries arrive as ``PhaseRecord("round", "enter"|"complete")``;
    # origin emits ``PhaseRecord("origin", "enter"|"exit", round=0)`` which is
    # handled by the same boundary logic — origin IS round 0 on disk, flushed
    # to ``round_0000.json`` alongside the L1 rounds ``round_0001.json``+.
    # ResumeCheckpointRecord and SnapshotRecord records bypass this projection
    # — decisions are archived in round_data JSON and snapshots are display-only.

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase in ("round", "origin"):
            if record.event == "enter" and record.round is not None:
                self.begin_round(record.round, started_at=record.timestamp)
            elif record.event in ("complete", "exit"):
                self._finished_at = record.timestamp
                self.flush()

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        """Project an :class:`LLMCallRecord` payload into the current round's nodes block.

        Sole ingress for ``nodes.l1_generate`` / ``.l1_critique`` /
        ``.l2_context`` / ``.l3_plan`` (and any synthesized
        load-from-disk variants). For ``payload_kind == "llm_call"`` the
        payload mirrors today's action-dict shape, so the projection
        logic stays ``_action_to_node_block``. For
        ``payload_kind == "synthesized"`` the payload already carries
        ``input`` / ``output`` keys directly — pass through.
        """
        if record.payload_kind == "synthesized":
            block: dict[str, Any] = {
                "input": dict(record.payload.get("input") or {}),
                "output": dict(
                    record.payload.get("response") or record.payload.get("output") or {}
                ),
                "timestamp": record.timestamp,
            }
        else:
            block = _action_to_node_block({**record.payload, "timestamp": record.timestamp})
        self._record_node(record.node, block)

    def flush(self) -> Path | None:
        """Write ``round_NNNN.json`` and reset. Returns the written path.

        Idempotent: a second flush on the same round (e.g. L2 LLM-call
        records that arrived between ``round:complete`` and the next
        ``round:enter``) merges new nodes into the existing file's
        ``nodes`` block rather than overwriting it.
        """
        if not self._nodes and self._l1_score is None:
            return None

        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        path = self.rounds_dir / f"round_{self._current_round:04d}.json"

        existing_nodes: dict[str, Any] = {}
        existing_started_at = self._started_at
        if path.exists():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
                existing_nodes = dict(prior.get("nodes") or {})
                existing_started_at = prior.get("started_at") or existing_started_at
            except (OSError, json.JSONDecodeError):
                pass

        nodes_ordered: dict[str, Any] = {}
        # Prefer a predictable reading order: L1 generate/critique first,
        # then scoring, then any escalation layers.
        for preferred in ("l1_generate", "l1_critique"):
            if preferred in existing_nodes:
                nodes_ordered[preferred] = existing_nodes[preferred]
            if preferred in self._nodes:
                nodes_ordered[preferred] = self._nodes[preferred]
        if "l1_score" in existing_nodes:
            nodes_ordered["l1_score"] = existing_nodes["l1_score"]
        if self._l1_score is not None:
            nodes_ordered["l1_score"] = self._l1_score
        for source in (existing_nodes, self._nodes):
            for key, block in source.items():
                if key not in nodes_ordered:
                    nodes_ordered[key] = block

        payload: dict[str, Any] = {
            "round": self._current_round,
            "started_at": existing_started_at,
            "finished_at": self._finished_at,
            "nodes": nodes_ordered,
        }
        if self._cycle_was_interrupted:
            payload["interrupted"] = True

        write_json(path, payload, default=str)
        logger.debug(
            "Round %d recorded: %d nodes → %s",
            self._current_round,
            len(nodes_ordered),
            path.name,
        )

        self._nodes = {}
        self._l1_score = None
        return path

    def drain(self) -> None:
        """Flush any buffered round state to disk on cycle teardown.

        The normal flush path fires on ``PhaseRecord("round", "complete"|"exit")``;
        a mid-candidate interrupt never emits ``complete``, so the buffered
        nodes would otherwise be lost on the runner returning. ``drain`` is
        the runner's seam for "the cycle is over — settle to disk now."
        When ``_cycle_was_interrupted`` is true (set externally), the partial
        ``round_NNNN.json`` carries ``"interrupted": true`` at top level.
        """
        if self._nodes or self._l1_score is not None:
            self.flush()
