"""An account's billed calls over a window, bucketed for the charts of Account → Activity."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import Field

from promptpotter.domain.spend import bill_is_floor
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.account_spend import (
    UsageRow,
    account_ledgers,
    iter_user_token_usage,
)
from promptpotter.infrastructure.store.stores import Stores

ActivityWindow = Literal["15m", "30m", "1h", "3h", "1d", "2d", "1w", "1mo", "1y"]
ActivityGroupBy = Literal["model", "api_key"]

_WINDOW_SECONDS: dict[ActivityWindow, int] = {
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "3h": 3 * 60 * 60,
    "1d": 24 * 60 * 60,
    "2d": 2 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
    "1mo": 30 * 24 * 60 * 60,
    "1y": 365 * 24 * 60 * 60,
}
_N_BUCKETS = 30


class ActivityBucket(StrictModel):
    """One time bucket of the Activity pane's three stacked bar charts."""

    ts: float = Field(description="Epoch seconds at the bucket's leading edge")
    spend_usd: float
    bill_is_floor: bool = Field(
        description="A call in this bucket was billed at no resolvable rate, so `spend_usd` and "
        "its series understate."
    )
    tokens: int
    requests: int
    series_spend: dict[str, float] = Field(
        description="Billed USD per `series_labels` entry. An unpriced call adds nothing here, "
        "sets `bill_is_floor`, and still counts in the other two."
    )
    series_tokens: dict[str, int]
    series_requests: dict[str, int]


class ActivityResponse(StrictModel):
    """Time-bucketed spend / requests / tokens over the requested window."""

    window: ActivityWindow
    group_by: ActivityGroupBy = Field(
        description="The colour axis: `model` is the exact model id, `api_key` is who billed the "
        "call (`TokenUsageRecord.provider`)."
    )
    since: float
    until: float
    buckets: list[ActivityBucket]
    series_labels: list[str] = Field(
        description="Every series in the window, in first-seen order — one colour per label, "
        "stable across the buckets. A call outside the optimizer's own carries its kind."
    )
    total_spend_usd: float
    bill_is_floor: bool = Field(
        description="`total_spend_usd` understates: a bucket's `bill_is_floor`, over the window."
    )
    total_tokens: int
    total_requests: int


def _series_label(row: UsageRow, group_by: ActivityGroupBy) -> str:
    base = (row.model if group_by == "model" else row.provider) or "unknown"
    # A judge or a backend shares a model and a biller with the optimizer more often than not, so
    # an untagged kind would sum into the optimizer's series.
    return base if row.kind == "optimizer" else f"{base} ({row.kind})"


def account_activity(
    stores: Stores, *, window: ActivityWindow, group_by: ActivityGroupBy
) -> ActivityResponse:
    """Each call lands at the price it was stamped with when it was recorded."""
    span_s = _WINDOW_SECONDS[window]
    until = time.time()
    since = until - span_s
    width = span_s / _N_BUCKETS
    spend: list[dict[str, float]] = [{} for _ in range(_N_BUCKETS)]
    tokens: list[dict[str, int]] = [{} for _ in range(_N_BUCKETS)]
    requests: list[dict[str, int]] = [{} for _ in range(_N_BUCKETS)]
    unpriced = [0] * _N_BUCKETS
    labels: dict[str, None] = {}
    for row in iter_user_token_usage(
        ledgers=account_ledgers(stores.campaigns), since=since, until=until
    ):
        i = min(_N_BUCKETS - 1, int((row.ts - since) / width))
        label = _series_label(row, group_by)
        labels[label] = None
        spend[i].setdefault(label, 0.0)
        if row.billed_usd is None:
            unpriced[i] += row.tokens
        else:
            spend[i][label] += row.billed_usd
        tokens[i][label] = tokens[i].get(label, 0) + row.tokens
        requests[i][label] = requests[i].get(label, 0) + 1
    buckets = [
        ActivityBucket(
            ts=since + i * width,
            spend_usd=sum(spend[i].values()),
            bill_is_floor=bill_is_floor(unpriced[i]),
            tokens=sum(tokens[i].values()),
            requests=sum(requests[i].values()),
            series_spend=spend[i],
            series_tokens=tokens[i],
            series_requests=requests[i],
        )
        for i in range(_N_BUCKETS)
    ]
    return ActivityResponse(
        window=window,
        group_by=group_by,
        since=since,
        until=until,
        buckets=buckets,
        series_labels=list(labels),
        total_spend_usd=round(sum(b.spend_usd for b in buckets), 6),
        bill_is_floor=bill_is_floor(sum(unpriced)),
        total_tokens=sum(b.tokens for b in buckets),
        total_requests=sum(b.requests for b in buckets),
    )


__all__ = [
    "ActivityBucket",
    "ActivityGroupBy",
    "ActivityResponse",
    "ActivityWindow",
    "account_activity",
]
