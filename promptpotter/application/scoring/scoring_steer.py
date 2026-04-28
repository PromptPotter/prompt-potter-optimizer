"""Interactive composite-score steering — between-round formula hot-swap.

The optimizer compiles ``campaign.json::scoring`` once at startup. Long
runs sometimes reveal that the formula's weights are wrong — e.g. the
operator wants a stronger verbosity penalty, or to flip from accuracy-only
to a composite that values latency. Killing the run, editing
``campaign.json``, and resuming forfeits in-flight progress.

This module gives the operator a file-drop hot-swap. Drop a JSON file at
``campaigns/{cycle_id}/scoring_steer.json`` with a ``{"per_round": "..."}``
formula. After the next round completes (in :func:`_post_round`), the
file is consumed: the formula compiles, ``session.scoring.round_scorer`` is
replaced, the file is renamed to ``scoring_steer.applied.{ts}.json`` for
audit, and a phase event is emitted.

Per-query steering is intentionally not supported here — changing the
per-query scorer mid-run rewrites recorded ``hit``/``score`` semantics
and triggers divergence-replay, which the operator should opt into via
``optimize --fork-on-divergence`` rather than a silent file-drop.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.domain.phases import emit_phase

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.domain.phases import PhaseEvent

logger = logging.getLogger(__name__)

__all__ = ["STEER_FILENAME", "apply_steer_file"]


STEER_FILENAME = "scoring_steer.json"


def _archive_name(ts: datetime) -> str:
    return f"scoring_steer.applied.{ts.strftime('%Y%m%dT%H%M%S')}.json"


def _read_steer_file(path: Path) -> dict | None:
    """Load + shape-validate the steer file. Returns None on shape failure."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("scoring_steer: read failed (%s)", exc)
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("scoring_steer: invalid JSON (%s) — leaving file in place", exc)
        return None
    if not isinstance(parsed, dict) or "per_round" not in parsed:
        logger.warning(
            "scoring_steer: missing 'per_round' key — got %r — leaving file in place",
            list(parsed) if isinstance(parsed, dict) else type(parsed).__name__,
        )
        return None
    return parsed


def apply_steer_file(
    session: Session,
    round_num: int,
    on_phase: Callable[[PhaseEvent], None] | None = None,
) -> str | None:
    """Look for ``scoring_steer.json`` in the cycle dir; hot-swap if present.

    Returns the new formula on success, ``None`` if no file was found or
    the file failed to validate. On success the file is moved to
    ``scoring_steer.applied.{ts}.json`` so a subsequent round-end won't
    re-apply it. On failure the file is left in place so the operator
    can fix the formula and let it apply on the next round.
    """
    if not session.state.cycle_id or session.store is None:
        return None

    cycle_dir = session.store.campaigns.campaign_dir(session.state.cycle_id)
    steer_path = cycle_dir / STEER_FILENAME
    if not steer_path.exists():
        return None

    parsed = _read_steer_file(steer_path)
    if parsed is None:
        return None

    formula = parsed["per_round"]
    if not isinstance(formula, str) or not formula.strip():
        logger.warning("scoring_steer: 'per_round' must be a non-empty string")
        return None

    # Compile + smoke-eval against a small known namespace so a typo
    # surfaces before we corrupt ``session.scoring.round_scorer``. The eval uses
    # accuracy=0.5 + every namespace name registered in the evaluators
    # registry so undefined-name typos surface as NameError.
    try:
        from promptpotter.application.scoring.evaluators import all_evaluators

        smoke_ns = {ev.name: 0.5 for ev in all_evaluators() if ev.scope == "per_round"} | {
            "accuracy": 0.5
        }
        scorer = compile_round_scorer(formula)
        scorer(smoke_ns)
    except (SyntaxError, NameError, TypeError, ValueError) as exc:
        logger.warning(
            "scoring_steer: formula failed to compile (%s) — leaving file in place. Formula: %s",
            exc,
            formula,
        )
        return None

    prev_formula = session.scoring.scorer_round_formula
    session.scoring.round_scorer = scorer
    session.scoring.scorer_round_formula = formula

    archive = cycle_dir / _archive_name(datetime.now(UTC))
    try:
        steer_path.rename(archive)
    except OSError as exc:
        logger.warning("scoring_steer: archive rename failed (%s)", exc)

    emit_phase(
        on_phase,
        "scoring_steer",
        "applied",
        round=round_num,
        formula=formula,
        previous_formula=prev_formula,
        archive=archive.name,
    )
    logger.info(
        "scoring_steer: per_round formula swapped at round %d → %s",
        round_num,
        formula,
    )
    return formula
