"""Free helpers + internal signal types for :class:`CommandDispatcher`.

Pure, dependency-light pieces carved off the dispatcher so the class file
holds only the dispatch / record / apply pipeline:

- ``_DeleteCycleRejectedError`` — internal signal raised by the delete-cycle
  applier when ``try_delete_stub_cycle`` refuses.
- ``_IdempotentMatch`` — the prior-command hit returned by the ledger scan.
- ``_optional_float`` / ``_parse_cycle_seed`` / ``_slugify_backend_id`` —
  payload coercion for the launcher / fork / register-backend commands.
- ``_find_idempotent_command`` — the ``Idempotency-Key`` dedupe scan.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ValidationError

from promptpotter.domain.run_records import CommandRecord, CycleSeed
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.shared.errors import PayloadInvalidError


class _DeleteCycleRejectedError(Exception):
    """Internal signal: ``try_delete_stub_cycle`` returned ``(False, reason)``.

    ``_record_and_apply`` translates this into a rejected ack + 409 response
    so the audit trail stays on the ledger and the caller gets the guard's
    reason in the error envelope."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _IdempotentMatch(BaseModel):
    model_config = {"frozen": True}
    command_id: str
    offset: int


def _optional_float(raw: object) -> float | None:
    """Best-effort cast for optional `mint-campaign` / `start-run` knobs."""
    if isinstance(raw, int | float):
        return float(raw)
    return None


def _parse_cycle_seed(raw: object) -> CycleSeed:
    """Validate the required ``fork-cycle`` seed into a typed :class:`CycleSeed`.

    Every operator fork is `operator_steered` and carries a seed (the edited
    searchpoint + reconciled limits). The wire payload is always a fork, so the
    C0 lineage provenance is stamped ``origin_source="fork_seed"`` here (the wire
    schema `OperatorForkOverride` doesn't carry it). A missing or malformed seed
    is a 422 (the typed schema is the contract; `ConfigOverrides` bounds ride
    `m12-api-openapi.yaml`)."""
    if not isinstance(raw, dict):
        raise PayloadInvalidError("payload.seed (object) is required.")
    try:
        return CycleSeed.model_validate({**raw, "origin_source": "fork_seed"})
    except ValidationError as exc:
        raise PayloadInvalidError(f"payload.seed invalid: {exc}") from exc


def _slugify_backend_id(name: str) -> str:
    """Lowercase + collapse non-alphanumerics into hyphens; strip ends.

    Mirrors the auto-derivation that ``POST /backends`` used pre-migration;
    documented in ``RegisterBackendPayload.id`` ("auto-derived from `name`
    when omitted")."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")


def _find_idempotent_command(
    ledger: CycleEventLog, idempotency_key: str
) -> _IdempotentMatch | None:
    """Scan the ledger for a prior ``CommandRecord`` with the same key.

    O(n) over the cycle ledger; acceptable for the lifecycle + sanctioned-POST
    paths (a cycle ledger holds thousands of records, not millions, and
    commands are operator-paced)."""
    for offset, record in enumerate(ledger.iter()):
        if isinstance(record, CommandRecord) and record.idempotency_key == idempotency_key:
            return _IdempotentMatch(command_id=record.command_id, offset=offset)
    return None
