from __future__ import annotations

import time
from typing import Literal

from pydantic import Field

from promptpotter.domain.spend import KeySource, bill_is_floor, calls_rate_priced
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

    series_spend: dict[str, float] = Field(
        description="Billed USD per `series_labels` entry. A call no provider billed adds nothing "
        "here and still counts in the other two; one no rate prices either sets the response's "
        "`bill_is_floor`."
    )
    series_tokens: dict[str, int]
    series_requests: dict[str, int]


class ActivityResponse(StrictModel):
    """Time-bucketed spend / requests / tokens over the requested window."""

    window: ActivityWindow
    group_by: ActivityGroupBy = Field(
        description="The colour axis: `model` is the exact model id, `api_key` the key that "
        "paid: the provider that billed the call, and whether the host's key under the coupon "
        "or the account's own sent it (`TokenUsageRecord.key_source`)."
    )
    buckets: list[ActivityBucket]
    series_labels: list[str] = Field(
        description="Every series in the window, in first-seen order — one colour per label, "
        "stable across the buckets. A call outside the optimizer's own carries its kind."
    )
    total_spend_usd: float = Field(description="What providers reported they billed.")
    total_rate_priced_usd: float = Field(
        description="What our rate table prices the window's calls no provider billed. Never "
        "spent, and in no series."
    )
    calls_rate_priced: bool = Field(
        description="Some call in the window was priced at our rate, so there is a "
        "`total_rate_priced_usd` to show beside the bill."
    )
    bill_is_floor: bool = Field(
        description="The two totals understate: a call in the window carries neither a bill nor "
        "a rate's price."
    )
    total_tokens: int
    total_requests: int
    peak_spend_usd: float | None = Field(
        description="The tallest bucket's billed USD — the scale the spend bars are drawn "
        "against. Null where no bucket was billed anything, so there is no bar to scale."
    )
    peak_tokens: int | None = Field(description="`peak_spend_usd`'s twin over a bucket's tokens.")
    peak_requests: int | None = Field(
        description="`peak_spend_usd`'s twin over a bucket's requests."
    )


_KEY_LABELS: dict[KeySource | None, str] = {"host": "host key", "user": "own key", None: "replay"}


def _series_label(row: UsageRow, group_by: ActivityGroupBy) -> str:
    if group_by == "model":
        base = row.model or "unknown"
    else:
        base = f"{row.provider or 'unknown'} · {_KEY_LABELS[row.key_source]}"
    # A judge or backend usually shares model and biller with the optimizer: untagged, it sums into that series.
    return base if row.kind == "optimizer" else f"{base} ({row.kind})"


def _peak[N: (int, float)](totals: list[N]) -> N | None:
    top = max(totals)
    return top if top > 0 else None


def account_activity(
    stores: Stores, *, window: ActivityWindow, group_by: ActivityGroupBy
) -> ActivityResponse:
    span_s = _WINDOW_SECONDS[window]
    until = time.time()
    since = until - span_s
    width = span_s / _N_BUCKETS
    spend: list[dict[str, float]] = [{} for _ in range(_N_BUCKETS)]
    tokens: list[dict[str, int]] = [{} for _ in range(_N_BUCKETS)]
    requests: list[dict[str, int]] = [{} for _ in range(_N_BUCKETS)]
    rate_priced = [0.0] * _N_BUCKETS
    unpriced = [0] * _N_BUCKETS
    labels: dict[str, None] = {}
    for row in iter_user_token_usage(
        ledgers=account_ledgers(stores.campaigns), since=since, until=until
    ):
        i = min(_N_BUCKETS - 1, int((row.ts - since) / width))
        label = _series_label(row, group_by)
        labels[label] = None
        spend[i].setdefault(label, 0.0)
        if row.billed_usd is not None:
            spend[i][label] += row.billed_usd
        elif row.rate_priced_usd is not None:
            rate_priced[i] += row.rate_priced_usd
        else:
            unpriced[i] += row.tokens
        tokens[i][label] = tokens[i].get(label, 0) + row.tokens
        requests[i][label] = requests[i].get(label, 0) + 1
    spent = [sum(s.values()) for s in spend]
    used = [sum(t.values()) for t in tokens]
    asked = [sum(r.values()) for r in requests]
    return ActivityResponse(
        window=window,
        group_by=group_by,
        buckets=[
            ActivityBucket(
                series_spend=spend[i], series_tokens=tokens[i], series_requests=requests[i]
            )
            for i in range(_N_BUCKETS)
        ],
        series_labels=list(labels),
        total_spend_usd=round(sum(spent), 6),
        total_rate_priced_usd=round(sum(rate_priced), 6),
        calls_rate_priced=calls_rate_priced(sum(rate_priced)),
        bill_is_floor=bill_is_floor(sum(unpriced)),
        total_tokens=sum(used),
        total_requests=sum(asked),
        peak_spend_usd=_peak(spent),
        peak_tokens=_peak(used),
        peak_requests=_peak(asked),
    )
