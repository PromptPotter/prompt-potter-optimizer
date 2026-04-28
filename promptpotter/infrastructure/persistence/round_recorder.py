"""Per-round action trace recorder — flushes to ``campaigns/{cycle_id}/.cache/rounds/round_NNNN.json``."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import write_json

logger = logging.getLogger(__name__)

__all__ = ["RoundRecorder"]


def _action_to_node_block(action: dict[str, Any]) -> dict[str, Any]:
    """Project an LLM action dict into the ``nodes[*]`` block shape.

    Input: the dict passed to ``add_action`` from ``pipeline.llm_call()``.
    Output: ``{input: {template_fields, variables}, output: {response},
    usage, model, duration_s, timestamp}``. Falls back to ``messages``
    when the caller didn't supply a ``template_fields`` decomposition.
    """
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
    # Fallback path: raw compiled messages when the call site didn't
    # provide a template decomposition.
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


class RoundRecorder:
    """Accumulates node I/O within a round, writes ``round_NNNN.json`` on flush."""

    def __init__(self, rounds_dir: Path) -> None:
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

    def begin_round(self, round_num: int) -> None:
        """Start recording a new round. Discards any pending node data."""
        if self._nodes or self._l1_score:
            logger.warning(
                "RoundRecorder: unflushed state from round %d discarded",
                self._current_round,
            )
        self._current_round = round_num
        self._nodes = {}
        self._l1_score = None
        self._started_at = datetime.now(UTC).isoformat()

    def rehydrate_sticky(self) -> None:
        """Pre-populate ``_sticky_nodes`` from the highest existing round file.

        Called once at startup on a resumed cycle so the dashboard's
        ``current_round.nodes`` shows the prior round's node history
        (l1_generate, l1_critique, …) before the next round writes its
        first block. No-op when the rounds directory is empty / missing
        or when sticky state is already populated.
        """
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
            logger.warning("RoundRecorder: failed to rehydrate from %s: %s", path.name, exc)
            return
        nodes = payload.get("nodes") or {}
        for key, block in nodes.items():
            if key == "l1_score":
                # l1_score is composed by the emitter from per-round candidates;
                # not a sticky-node slot.
                continue
            if isinstance(block, dict):
                self._sticky_nodes[key] = {**block, "round": round_num}

    def add_action(self, action: dict[str, Any]) -> None:
        """Record an LLM node call into the current round.

        Called by ``pipeline.llm_call()`` for every optimizer node. The
        action dict is reshaped into the ``{input, output, usage, ...}``
        block and keyed by node type (``l1_generate``, ``l1_critique``,
        ``l2_context``, ``l3_plan``). Re-entry on the same
        type overwrites — probes or retries naturally shadow the earlier
        call, matching the user's mental model ("what was the last thing
        node X did this round?").
        """
        action.setdefault("timestamp", datetime.now(UTC).isoformat())
        node_type = str(action.get("type") or "llm_call")
        block = _action_to_node_block(action)
        self._nodes[node_type] = block
        self._sticky_nodes[node_type] = {**block, "round": self._current_round}

    def set_node(self, name: str, block: dict[str, Any]) -> None:
        """Deposit a prebuilt node block under ``name``.

        Used when a node's output is available but didn't flow through
        ``pipeline.llm_call()`` — e.g. ``l1_generate`` on a resumed round
        where candidates were loaded from ``.cache/candidates/round_NNNN.json``
        instead of generated by a fresh LLM call.
        """
        self._nodes[name] = block
        self._sticky_nodes[name] = {**block, "round": self._current_round}

    def set_l1_score(self, block: dict[str, Any]) -> None:
        """Deposit the scoring-phase block built by the session emitter."""
        self._l1_score = block

    def snapshot_nodes(self) -> dict[str, dict[str, Any]]:
        """Return a sticky snapshot of phase-keyed node blocks.

        Used by the session emitter to mirror the LLM node I/O into
        ``dashboard.json::current_round``. **Spans rounds**: each slot
        (``l1_generate``, ``l1_critique``, ``l2_context``, …) keeps its
        most-recent fire and is replaced only when the same phase fires
        again — so e.g. round 0's critique stays visible during round 1
        until round 1's critique runs. Each block carries a ``"round"``
        field tagging the round that produced it. The scoring-phase
        block (``l1_score``) is *not* included — the emitter owns that
        data and composes it separately.
        """
        return dict(self._sticky_nodes)

    def flush(self) -> Path | None:
        """Write ``round_NNNN.json`` and reset. Returns the written path.

        Node ordering in the output: ``l1_generate``, ``l1_critique``,
        ``l1_score``, then any remaining (L2, L3, etc.) in insertion
        order. Round *outcome* (accuracy, decision, opt_sp) still lives
        in ``trials/trial_NNNN.json`` via ``_checkpoint_round`` — this
        file is not the place for that data.
        """
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
