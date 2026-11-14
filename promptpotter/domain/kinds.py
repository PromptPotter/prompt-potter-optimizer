from __future__ import annotations

from pydantic import BaseModel


class RegistryIncompleteError(RuntimeError):
    """A wiring fault, so never a ``ValueError``: ``ledger_record`` would refuse the line as unreadable data."""


class KindTable[M: BaseModel]:
    """The ONE kind -> model table: a model registers as its class is built, and every read by kind
    goes through ``parse``. A table whose package walks its modules at import completes there;
    one without completes at its declared step."""

    def __init__(self, what: str) -> None:
        self._what = what
        self._held: dict[str, type[M]] = {}
        self._complete = False

    def register(self, kind: str, model: type[M]) -> None:
        if (held := self._held.setdefault(kind, model)) is not model:
            raise TypeError(
                f"{self._what} kind {kind!r} is registered twice: {held.__name__}, {model.__name__}"
            )

    def complete(self) -> None:
        """Every package declaring a kind has loaded: a kind still unknown is data no installed code writes."""
        self._complete = True

    def registered(self) -> dict[str, type[M]]:
        if not self._complete:
            raise RegistryIncompleteError(
                f"the {self._what} kinds were asked before the registries completed"
            )
        return dict(sorted(self._held.items()))

    def schemas(self) -> dict[str, object]:
        """Every kind's shape, so a reshape of any moves what hashes them (`landing.py::landing_key`)."""
        return {kind: model.model_json_schema() for kind, model in self.registered().items()}

    def parse(self, kind: str, raw: object) -> M:
        """A ledger line's bytes, or a banked part's mapping, read as the model *kind* names."""
        if (held := self._held.get(kind)) is None:
            if not self._complete:
                raise RegistryIncompleteError(
                    f"{self._what} kind {kind!r} was read before this process completed its "
                    "registries (`land_at_entry`)"
                )
            raise ValueError(f"no installed package declares the {self._what} kind {kind!r}")
        return held.model_validate_json(raw) if isinstance(raw, bytes) else held.model_validate(raw)
