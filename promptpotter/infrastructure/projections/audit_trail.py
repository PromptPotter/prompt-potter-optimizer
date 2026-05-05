"""AuditTrailProjection — per-cycle round-recorder, flushes to ``.runtime/cache/rounds/round_NNNN.json``.

Per-cycle scope: a fork's recorder MUST point at the fork's own
``.runtime/cache/rounds/`` directory, never at the parent's. The constructor
takes ``CycleDir`` (not a raw ``Path``) so the routing is explicit at every
construction site, and ``from_cycle_dir`` derives the standard subpath.
A runtime assertion in ``__init__`` rejects any path that doesn't end in
``/.runtime/cache/rounds`` to catch ad-hoc constructions.

Round boundaries arrive via ``on_record``: ``PhaseRecord("round","enter")``
triggers ``begin_round`` and ``PhaseRecord("round","complete")`` triggers
``flush``. Per-node action data still arrives via direct calls
(``add_action`` / ``set_node`` / ``set_l1_score``) from the LLM call
sites and the live dashboard projection — that data isn't replayable
and has no other consumer.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.infrastructure.projections.base import ProjectionBase
from promptpotter.infrastructure.store.base import write_json

logger = logging.getLogger(__name__)

__all__ = ["AuditTrailProjection"]


_ROUNDS_SUBPATH = (".runtime", "cache", "rounds")
_BASELINE_ROUND_SENTINEL = -1


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
    return block


class AuditTrailProjection(ProjectionBase):
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
                f"AuditTrailProjection rounds_dir must end in {'/'.join(_ROUNDS_SUBPATH)}; "
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
        self._started_at: str = ""

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> AuditTrailProjection:
        """Build a projection rooted at ``{cycle_dir}/.runtime/cache/rounds``."""
        return cls(Path(cycle_dir).joinpath(*_ROUNDS_SUBPATH))

    def begin_round(self, round_num: int) -> None:
        """Start recording a new round. Discards any pending node data."""
        if self._nodes or self._l1_score:
            logger.warning(
                "AuditTrailProjection: unflushed state from round %d discarded",
                self._current_round,
            )
        self._current_round = round_num
        self._nodes = {}
        self._l1_score = None
        self._started_at = datetime.now(UTC).isoformat()

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
            logger.warning("AuditTrailProjection: failed to rehydrate from %s: %s", path.name, exc)
            return
        nodes = payload.get("nodes") or {}
        for key, block in nodes.items():
            if key == "l1_score":
                # l1_score is composed by the live dashboard from per-round
                # candidates; not a sticky-node slot.
                continue
            if isinstance(block, dict):
                self._sticky_nodes[key] = {**block, "round": round_num}

    def add_action(self, action: dict[str, Any]) -> None:
        """Record an LLM node call into the current round; same-type re-entry overwrites."""
        action.setdefault("timestamp", datetime.now(UTC).isoformat())
        node_type = str(action.get("type") or "llm_call")
        block = _action_to_node_block(action)
        self._nodes[node_type] = block
        self._sticky_nodes[node_type] = {**block, "round": self._current_round}

    def set_node(self, name: str, block: dict[str, Any]) -> None:
        """Deposit a prebuilt node block — used when output didn't flow through ``llm_call()``."""
        self._nodes[name] = block
        self._sticky_nodes[name] = {**block, "round": self._current_round}

    def set_l1_score(self, block: dict[str, Any]) -> None:
        """Deposit the scoring-phase block built by the live dashboard projection."""
        self._l1_score = block

    def snapshot_nodes(self) -> dict[str, dict[str, Any]]:
        """PhaseRecord-keyed sticky snapshot for ``dashboard.json::current_round`` — slots overwritten only when the same phase re-fires (excludes ``l1_score``, composed by the live dashboard)."""
        return dict(self._sticky_nodes)

    # -- Ledger subscription (PhaseRecord 3) ----------------------------------------

    # Round boundaries (``PhaseRecord("round", "enter"|"complete")``) drive
    # ``begin_round`` and ``flush``. The baseline phase emits its own
    # ``PhaseRecord("baseline", "enter"|"exit")`` pair before the first round; treated
    # as a pseudo-round so its node I/O lands in ``round_baseline.json`` instead
    # of being silently discarded when round 0 begins. DecisionRecord and SnapshotRecord
    # records bypass this projection — decisions are archived in trial JSON and
    # snapshots are display-only.

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == "round":
            if record.event == "enter" and record.round is not None:
                self.begin_round(record.round)
            elif record.event == "complete":
                self.flush()
        elif record.phase == "baseline":
            if record.event == "enter":
                self.begin_round(_BASELINE_ROUND_SENTINEL)
            elif record.event == "exit":
                self.flush()

    def flush(self) -> Path | None:
        """Write ``round_NNNN.json`` and reset. Returns the written path."""
        if not self._nodes and self._l1_score is None:
            return None

        self.rounds_dir.mkdir(parents=True, exist_ok=True)

        nodes_ordered: dict[str, Any] = {}
        # Prefer a predictable reading order: L1 generate/critique first,
        # then scoring, then any escalation layers.
        for preferred in ("l1_generate", "l1_critique"):
            if preferred in self._nodes:
                nodes_ordered[preferred] = self._nodes[preferred]
        if self._l1_score is not None:
            nodes_ordered["l1_score"] = self._l1_score
        for key, block in self._nodes.items():
            if key not in nodes_ordered:
                nodes_ordered[key] = block

        if self._current_round == _BASELINE_ROUND_SENTINEL:
            path = self.rounds_dir / "round_baseline.json"
        else:
            path = self.rounds_dir / f"round_{self._current_round:04d}.json"
        payload: dict[str, Any] = {
            "round": self._current_round,
            "started_at": self._started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "nodes": nodes_ordered,
        }

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
