"""Every ledger record kind: a module here declares its arms, found by walking this package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.domain.kinds import KindTable
from promptpotter.domain.strict_model import FrozenModel


class CycleRecord(FrozenModel):
    """One ledger line, registered under its ``record_type``; a base declaring none registers nothing."""

    if TYPE_CHECKING:
        # Each arm declares both where its own field order puts them, which is the order on disk.
        record_type: str
        timestamp: str

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if (field := cls.model_fields.get("record_type")) is not None:
            RECORDS.register(field.default, cls)


RECORDS: KindTable[CycleRecord] = KindTable("record", walk=__name__)
