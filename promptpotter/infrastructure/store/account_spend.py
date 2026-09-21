"""User spend, summed from the canonical per-cycle ledger — NOT from ``dashboard.json``, whose spend
block is cumulative-from-seed, so summing those snapshots double-counts a fork's inherited spend."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.run_records import SpendTombstoneRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.runtime_flags import derive_run_phase
from promptpotter.infrastructure.store.io import stat_key
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import HOLD_TRAIL, iter_jsonl, open_holds
from promptpotter.shared.clock import epoch_seconds

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
            ts = epoch_seconds(rec.get("timestamp"))
            if ts is None:
                continue
            if since <= ts < until:
                yield {**rec, "ts": ts}


def iter_user_token_usage(
    *, ledgers: Iterable[Path], since: float, until: float
) -> list[dict[str, Any]]:
    """Every ``TokenUsageRecord`` in the window, shaped for :func:`record_cost_usd` and the activity
    chart."""
    out: list[dict[str, Any]] = []
    for rec in _iter_dated_records(ledgers, since=since, until=until):
        if rec.get("record_type") != "token_usage":
            continue
        out.append(
            {
                "ts": rec["ts"],
                "cost_usd": rec.get("cost_usd"),
                "tokens": int(rec.get("input_tokens", 0)) + int(rec.get("output_tokens", 0)),
                "model": rec.get("model"),
                "kind": rec.get("kind"),
                "cached": bool(rec.get("cached", False)),
            }
        )
    return out


def record_cost_usd(rec: dict[str, Any]) -> float | None:
    """Billed USD for one usage record — the price it was stamped with — and only ``cached=False``
    is money that left the account. ``None`` means unpriced, which each caller answers for itself."""
    if rec.get("cached"):
        return 0.0
    raw = rec.get("cost_usd")
    return float(raw) if isinstance(raw, int | float) else None


class UserSpend(NamedTuple):
    """What an account has spent — its BILLS, in both units plus the residue the first one cannot
    see — and apart from them what its unreported sends may have cost, which is never spent and
    still binds every ceiling (``infrastructure/llm/spend_book.py``). Field names mirror
    ``SpendBucket`` so the per-cycle and per-account reads name one concept."""

    used_usd: float
    used_tokens: int
    unpriced_tokens: int
    unreported_usd: float = 0.0
    unreported_tokens: int = 0

    @property
    def at_most_usd(self) -> float:
        """The most that may have left the account: every bill, and every unreported send."""
        return self.used_usd + self.unreported_usd

    @property
    def at_most_tokens(self) -> int:
        return self.used_tokens + self.unreported_tokens

    def plus(self, other: UserSpend) -> UserSpend:
        return UserSpend(
            self.used_usd + other.used_usd,
            self.used_tokens + other.used_tokens,
            self.unpriced_tokens + other.unpriced_tokens,
            self.unreported_usd + other.unreported_usd,
            self.unreported_tokens + other.unreported_tokens,
        )


ZERO_SPEND = UserSpend(0.0, 0, 0)


def _billed_of(rec: dict[str, Any]) -> UserSpend:
    """One non-cached ``token_usage`` row as billed spend. Unpriced means the count is known and
    the rate is not, so the tokens land in the residue and the USD stays at zero."""
    tokens = int(rec.get("input_tokens", 0)) + int(rec.get("output_tokens", 0))
    usd = record_cost_usd(rec)
    return UserSpend(0.0, tokens, tokens) if usd is None else UserSpend(usd, tokens, 0)


def _unreported_of(hold: dict[str, Any]) -> UserSpend:
    """One hold no bill closed, at the bound it was admitted on. Unpriced lands in the residue for
    the same reason a bill's does (:func:`_billed_of`): the bound is known in tokens and not in
    money, so folding it as ``$0.00`` would report an unpriceable send as a free one — and
    ``at_most_usd``, which bounds the account's own ceiling, would under-read by it."""
    tokens = int(hold.get("input_tokens", 0)) + int(hold.get("output_tokens", 0))
    usd = hold.get("cost_usd")
    if not isinstance(usd, int | float):
        return UserSpend(0.0, 0, tokens, 0.0, tokens)
    return UserSpend(0.0, 0, 0, float(usd), tokens)


def _fold_billed(records: Iterable[dict[str, Any]]) -> tuple[UserSpend, UserSpend]:
    """The bills over the usage rows, and apart from them every hold no bill closed, at its bound —
    whether that is a send still out or one unreported, only the run's liveness says
    (:func:`_unreported_once_stopped`). A nested run's bill that was carried onto its outer run's
    ledger is summed there, never here."""
    rows = list(records)
    total = ZERO_SPEND
    for rec in rows:
        if rec.get("record_type") == "token_usage" and not (
            rec.get("cached") or rec.get("mirrored")
        ):
            total = total.plus(_billed_of(rec))
    open_ = ZERO_SPEND
    for rec in open_holds(rows).values():
        open_ = open_.plus(_unreported_of(rec))
    return total, open_


def _unreported_once_stopped(ledger: Path, open_: UserSpend) -> UserSpend:
    """A hold no bill closed on a LIVE run is a send still out, and the account wallet already
    reserves that run's whole ceiling, so counting it too would read a running cell's worst case
    twice. On a run that is not live (killed, paused, finished) nobody will close it: it is
    unreported, at its bound."""
    if open_ == ZERO_SPEND:
        return open_
    phase = derive_run_phase(CycleLayout.of_ledger(ledger).cycle_dir)
    return ZERO_SPEND if phase in (RunPhase.RUNNING, RunPhase.GATE) else open_


_TOMBSTONE = frozenset({"spend_tombstone"})
_BILLED_BY_LEDGER: dict[Path, tuple[tuple[int, int], tuple[UserSpend, UserSpend]]] = {}
_BANKED_BY_LEDGER: dict[Path, tuple[tuple[int, int], dict[str, UserSpend]]] = {}


def _ledger_billed(ledger: Path) -> UserSpend:
    """One ledger's whole-life bills and unreported sends, re-read only when the file's size or
    mtime moved — the campaign list is polled, and a ledger is append-only between the rewinds
    that restat it. The open holds are cached apart: whether they are unreported moves with the
    run, not the file."""
    key = stat_key(ledger)
    if key is None:
        return ZERO_SPEND
    hit = _BILLED_BY_LEDGER.get(ledger)
    if hit is not None and hit[0] == key:
        billed, open_ = hit[1]
    else:
        billed, open_ = _fold_billed(
            rec
            for rec in iter_jsonl(ledger, record_types=HOLD_TRAIL)
            if epoch_seconds(rec.get("timestamp")) is not None
        )
        _BILLED_BY_LEDGER[ledger] = (key, (billed, open_))
    return billed.plus(_unreported_once_stopped(ledger, open_))


def billed_spend(ledgers: Iterable[Path]) -> UserSpend:
    """What these ledgers' own rows say was billed, and left unreported, over their whole life.
    Holds and bills only — a tombstone is appended to the WORKSPACE ledger, never a cycle's, so no
    cycle ledger can carry one to double-count."""
    total = ZERO_SPEND
    for ledger in ledgers:
        total = total.plus(_ledger_billed(ledger))
    return total


def _banked_by_campaign(workspace_ledger: Path) -> dict[str, UserSpend]:
    key = stat_key(workspace_ledger)
    if key is None:
        return {}
    hit = _BANKED_BY_LEDGER.get(workspace_ledger)
    if hit is not None and hit[0] == key:
        return hit[1]
    banked: dict[str, UserSpend] = {}
    for rec in iter_jsonl(workspace_ledger, record_types=_TOMBSTONE):
        if rec.get("record_type") != "spend_tombstone":
            continue
        campaign_id = str(rec.get("campaign_id", ""))
        banked[campaign_id] = banked.get(campaign_id, ZERO_SPEND).plus(_tombstone_of(rec))
    _BANKED_BY_LEDGER[workspace_ledger] = (key, banked)
    return banked


def _tombstone_of(rec: dict[str, Any]) -> UserSpend:
    """A banked subject's spend — whole, never re-priced: its rows are gone."""
    return UserSpend(
        float(rec.get("used_usd", 0.0)),
        int(rec.get("used_tokens", 0)),
        int(rec.get("unpriced_tokens", 0)),
        float(rec.get("unreported_usd", 0.0)),
        int(rec.get("unreported_tokens", 0)),
    )


def campaign_spend(campaigns: CampaignStore, campaign_id: str) -> UserSpend:
    """One campaign's share of :func:`sum_user_spend`'s lifetime total: its cycle ledgers plus the
    tombstones banked under its id (a deleted stub fork, a reaped inner sandbox's residue)."""
    own = billed_spend(campaigns.campaign_cycle_ledgers(campaign_id))
    banked = _banked_by_campaign(CycleEventLog.workspace_path(campaigns.workspace)).get(
        campaign_id, ZERO_SPEND
    )
    return own.plus(banked)


def sandbox_cycle_dirs(sandbox: Path) -> list[Path]:
    """Every cycle inside one L4 inner sandbox. The sandbox tree is a SIBLING of the tenant tree
    (``layout.py::inner_sandboxes_dir``), so no account-wide walk reaches it and a destroyer that
    takes one has to enumerate it here."""
    return sorted(sandbox.glob("*/campaigns/*/cycles/*")) if sandbox.is_dir() else []


def sum_user_spend(*, ledgers: Iterable[Path], since: float, until: float) -> UserSpend:
    """Both units plus the unpriceable residue, and the unreported sends beside them, over live
    usage AND banked tombstones — a tombstone is spend whose rows are gone, so it is added whole
    rather than re-priced."""
    total = ZERO_SPEND
    for ledger in ledgers:
        billable: list[dict[str, Any]] = []
        for rec in _iter_dated_records((ledger,), since=since, until=until):
            if rec.get("record_type") == "spend_tombstone":
                total = total.plus(_tombstone_of(rec))
            elif rec.get("record_type") in HOLD_TRAIL:
                billable.append(rec)
        billed, open_ = _fold_billed(billable)
        total = total.plus(billed).plus(_unreported_once_stopped(ledger, open_))
    return total


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

    What an L4 inner cycle spent that already reached its outer ledger is not banked again: those
    rows are ``mirrored``, and :func:`billed_spend` leaves them out."""
    # Unbounded on purpose: this banks everything the subject ever wrote, not a window of it.
    spent = billed_spend(CycleLayout(d).ledger for d in cycle_dirs)
    if spent == ZERO_SPEND:
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
            unreported_usd=spent.unreported_usd,
            unreported_tokens=spent.unreported_tokens,
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
    "ZERO_SPEND",
    "UserSpend",
    "account_ledgers",
    "bank_spend",
    "billed_spend",
    "campaign_spend",
    "iter_user_token_usage",
    "record_cost_usd",
    "sandbox_cycle_dirs",
    "sum_user_spend",
]
