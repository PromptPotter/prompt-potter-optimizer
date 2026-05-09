"""SignalsProjection — appends escalation rule firings to ``.runtime/signals.jsonl``.

Sister to :class:`PoBBStreamView`. Subscribed to the same
``CycleEventLog`` as the other projections; filters on ``PhaseRecord.phase
== "cadence" AND event == "rule_fired"`` and writes one JSONL line per
fire under the cycle's ``.runtime/`` umbrella.

This is the operator-side surface for "why is the loop in the state it's
in." The webapp's ``StuckDiagnosis`` widget reads the same firings
(mirrored as ``dashboard.json::recent_rules`` by
:class:`LiveDashboardView`); the on-disk JSONL keeps the full
trail past the dashboard's rolling window for post-hoc trajectory
plots.

Per-cycle scope: each fork's projection points at its own ``.runtime/``
directory under the fork's audit tree (parallel to ``.runtime/streams/``
and ``.runtime/cache/rounds/``). A runtime check rejects misrouted
construction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import PhaseRecord
from promptpotter.infrastructure.projections.base import DerivedView

logger = logging.getLogger(__name__)

__all__ = ["SignalsProjection"]

_RUNTIME_SUBDIR = ".runtime"
_SIGNALS_FILENAME = "signals.jsonl"


class SignalsProjection(DerivedView):
    """Appends escalation rule firings to ``.runtime/signals.jsonl``.

    Each line is a JSON object with ``round``, ``timestamp``, ``layer``
    (``post_round``), ``rule_name``, ``rule_priority``, ``next_action``,
    ``reason``, and the ``signal_inputs`` snapshot the rule consulted.
    The file is per-cycle; forks open their own under the fork's audit dir.
    """

    def __init__(self, runtime_dir: Path) -> None:
        if runtime_dir.name != _RUNTIME_SUBDIR:
            raise ValueError(
                f"SignalsProjection runtime_dir must be a /{_RUNTIME_SUBDIR} dir; got {runtime_dir}"
            )
        self.runtime_dir = runtime_dir
        self.signals_path = runtime_dir / _SIGNALS_FILENAME

    @classmethod
    def from_cycle_dir(cls, cycle_dir: CycleDir) -> SignalsProjection:
        """Build a projection rooted at ``{cycle_dir}/.runtime``."""
        return cls(Path(cycle_dir) / _RUNTIME_SUBDIR)

    def _handle_phase(self, record: PhaseRecord) -> None:
        if record.phase != "escalation" or record.event != "rule_fired":
            return
        payload: dict[str, Any] = dict(record.payload or {})
        line = {
            "round": record.round,
            "timestamp": record.timestamp,
            "layer": payload.get("layer"),
            "rule_name": payload.get("rule_name"),
            "rule_priority": payload.get("rule_priority"),
            "next_action": payload.get("next_action"),
            "reason": payload.get("reason"),
            "signal_inputs": payload.get("signal_inputs") or {},
        }
        self._append(line)

    def _append(self, line: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self.signals_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, default=str) + "\n")
        except OSError as exc:
            logger.warning("SignalsProjection: append failed: %s", exc)
