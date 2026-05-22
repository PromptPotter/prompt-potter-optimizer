"""LiveStateCore + spend bookkeeping — shared accumulators for live subscribers.

Both ``LiveDisplay`` (terminal) and ``LiveDashboardView`` (``dashboard.json``)
subscribe the same ``CycleEventLog``. They maintain divergent surface-specific
state (tqdm bars on one side, JSON spend rollup on the other), but a small
core overlaps:

* the active round number,
* the running origin + best anchors (updated on ``INIT:exit`` and on an
  improved ``L1_SCORE:exit``),
* the round-wide Posterior-of-Being-Best snapshot used to render cross-round
  ▲/▼ arrows, and
* the two-bucket (backend / loop) spend rollup populated from per-sample
  ``pipeline_data.step_tokens`` and per-call ``TokenUsageRecord`` rows.

Surface state stays on each class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promptpotter.domain.phases import CampaignPhase, PhaseEvent
from promptpotter.domain.run_records import TokenUsageRecord
from promptpotter.shared.spend import compute_usd

__all__ = [
    "LiveStateCore",
    "accumulate_backend_spend",
    "add_to_spend_bucket",
    "apply_p_best_update",
    "apply_phase",
    "apply_token_usage",
    "empty_bucket",
    "empty_spend",
    "roll_p_best_at_round_complete",
    "top_n_p_best",
]


def empty_bucket() -> dict[str, Any]:
    """One ``state["spend"]`` sub-bucket — backend or loop."""
    return {
        "used_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "rate_known": False,
        "model": None,
    }


def empty_spend() -> dict[str, Any]:
    return {
        "backend": empty_bucket(),
        "loop": empty_bucket(),
        "total_used_usd": 0.0,
        "budget_usd": None,
    }


def add_to_spend_bucket(
    spend: dict[str, Any],
    bucket: str,
    in_tok: int,
    out_tok: int,
    model: str | None,
    wire_cost_usd: float | None,
) -> None:
    """Single mutator for both spend buckets. Recomputes the running total.

    ``wire_cost_usd`` is the provider-reported USD for this call, if any
    (today only OpenRouter ships it). When provided, it short-circuits the
    rate lookup; this is the path that matches the operator's invoice.
    Otherwise the rate table is consulted via ``compute_usd``.
    """
    b = spend.setdefault(bucket, empty_bucket())
    b["input_tokens"] += in_tok
    b["output_tokens"] += out_tok
    if model and not b.get("model"):
        b["model"] = model
    usd = compute_usd(model, in_tok, out_tok, override_usd=wire_cost_usd)
    if usd is not None:
        b["used_usd"] = round(b["used_usd"] + usd, 6)
        b["rate_known"] = True
    spend["total_used_usd"] = round(
        spend.get("backend", {}).get("used_usd", 0.0) + spend.get("loop", {}).get("used_usd", 0.0),
        6,
    )


def accumulate_backend_spend(spend: dict[str, Any], pipeline_data: dict) -> None:
    """Sum per-sample backend tokens into ``spend["backend"]``.

    Reads ``pipeline_data.step_tokens`` (per-LLM-node ``{input, output,
    cost_usd?}``) and ``pipeline_data.llm_provider`` (model string). When a
    step entry carries ``cost_usd`` (set by the backend when the wire LLM is
    OpenRouter), it short-circuits the rate-table lookup. Otherwise
    ``shared/spend.py`` resolves USD; unknown models still bump token totals
    so the chip falls back to a token-count display rather than a fake zero.
    Cached samples skip this call (no fresh wire cost).
    """
    step_tokens = pipeline_data.get("step_tokens") or {}
    if not isinstance(step_tokens, dict):
        return
    in_tok = 0
    out_tok = 0
    wire_cost: float | None = None
    for entry in step_tokens.values():
        if not isinstance(entry, dict):
            continue
        in_tok += int(entry.get("input", 0) or 0)
        out_tok += int(entry.get("output", 0) or 0)
        step_cost = entry.get("cost_usd")
        if step_cost is not None:
            wire_cost = (wire_cost or 0.0) + float(step_cost)
    if in_tok == 0 and out_tok == 0:
        return
    model = pipeline_data.get("llm_provider")
    add_to_spend_bucket(spend, "backend", in_tok, out_tok, model, wire_cost)


def apply_token_usage(spend: dict[str, Any], record: TokenUsageRecord) -> None:
    """Route an optimizer LLM call into ``spend["loop"]`` (or ``backend`` for
    backend-kind events that surface here)."""
    bucket = "loop" if record.kind == "optimizer" else "backend"
    add_to_spend_bucket(
        spend,
        bucket,
        int(record.input_tokens),
        int(record.output_tokens),
        record.model,
        record.cost_usd,
    )


@dataclass
class LiveStateCore:
    """Shared per-cycle scalars for live ledger subscribers."""

    round_num: int = 0
    origin_acc: float = 0.0
    best_acc: float = 0.0
    last_p_best: dict[str, float] = field(default_factory=dict)
    current_p_best: dict[str, float] = field(default_factory=dict)
    current_p_best_id: str = ""
    current_p_best_n: int = 0


def apply_phase(core: LiveStateCore, event: PhaseEvent, view: dict | None = None) -> None:
    """Update *core* from a ``PhaseEvent``.

    Round number tracks ``event.round`` directly. Origin is set once on
    ``INIT:exit`` and never re-anchored — "vs origin" labels are meant
    to read against the immutable campaign origin, not the running
    leader. The leader anchor (``best_acc``) advances on an improved
    ``L1_SCORE:exit``.
    """
    if event.round is not None:
        core.round_num = event.round
    if view is None:
        return
    if event.phase == CampaignPhase.INIT and event.event == "exit":
        new_origin = view.get("origin_acc", core.origin_acc)
        core.origin_acc = new_origin
        if new_origin > core.best_acc:
            core.best_acc = new_origin
    elif event.phase == CampaignPhase.L1_SCORE and event.event == "exit" and view.get("improved"):
        winner = view.get("winner_accuracy", core.best_acc)
        if winner > core.best_acc:
            core.best_acc = winner


def apply_p_best_update(
    core: LiveStateCore,
    current_id: str,
    n_samples: int,
    p_best: dict[str, float],
) -> None:
    """Stash the latest mid-round P(best) snapshot for the round-end roll-up."""
    if not p_best:
        return
    core.current_p_best = dict(p_best)
    core.current_p_best_id = current_id
    core.current_p_best_n = n_samples


def roll_p_best_at_round_complete(core: LiveStateCore) -> None:
    """At round-end, promote the current snapshot to ``last`` for next round's arrows."""
    if core.current_p_best:
        core.last_p_best = core.current_p_best
        core.current_p_best = {}
        core.current_p_best_id = ""
        core.current_p_best_n = 0


def top_n_p_best(snapshot: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    """Top-*n* ``(cid, prob)`` from a snapshot, descending."""
    return sorted(snapshot.items(), key=lambda kv: -kv[1])[:n]
