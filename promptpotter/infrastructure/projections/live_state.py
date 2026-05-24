"""LiveStateCore + spend bookkeeping — shared accumulators for live subscribers.

``LiveDisplay`` and ``LiveDashboardView`` both subscribe to the same ledger;
the overlap (round number, origin/best anchors, PoBB snapshot, backend/loop
spend rollup) lives here. Surface-specific state stays on each class."""

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
    "backfill_spend_rates",
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
    """Single mutator for both buckets; recomputes the running total.
    ``wire_cost_usd`` is the provider-reported USD (OpenRouter); when present it
    short-circuits the rate table and matches the operator's invoice."""
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


def accumulate_backend_spend(spend: dict[str, Any], pipeline_data: dict[str, Any]) -> None:
    """Sum per-sample backend tokens into ``spend['backend']``.
    Reads ``step_tokens`` + ``llm_provider``; per-step ``cost_usd`` short-circuits
    the rate table. Unknown models still bump tokens (chip falls back to a
    token-count, not a fake zero). Cached samples skip this call."""
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
    """Optimizer call → ``spend['loop']``; backend-kind events → ``backend``."""
    bucket = "loop" if record.kind == "optimizer" else "backend"
    add_to_spend_bucket(
        spend,
        bucket,
        int(record.input_tokens),
        int(record.output_tokens),
        record.model,
        record.cost_usd,
    )


def backfill_spend_rates(spend: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``used_usd`` on buckets whose rate now resolves.
    Optimizer cache hits short-circuit ``emit_token_usage`` so a re-run never
    fires fresh ``TokenUsageRecord`` for the loop bucket; this pass re-resolves
    the rate on load (per-event historical drift accepted vs replaying the ledger)."""
    for b in spend.values():
        if not isinstance(b, dict):
            continue
        if b.get("rate_known") is True:
            continue
        model = b.get("model")
        in_tok = int(b.get("input_tokens") or 0)
        out_tok = int(b.get("output_tokens") or 0)
        if not model or (in_tok == 0 and out_tok == 0):
            continue
        usd = compute_usd(model, in_tok, out_tok)
        if usd is None:
            continue
        b["used_usd"] = round(float(usd), 6)
        b["rate_known"] = True
    spend["total_used_usd"] = round(
        sum(float(b.get("used_usd") or 0.0) for b in spend.values() if isinstance(b, dict)),
        6,
    )
    return spend


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


def apply_phase(core: LiveStateCore, event: PhaseEvent, view: dict[str, Any] | None = None) -> None:
    """Update *core* from a ``PhaseEvent``. Origin set once on ``INIT:exit`` and
    never re-anchored (immutable campaign origin). Leader (``best_acc``) advances
    on improved ``L1_SCORE:exit``."""
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
