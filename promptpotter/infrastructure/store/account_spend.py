"""User spend, summed from the canonical per-cycle ledger — NOT from ``dashboard.json``, whose spend
block is cumulative-from-seed, so summing those snapshots double-counts a fork's inherited spend."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import SpendTombstoneRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.pricing import compute_usd

if TYPE_CHECKING:
    # Type-only: the campaign store imports THIS module to bank a spend before it destroys the
    # rows carrying it, so a runtime edge back would close the loop.
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


def account_ledgers(campaigns: CampaignStore) -> list[Path]:
    """Every file an account's lifetime spend is recorded in: its cycle ledgers plus the workspace
    ledger, where a deleted campaign's spend is banked. It takes the STORE rather than a ``Stores``
    so the cross-tenant install report sums by the same walk, over a store it rooted itself. The
    workspace path is RESOLVED, not opened — a read must not mint the dir it reads."""
    return [*campaigns.iter_cycle_ledgers(), CycleEventLog.workspace_path(campaigns.workspace)]


def _iter_dated_records(
    ledgers: Iterable[Path], *, since: float, until: float
) -> Iterator[dict[str, Any]]:
    """Every record in ``[since, until)`` across the given ledgers, raw and undiscriminated. A
    corrupt or half-written line degrades to "not there" (``iter_jsonl``), so a torn tail
    UNDER-counts and the gate above this fails OPEN. An unreadable FILE still raises — only
    malformed CONTENT is skipped."""
    for ledger_path in ledgers:
        for rec in iter_jsonl(ledger_path):
            ts_str = rec.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError, AttributeError):
                continue
            if since <= ts < until:
                yield {**rec, "ts": ts}


def iter_user_token_usage(
    *, ledgers: Iterable[Path], since: float, until: float
) -> list[dict[str, Any]]:
    """Every ``TokenUsageRecord`` in the window, shaped for the pricer and the activity chart."""
    out: list[dict[str, Any]] = []
    for rec in _iter_dated_records(ledgers, since=since, until=until):
        if rec.get("record_type") != "token_usage":
            continue
        raw_cost = rec.get("cost_usd")
        input_t = int(rec.get("input_tokens", 0))
        output_t = int(rec.get("output_tokens", 0))
        out.append(
            {
                "ts": rec["ts"],
                "cost_usd": float(raw_cost) if isinstance(raw_cost, int | float) else None,
                "input_tokens": input_t,
                "output_tokens": output_t,
                "tokens": input_t + output_t,
                "model": rec.get("model"),
                # `lookup_rate` keys on the (provider, model) PAIR and refuses a namespaced model
                # id alone, so dropping this prices the row at nothing rather than raising.
                "provider": rec.get("provider"),
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


class BilledSpend(NamedTuple):
    """``UserSpend`` with the token halves kept apart, because a forwarding emitter has to hand
    ``emit_token_usage`` an input and an output count rather than their sum."""

    used_usd: float
    input_tokens: int
    output_tokens: int
    unpriced_tokens: int

    @property
    def used_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: BilledSpend) -> BilledSpend:
        return BilledSpend(
            self.used_usd + other.used_usd,
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.unpriced_tokens + other.unpriced_tokens,
        )

    def since(self, mark: BilledSpend) -> BilledSpend:
        """What this total holds beyond ``mark``. Clamped at zero per component: a ledger can
        SHRINK under ``--keep-results`` or a rewind while the mark beside it survives, and a
        negative residue would refund money at the account tier."""
        return BilledSpend(
            max(0.0, self.used_usd - mark.used_usd),
            max(0, self.input_tokens - mark.input_tokens),
            max(0, self.output_tokens - mark.output_tokens),
            max(0, self.unpriced_tokens - mark.unpriced_tokens),
        )

    def as_user_spend(self) -> UserSpend:
        return UserSpend(self.used_usd, self.used_tokens, self.unpriced_tokens)


ZERO_SPEND = BilledSpend(0.0, 0, 0, 0)

FORWARDED_SPEND_KEY = "forwarded_spend"


def _billed_of(rec: dict[str, Any]) -> BilledSpend:
    """One non-cached ``token_usage`` row as billed spend. Unpriced means the count is known and
    the rate is not, so the tokens land in the residue and the USD stays at zero."""
    inp = int(rec.get("input_tokens", 0))
    out = int(rec.get("output_tokens", 0))
    usd = record_cost_usd(rec)
    if usd is None:
        return BilledSpend(0.0, inp, out, inp + out)
    return BilledSpend(usd, inp, out, 0)


def billed_spend(ledgers: Iterable[Path]) -> BilledSpend:
    """What these ledgers' own rows say was spent, over their whole life. Token usage only — a
    tombstone is appended to the WORKSPACE ledger, never a cycle's, so no cycle ledger can carry
    one to double-count."""
    total = ZERO_SPEND
    for rec in _iter_dated_records(ledgers, since=0.0, until=float("inf")):
        if rec.get("record_type") != "token_usage" or rec.get("cached"):
            continue
        total = total.plus(_billed_of(rec))
    return total


def forwarded_mark(cycle_dir: Path) -> BilledSpend:
    """How much of this cycle's own ledger has already reached ANOTHER ledger — the L4 roll-up's
    high-water mark. Absent means nothing was forwarded, which is every cycle outside a sandbox."""
    data = read_json_optional(CycleLayout(cycle_dir).manifest)
    raw = data.get(FORWARDED_SPEND_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return ZERO_SPEND
    return BilledSpend(
        float(raw.get("used_usd", 0.0)),
        int(raw.get("input_tokens", 0)),
        int(raw.get("output_tokens", 0)),
        int(raw.get("unpriced_tokens", 0)),
    )


def sandbox_cycle_dirs(sandbox: Path) -> list[Path]:
    """Every cycle inside one L4 inner sandbox. The sandbox tree is a SIBLING of the tenant tree
    (``layout.py::inner_sandboxes_dir``), so no account-wide walk reaches it and a destroyer that
    takes one has to enumerate it here."""
    return sorted(sandbox.glob("*/campaigns/*/cycles/*")) if sandbox.is_dir() else []


def sum_user_spend(*, ledgers: Iterable[Path], since: float, until: float) -> UserSpend:
    """Both units plus the unpriceable residue, over live usage AND banked tombstones — a tombstone
    is spend whose rows are gone, so it is added whole rather than re-priced."""
    used_usd = 0.0
    used_tokens = 0
    unpriced_tokens = 0
    for rec in _iter_dated_records(ledgers, since=since, until=until):
        if rec.get("record_type") == "spend_tombstone":
            used_usd += float(rec.get("used_usd", 0.0))
            used_tokens += int(rec.get("used_tokens", 0))
            unpriced_tokens += int(rec.get("unpriced_tokens", 0))
            continue
        if rec.get("record_type") != "token_usage" or rec.get("cached"):
            continue
        billed = _billed_of(rec)
        used_usd += billed.used_usd
        used_tokens += billed.used_tokens
        unpriced_tokens += billed.unpriced_tokens
    return UserSpend(used_usd, used_tokens, unpriced_tokens)


def bank_spend(
    *,
    workspace: WorkspaceDir,
    cycle_dirs: list[Path],
    campaign_id: str,
    cycle_id: str = "",
) -> UserSpend:
    """Sum what a subject still HOLDS and write it to the workspace ledger BEFORE its rows are
    destroyed. Called from inside the three destroyers themselves, so no caller can take a ledger
    without banking it; obligatory THERE and nowhere else, since archiving keeps the rows and
    banking them too would double-count. A subject already carrying a tombstone is skipped —
    banking precedes the delete, so a crash between the two leaves the tombstone standing and a
    retry counts the money twice. ``cycle_id`` is empty for a whole campaign.

    A cycle whose spend already reached another ledger banks only the REMAINDER: an L4 inner cycle
    forwards onto its outer cycle as it runs, so summing its rows whole would bill that money a
    second time on the way out."""
    # Unbounded on purpose: this banks everything the subject ever wrote, not a window of it.
    residue = ZERO_SPEND
    for cycle_dir in cycle_dirs:
        held = billed_spend([CycleLayout(cycle_dir).ledger])
        residue = residue.plus(held.since(forwarded_mark(cycle_dir)))
    spent = residue.as_user_spend()
    if spent == UserSpend(0.0, 0, 0):
        return spent
    log = CycleEventLog.open_workspace(workspace)
    if _already_banked(log.path, campaign_id=campaign_id, cycle_id=cycle_id):
        return spent
    log.append(
        SpendTombstoneRecord(
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            used_usd=spent.used_usd,
            used_tokens=spent.used_tokens,
            unpriced_tokens=spent.unpriced_tokens,
        )
    )
    return spent


def _already_banked(workspace_ledger: Path, *, campaign_id: str, cycle_id: str) -> bool:
    # `record_types` is a raw-line SUBSTRING probe, so the type is re-asserted on the parsed row —
    # a neighbouring record naming the string in a payload must not answer for a tombstone.
    return any(
        rec.get("record_type") == "spend_tombstone"
        and rec.get("campaign_id") == campaign_id
        and str(rec.get("cycle_id", "")) == cycle_id
        for rec in iter_jsonl(workspace_ledger, record_types=frozenset({"spend_tombstone"}))
    )


__all__ = [
    "FORWARDED_SPEND_KEY",
    "ZERO_SPEND",
    "BilledSpend",
    "UserSpend",
    "account_ledgers",
    "bank_spend",
    "billed_spend",
    "forwarded_mark",
    "iter_user_token_usage",
    "record_cost_usd",
    "sandbox_cycle_dirs",
    "sum_user_spend",
]
