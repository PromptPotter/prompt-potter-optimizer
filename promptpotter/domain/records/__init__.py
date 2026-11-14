"""The ledger record base: a module here declares its arms, and `record_kinds` walks them in."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.domain.kinds import KindTable
from promptpotter.domain.strict_model import FrozenModel


class CycleRecord(FrozenModel):
    """One ledger line, registered under its ``record_type``; a base declaring none registers nothing."""

    # A fork reads its parent's records below its cut, unless the record is its own cycle's alone.
    forks_inherit: ClassVar[bool] = True

    if TYPE_CHECKING:
        # Each arm declares both where its own field order puts them, which is the order on disk.
        record_type: str
        timestamp: str

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if (field := cls.model_fields.get("record_type")) is not None:
            RECORDS.register(field.default, cls)


RECORDS: KindTable[CycleRecord] = KindTable("record")
