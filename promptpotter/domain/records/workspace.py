"""Records on the WORKSPACE ledger: a deleted cycle's tombstones, a probe's health, an account's events."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from promptpotter.domain.cycle_paths import CycleHop, CyclePath, Subject
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.records.run_phase import Ended, Paused
from promptpotter.domain.spend import Budget, CeilingMeter, KeySource, UserSpend
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import utcnow_iso


class BankedSpend(FrozenModel):
    """One wallet's `UserSpend`, as a tombstone banks it."""

    used_usd: float
    # What our rate table priced the calls no provider billed (`CallPrice.rate_priced_usd`).
    rate_priced_usd: float
    used_tokens: int
    unpriced_tokens: int

    @classmethod
    def of(cls, spent: UserSpend) -> BankedSpend:
        """Bills alone: an unreported send's cost stays on the account (`Lapse`), past any delete."""
        return cls(
            used_usd=spent.used_usd,
            rate_priced_usd=spent.rate_priced_usd,
            used_tokens=spent.used_tokens,
            unpriced_tokens=spent.unpriced_tokens,
        )

    @property
    def spend(self) -> UserSpend:
        return UserSpend(**self.model_dump())


class SpendTombstoneRecord(CycleRecord):
    """On the WORKSPACE ledger, so deleting a campaign's cycle cannot re-earn its ceiling; the
    account's charges are `store/wallet_store.py::ChargeBook`'s, which no delete reaches."""

    record_type: Literal["spend_tombstone"] = "spend_tombstone"
    # Its subject's address: a cycle, or a whole campaign (no cycle), in its sandbox chain.
    campaign_id: str
    cycle_id: str = ""
    inside: CyclePath = ()
    # By whose key paid it, so the campaign's row keeps what its deleted cycles cost.
    spent: dict[KeySource, BankedSpend]
    # What the campaign's ceiling counted of it in each meter, so a bank needs no manifest to name one.
    metered: dict[CeilingMeter, Budget]
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def subject(self) -> Subject:
        return Subject(self.campaign_id, self.cycle_id, inside=self.inside)


class BankedStop(FrozenModel):
    """A cycle's abnormal run stop, banked before the cycle went, worded where it is read; its
    commands are the account log's."""

    # The cycle, below its tombstone's sandbox chain.
    hop: CycleHop
    state: Annotated[Paused | Ended, Field(discriminator="run_phase")]
    at: str


class TrailTombstoneRecord(CycleRecord):
    """On the WORKSPACE ledger, so deleting a cycle cannot delete that its run stopped."""

    record_type: Literal["trail_tombstone"] = "trail_tombstone"
    # Its subject's address, as `SpendTombstoneRecord`'s.
    campaign_id: str
    cycle_id: str = ""
    inside: CyclePath = ()
    stops: tuple[BankedStop, ...]
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def subject(self) -> Subject:
        return Subject(self.campaign_id, self.cycle_id, inside=self.inside)


AccountEvent = Literal["uploaded", "request_refused", "operator_notified"]


HealthState = Literal["held", "broken", "unverified"]


# Who read a standing: its probe, or the admin bot's poll, whose refusal stands until a poll answers.
HealthReader = Literal["probe", "poll"]


class HealthRecord(CycleRecord):
    """On the box's own WORKSPACE ledger: a probe's state CHANGED. ``unverified`` never is one."""

    record_type: Literal["health"] = "health"
    probe: str
    state: Literal["held", "broken"]
    said: str
    by: HealthReader = "probe"
    timestamp: str = Field(default_factory=utcnow_iso)


class AccountEventRecord(CycleRecord):
    """On the WORKSPACE ledger; ``user_id`` is the acting principal, a delegate's own id in its delegator's tenant."""

    record_type: Literal["account_event"] = "account_event"
    event: AccountEvent
    user_id: str
    # The file an upload was kept as, what a refused request asked and was told.
    subject: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)
