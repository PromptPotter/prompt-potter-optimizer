"""User spend, summed from the canonical per-cycle ledger — NOT from ``dashboard.json``, whose spend
block is cumulative-from-seed, so summing those snapshots double-counts a fork's inherited spend."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.spend import compute_usd


def start_of_utc_day() -> float:
    """UTC-midnight timestamp — the day boundary ``JobRegistry.list_created_today`` uses."""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


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


def record_cost_usd(rec: dict[str, Any]) -> float:
    """Billed USD for one usage record; only ``cached=False`` is money that left the account. An unpriced
    model resolves to ``0.0``, under-counting the cap — and this path has no channel to say so."""
    if rec.get("cached"):
        return 0.0
    raw = rec.get("cost_usd")
    usd = compute_usd(
        rec.get("model"),
        int(rec.get("input_tokens", 0)),
        int(rec.get("output_tokens", 0)),
        override_usd=float(raw) if isinstance(raw, int | float) else None,
        provider=rec.get("provider"),
    )
    return usd if usd is not None else 0.0


def sum_user_spend(*, stores: Stores, since: float, until: float) -> float:
    return sum(
        record_cost_usd(r) for r in iter_user_token_usage(stores=stores, since=since, until=until)
    )


__all__ = ["iter_user_token_usage", "record_cost_usd", "start_of_utc_day", "sum_user_spend"]
