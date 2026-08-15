"""User spend, summed from the canonical per-cycle ledger — NOT from ``dashboard.json``, whose spend
block is cumulative-from-seed, so summing those snapshots double-counts a fork's inherited spend."""

from __future__ import annotations

from datetime import datetime
from typing import Any, NamedTuple

from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.spend import compute_usd


def iter_user_token_usage(*, stores: Stores, since: float, until: float) -> list[dict[str, Any]]:
    """Every ``TokenUsageRecord`` in ``[since, until)`` across the user's ledgers, archived included —
    archiving must not free budget. An unreadable ledger RAISES: a zero fails open into a full budget."""
    out: list[dict[str, Any]] = []
    for ledger_path in stores.campaigns.iter_cycle_ledgers():
        for rec in iter_jsonl(ledger_path):
            if rec.get("record_type") != "token_usage":
                continue
            ts_str = rec.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError, AttributeError):
                continue
            if not (since <= ts < until):
                continue
            raw_cost = rec.get("cost_usd")
            input_t = int(rec.get("input_tokens", 0))
            output_t = int(rec.get("output_tokens", 0))
            out.append(
                {
                    "ts": ts,
                    "cost_usd": float(raw_cost) if isinstance(raw_cost, int | float) else None,
                    "input_tokens": input_t,
                    "output_tokens": output_t,
                    "tokens": input_t + output_t,
                    "model": rec.get("model"),
                    "kind": rec.get("kind"),
                    "cached": bool(rec.get("cached", False)),
                }
            )
    return out


def record_cost_usd(rec: dict[str, Any]) -> float | None:
    """Billed USD for one usage record; only ``cached=False`` is money that left the account. ``None``
    means unpriced — no wire cost and no rate on file — which each caller answers for itself."""
    if rec.get("cached"):
        return 0.0
    raw = rec.get("cost_usd")
    return compute_usd(
        rec.get("model"),
        int(rec.get("input_tokens", 0)),
        int(rec.get("output_tokens", 0)),
        override_usd=float(raw) if isinstance(raw, int | float) else None,
        provider=rec.get("provider"),
    )


class UserSpend(NamedTuple):
    """What an account has spent, in both units plus the residue the first one cannot see. Field
    names mirror ``SpendBucket`` so the per-cycle and per-account reads name one concept."""

    used_usd: float
    used_tokens: int
    unpriced_tokens: int


def sum_user_spend(*, stores: Stores, since: float, until: float) -> UserSpend:
    used_usd = 0.0
    used_tokens = 0
    unpriced_tokens = 0
    for rec in iter_user_token_usage(stores=stores, since=since, until=until):
        if rec["cached"]:
            continue
        tokens = int(rec["tokens"])
        used_tokens += tokens
        usd = record_cost_usd(rec)
        if usd is None:
            unpriced_tokens += tokens
        else:
            used_usd += usd
    return UserSpend(used_usd, used_tokens, unpriced_tokens)


__all__ = ["UserSpend", "iter_user_token_usage", "record_cost_usd", "sum_user_spend"]
