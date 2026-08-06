"""Per-cycle round recorder — a fork's MUST point at the fork's own rounds dir, so ``__init__`` rejects a path that does
not end in ``.runtime/cache/rounds``. Pure ledger projection but for ``set_l1_score``, which the dashboard composes."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import LLMCallRecord, PhaseRecord, RoundWarningRecord
from promptpotter.infrastructure.projections.base import DerivedView
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json
from promptpotter.infrastructure.store.layout import CycleLayout, round_basename

logger = logging.getLogger(__name__)

__all__ = [
    "AuditTrailView",
    "audit_rounds_dir",
    "build_node_block",
    "load_round_audits",
    "read_most_recent_round_nodes",
]


_ROUNDS_SUBPATH = (".runtime", "cache", "rounds")


def audit_rounds_dir(cycle_dir: Path) -> Path:
    return CycleLayout(cycle_dir).audit_rounds


def load_round_audits(cycle_dir: Path, round_nums: list[int]) -> list[dict[str, Any] | None]:
    """Load the audit twin of each round in *round_nums*, in order; missing/corrupt → None
    (render degrades gracefully)."""
    layout = CycleLayout(cycle_dir)
    return [read_json_tolerant(layout.audit_round_file(n)) for n in round_nums]


def build_node_block(record: LLMCallRecord) -> dict[str, Any]:
    """Project an ``LLMCallRecord`` into the canonical ``nodes[*]`` shape. ``synthesized`` payloads carry I/O directly (L1
    generate records its parser output with no live call); everything else routes through the per-field projection."""
    if record.payload_kind == "synthesized":
        return {
            "input": dict(record.payload.get("input") or {}),
            "output": dict(record.payload.get("response") or {}),
            "timestamp": record.timestamp,
        }
    return _action_to_node_block({**record.payload, "timestamp": record.timestamp})


def read_most_recent_round_nodes(rounds_dir: Path) -> dict[str, dict[str, Any]]:
    """The latest round file's ``nodes`` block — the live dashboard seeds its sticky LLM-call mirror from this on resume
    without re-issuing the calls. ``l1_score`` is skipped, since the dashboard composes it live."""
    if not rounds_dir.is_dir():
        return {}
    round_re = re.compile(r"^round_(\d+)\.json$")
    candidates: list[tuple[int, Path]] = []
    for path in rounds_dir.iterdir():
        m = round_re.match(path.name)
        if m:
            candidates.append((int(m.group(1)), path))
    if not candidates:
        return {}
    round_num, path = max(candidates, key=lambda c: c[0])
    payload = read_json_tolerant(path, {})
    out: dict[str, dict[str, Any]] = {}
    for key, block in (payload.get("nodes") or {}).items():
        if key == "l1_score":
            continue
        if isinstance(block, dict):
            out[key] = {**block, "round": round_num}
    return out


def _action_to_node_block(action: dict[str, Any]) -> dict[str, Any]:
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

    output_block: dict[str, Any] = {"response": action.get("response")}
    # The model's own thinking, beside the answer it produced. Present only when the
    # provider returned one. Sits on `output` because that is what it is — the model's
    # own account of the response — but it is READ BY HUMANS ONLY (see
    # ``LLMResponse.reasoning``); no projection, gate or scorer consumes this key.
    reasoning = action.get("reasoning")
    if reasoning:
        output_block["reasoning"] = reasoning

    block: dict[str, Any] = {
        "input": input_block,
        "output": output_block,
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
    # Schema-repair count surfaces only when non-zero — keeps the common (clean parse) audit terse.
    repairs = action.get("schema_repair_attempts")
    if repairs:
        block["schema_repair_attempts"] = repairs
    return block


class AuditTrailView(DerivedView):
    """Accumulates node I/O within a round and writes the round file on flush. Both constructors assert the audit path, so
    a fork can never point at the parent tree."""

    def __init__(self, rounds_dir: Path) -> None:
        if rounds_dir.parts[-len(_ROUNDS_SUBPATH) :] != _ROUNDS_SUBPATH:
            raise ValueError(
                f"AuditTrailView rounds_dir must end in {'/'.join(_ROUNDS_SUBPATH)}; "
                f"got {rounds_dir}"
            )
        self.rounds_dir = rounds_dir
        self._current_round: int = 0
        self._nodes: dict[str, dict[str, Any]] = {}
        self._l1_score: dict[str, Any] | None = None
        # Round-scoped degradations (zero candidates, L2 soft-rejects, injection
        # truncations) accumulated for the round_NNNN.json::warnings audit block.
        self._warnings: list[dict[str, Any]] = []
        # Boundary timestamps from `PhaseRecord.timestamp` — no wall-clock observation here.
        self._started_at: str = ""
        self._finished_at: str = ""
        # Set by the runner pre-`drain()` on teardown; threads `"interrupted": True` onto rounds
        # that never received a `round:complete`.
        self._halted_mid_round: bool = False

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> AuditTrailView:
        return cls(CycleLayout(Path(cycle_dir)).audit_rounds)

    def begin_round(self, round_num: int, started_at: str = "") -> None:
        """Start a new round; flush pending state first so L2/L3 calls that arrived between
        `round:complete` and `round:enter` merge into the just-closed round's file (not discarded).
        """
        if self._nodes or self._l1_score or self._warnings:
            self.flush()
        self._current_round = round_num
        self._nodes = {}
        self._l1_score = None
        self._warnings = []
        self._started_at = started_at
        self._finished_at = ""

    def set_l1_score(self, block: dict[str, Any]) -> None:
        """Deposit the scoring-phase block built by the live dashboard projection."""
        self._l1_score = block

    # -- Ledger subscription (PhaseRecord 3) ----------------------------------------

    # One phase, one boundary pair. Origin IS round 0 (`round_0000.json`) and `emit_origin_round`
    # closes it through the same `close_round` seam, so it arrives as `("round", "complete", 0)`.
    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase == "round":
            if record.event == "enter" and record.round is not None:
                self.begin_round(record.round, started_at=record.timestamp)
            elif record.event == "complete":
                self._finished_at = record.timestamp
                self.flush()

    def _handle_llm_call(self, record: LLMCallRecord) -> None:
        """Sole ingress for the optimizer node blocks. The live dashboard subscribes the same record kind and mirrors into
        ``current_round.nodes`` independently."""
        self._nodes[record.node] = build_node_block(record)

    def _handle_round_warning(self, record: RoundWarningRecord) -> None:
        self._warnings.append(
            {
                "kind": record.kind,
                "severity": record.severity,
                "message": record.message,
                "detail": dict(record.detail),
                "timestamp": record.timestamp,
            }
        )

    def flush(self) -> Path | None:
        """Write `round_NNNN.json` and reset. Idempotent — second flush merges new nodes into the
        existing file rather than overwriting (so late L2/L3 records aren't lost).
        """
        if not self._nodes and self._l1_score is None and not self._warnings:
            return None

        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        path = self.rounds_dir / round_basename(self._current_round)

        prior = read_json_tolerant(path, {})
        existing_nodes = dict(prior.get("nodes") or {})
        existing_started_at = prior.get("started_at") or self._started_at
        existing_warnings = list(prior.get("warnings") or [])

        nodes_ordered: dict[str, Any] = {}
        # Reading order: L1 generate/critique → scoring → escalation layers.
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
        merged_warnings = existing_warnings + self._warnings
        if merged_warnings:
            payload["warnings"] = merged_warnings
        if self._halted_mid_round:
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
        self._warnings = []
        return path

    def drain(self) -> None:
        """Runner's teardown seam — flush buffered state since a mid-candidate interrupt never
        emits `round:complete`. `_halted_mid_round` threads `"interrupted": true` on the partial.
        """
        if self._nodes or self._l1_score is not None or self._warnings:
            self.flush()
