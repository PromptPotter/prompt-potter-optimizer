"""User spend, summed from the canonical ledger.

Spend is owned by the per-cycle ledger (``TokenUsageRecord``, ADR-0003 "token/cost
on the canonical ledger") — **not** by ``dashboard.json``, which is a Display
projection whose ``spend`` block is cumulative-from-seed (a fork/resume inherits the
parent's whole spend block, then adds its own). Summing those snapshots double-counts
inherited spend, so anything that needs an additive figure over a time window —
the daily-cap gate (:mod:`quota`) and the Activity pane (``/auth/activity``) — reads
it here, from the ledger, filtered by record ``timestamp``.

``cost_usd`` may be absent on a record (Groq doesn't return wire cost); we fall back
to the rate table × tokens so historical spend isn't silently zero.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from promptpotter.infrastructure.store import Stores
from promptpotter.shared.spend import lookup_rate


def start_of_utc_day() -> float:
    """UTC-midnight timestamp — the day boundary ``JobRegistry.list_created_today`` uses."""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def iter_user_token_usage(*, store: Stores, since: float, until: float) -> list[dict[str, Any]]:
    """Walk every per-cycle ledger under the user's workspace.

    The canonical per-cycle ledger lives at ``{cycle_dir}/.runtime/ledger.jsonl``
    (the name ``events.jsonl`` is used only by the workspace-scoped sibling).
    Filters to ``TokenUsageRecord`` rows whose ``timestamp`` lands in
    ``[since, until)``. JSON-decodes each line; cheaper than running the full
    ledger ``iter`` (no pydantic validation per record). Beta-scale only — fine
    for a few campaigns × a few cycles each.

    Returns ``cost_usd`` as ``None`` when the record didn't carry one; the caller
    resolves it via :func:`record_cost_usd`.
    """
    out: list[dict[str, Any]] = []
    campaigns_root = store.base_dir / "campaigns"
    if not campaigns_root.is_dir():
        return out
    for ledger_path in campaigns_root.glob("*/cycles/*/.runtime/ledger.jsonl"):
        try:
            with ledger_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"token_usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("record_type") != "token_usage":
                        continue
                    ts_str = rec.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    except (ValueError, TypeError):
                        continue
                    if not (since <= ts < until):
                        continue
                    raw_cost = rec.get("cost_usd")
                    input_t = int(rec.get("input_tokens", 0))
                    output_t = int(rec.get("output_tokens", 0))
                    out.append(
                        {
                            "ts": ts,
                            "cost_usd": float(raw_cost)
                            if isinstance(raw_cost, int | float)
                            else None,
                            "input_tokens": input_t,
                            "output_tokens": output_t,
                            "tokens": input_t + output_t,
                            "model": rec.get("model"),
                            "kind": rec.get("kind"),
                        }
                    )
        except OSError:
            continue
    return out


def record_cost_usd(rec: dict[str, Any]) -> float:
    """Resolve one usage record's USD cost: wire ``cost_usd`` if present, else the
    rate table × tokens, else ``0.0`` (unknown model / no rate)."""
    cost = rec.get("cost_usd")
    if cost is not None:
        return float(cost)
    model = rec.get("model") or "unknown"
    rate = lookup_rate(model if model != "unknown" else None)
    if rate is None:
        return 0.0
    input_t = int(rec.get("input_tokens", 0))
    output_t = int(rec.get("output_tokens", 0))
    return input_t * rate[0] + output_t * rate[1]


def sum_user_spend(*, store: Stores, since: float, until: float) -> float:
    """Total USD spend over ``[since, until)`` from the user's per-cycle ledgers."""
    return sum(
        record_cost_usd(r) for r in iter_user_token_usage(store=store, since=since, until=until)
    )


__all__ = ["iter_user_token_usage", "record_cost_usd", "start_of_utc_day", "sum_user_spend"]
