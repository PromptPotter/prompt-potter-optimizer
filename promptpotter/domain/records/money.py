"""Money on a ledger: a hold and its charge-book lines, one answered send, and a cycle's budget and share."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import (
    Field,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from promptpotter.domain.records import CycleRecord
from promptpotter.domain.spend import (
    NESTED_NODE_PREFIX,
    Budget,
    KeySource,
    TokenUsageKind,
    bill_or_rate_usd,
    cycle_axes_only,
    money_axes_only,
)
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.measurement_context import MeasurementRole


class CallPrice(FrozenModel):
    """What one call cost, the one carrier of a call's price: on a send's charge, or a replay's own."""

    kind: TokenUsageKind
    # ``None`` where neither the send nor its reply named one, as on a backend cell.
    model: str | None
    # Who BILLED: a rate belongs to the (provider, model) pair; ``None`` ⇒ no rate prices the call.
    provider: str | None
    # The BILL the provider reported, never filled from a rate; ``None`` on a hold, whose money is its bound.
    cost_usd: float | None
    # OUR rate's price of an unbilled call, stamped once: it counts against ceilings, never spent.
    rate_priced_usd: float | None

    @property
    def bill_or_rate_usd(self) -> float | None:
        return bill_or_rate_usd(self.cost_usd, self.rate_priced_usd)


class Hold(CycleRecord):
    """A hold, filed on the book of what it holds: on the account's charge book a run's or a call's
    money, open until a bill or a release closes it (a later hold of the same id restates its
    bound); on a cycle's ledger a job's claim of that cycle, until its run declares itself or the
    job dies (`infrastructure/holders.py`)."""

    record_type: Literal["hold"] = "hold"
    hold_id: str
    # The hold it draws on: a call's its run's, an inner run's its enclosing run's; ``None`` draws
    # on the account alone.
    parent: str | None
    key_source: KeySource
    # The most it and every hold under it may draw; ``None`` on an axis nothing bounds.
    bound: Budget
    # The kind a call's or a cell's hold binds, as its run's share files it; ``None`` on a job's.
    resource: TokenUsageKind | None
    # The node a call or a cell sends for; ``None`` on a job's.
    node: str | None
    # The ledger a call's sends land on, relative to the account; ``None`` on a job's or a cell's.
    ledger: str | None
    timestamp: str = Field(default_factory=utcnow_iso)

    @classmethod
    def of_job(cls, job_id: str, bound: Budget, *, parent: str | None) -> Hold:
        """A job's own hold, its id the job's: its sends draw on the box's key unless a call's says."""
        return cls(
            hold_id=job_id,
            parent=parent,
            key_source="host",
            bound=bound,
            resource=None,
            node=None,
            ledger=None,
        )


class Bill(FrozenModel):
    """One answered part of a call, which closes the call's hold."""

    record_type: Literal["bill"] = "bill"
    hold_id: str
    key_source: KeySource
    # Both money fields ``None``: unpriced.
    price: CallPrice
    tokens: int
    timestamp: str = Field(default_factory=utcnow_iso)


class Release(FrozenModel):
    """A hold closed with nothing more billed on it: a send never made, a run ended."""

    record_type: Literal["release"] = "release"
    hold_id: str
    timestamp: str = Field(default_factory=utcnow_iso)


class Lapse(FrozenModel):
    """A send that may have billed and reported nothing: it closes its call's hold, and *bound* is
    what it may have cost, drawn on every hold above it and unreported at once."""

    record_type: Literal["lapse"] = "lapse"
    hold_id: str
    bound: Budget
    timestamp: str = Field(default_factory=utcnow_iso)


# One line of the account's charge book (`store/wallet_store.py::ChargeBook`).
ChargeLine = Annotated[Hold | Bill | Release | Lapse, Field(discriminator="record_type")]


class RootFiling(FrozenModel):
    """How an L4 outer files a send its inner run filed: the kind of the pass that spawned the
    run, in the outer's round."""

    kind: TokenUsageKind
    round: int | None


class TokenUsageRecord(CycleRecord):
    """ONE answered send, or a replay; a send that reported no usage writes none."""

    record_type: Literal["token_usage"] = "token_usage"
    node: str
    # WHICH upstream host answered, where the price's provider is a gateway; ``None`` where it is.
    served_by: str | None = None
    input_tokens: int
    output_tokens: int
    # `TokenAccount`'s parts: ``None`` is no breakdown reported. A SUBSET of ``output_tokens``.
    reasoning_tokens: int | None = None
    # A SUBSET of ``input_tokens`` the PROVIDER cached. Not a replay.
    cache_read_tokens: int | None = None
    # The part of ``input_tokens`` billed at a premium to POPULATE that cache.
    cache_write_tokens: int | None = None
    duration_s: float = 0.0
    # A send's bill; a replay's recorded price alone, which no wallet paid.
    money: Bill | CallPrice
    # A nested run's send, filed once on its own ledger: how its root's folds file it.
    root_filing: RootFiling | None = None
    round: int | None = None
    # ``None`` outside a candidate's pass; a nested run's copy keeps ITS pass: `by_role` skips it.
    role: MeasurementRole | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    @property
    def bill(self) -> Bill | None:
        """``None`` exactly on a replay."""
        return self.money if isinstance(self.money, Bill) else None

    @property
    def price(self) -> CallPrice:
        return self.money.price if isinstance(self.money, Bill) else self.money

    def filed_as(self, kind: TokenUsageKind) -> TokenUsageRecord:
        price = self.price.model_copy(update={"kind": kind})
        money = price if self.bill is None else self.money.model_copy(update={"price": price})
        return self.model_copy(update={"money": money})

    def filed_by_root(self) -> TokenUsageRecord:
        """This nested send as its root's folds read it: its kind and round, its node marked."""
        if (filing := self.root_filing) is None:
            raise ValueError(f"{self.node}: a send its own run filed has no root filing")
        return self.filed_as(filing.kind).model_copy(
            update={
                "node": f"{NESTED_NODE_PREFIX}{self.node}",
                "round": filing.round,
                "root_filing": None,
            }
        )

    @property
    def spend_round(self) -> int:
        """A call carrying no round ran before any closed (init, the origin score): banks at 0."""
        return 0 if self.round is None else self.round


class PricedKeyRecord(CycleRecord):
    """A key the search has PRICED, so a later read of it, resumed or not, prices nothing again."""

    record_type: Literal["priced_key"] = "priced_key"
    priced_key: str
    timestamp: str = Field(default_factory=utcnow_iso)


class StandingBudgetRecord(CycleRecord):
    """A patch of a cycle's standing budget: it names the fields it changes and the rest stand, a
    lift spelled ``unbounded`` (`Budget`'s rule). Folded in order of arrival, PHYSICALLY, so a fork never
    inherits one."""

    forks_inherit: ClassVar[bool] = False
    record_type: Literal["standing_budget"] = "standing_budget"
    # The cycle axes an operator named; the money axes are the campaign's (`CampaignCeilingRecord`).
    budget: Budget = Budget()
    # RUN-scoped: the closed-round count at which the launch in flight pauses (``start-run {step_rounds}``).
    pause_at_round: int | None = None
    # On: a call holds its node's mean billed cost in the run, never its worst case (`SpendBook.expected`).
    overshoot: bool = False
    timestamp: str = Field(default_factory=utcnow_iso)

    _cycle_axes = field_validator("budget")(cycle_axes_only)

    @model_serializer(mode="wrap")
    def _named_only(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """An unnamed field written out would read back as a change nobody asked for."""
        data: dict[str, object] = handler(self)
        return {k: v for k, v in data.items() if k not in _PATCHED or k in self.model_fields_set}

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """The wire omits what the input omits, so both directions are the fields' one schema."""
        return handler({key: value for key, value in core.items() if key != "serialization"})

    def over(self, patch: StandingBudgetRecord) -> StandingBudgetRecord:
        """*patch* laid on this fold: its named fields win, its budget's named axes over this one's."""
        named = {
            field: getattr(layer, field)
            for layer in (self, patch)
            for field in _PATCHED & layer.model_fields_set
        }
        return StandingBudgetRecord(budget=self.budget.over(patch.budget), **named)


_PATCHED = frozenset({"pause_at_round", "overshoot"})


class CampaignCeilingRecord(CycleRecord):
    """The money axes an operator named for the whole campaign (`set-limits`), LAST wins on its ROOT's
    ledger, so every cycle of it reads one; every move lands through `jobs/shares.py::move_campaign_money`."""

    record_type: Literal["campaign_ceiling"] = "campaign_ceiling"
    budget: Budget
    # What the operator asked of each axis `budget` names; lower there only where the account's
    # allowance clamped it (`quota.py::clamped_change`).
    asked: Budget
    timestamp: str = Field(default_factory=utcnow_iso)

    _money_axes = field_validator("budget", "asked")(money_axes_only)


class CycleShareRecord(CycleRecord):
    """The cycle's share of its campaign's ceiling, LAST wins and never inherited; every move lands through `hold_moved`."""

    forks_inherit: ClassVar[bool] = False
    record_type: Literal["cycle_share"] = "cycle_share"
    # The most the cycle may have metered of its campaign's ceiling, in all.
    allotment: Budget
    timestamp: str = Field(default_factory=utcnow_iso)
