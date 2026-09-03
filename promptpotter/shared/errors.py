from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


class ErrorCategory(enum.StrEnum):
    CLIENT = "CLIENT"
    SERVER = "SERVER"
    CONNECTION = "CONNECTION"
    PIPELINE = "PIPELINE"
    UNKNOWN = "UNKNOWN"


class PotterError(Exception):
    """Base for every API error — ONE flat wire envelope at ONE seam, the single FastAPI handler.
    A subclass fixes the status FAMILY; ``code`` is the stable string, overridable per raise site."""

    http_status: int = 500
    code: str = "internal_error"

    def __init__(
        self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}

    @property
    def message(self) -> str:
        return str(self)


class BadRequestError(PotterError):
    """Syntactically/semantically bad request the route rejects up front — 400."""

    http_status = 400
    code = "bad_request"


class UnauthorizedError(PotterError):
    """No valid identity on a route that requires sign-in — 401."""

    http_status = 401
    code = "unauthenticated"


class NotFoundError(PotterError):
    """Target resource doesn't exist (or isn't visible to the caller) — 404."""

    http_status = 404
    code = "not_found"


class ConflictError(PotterError):
    """Request conflicts with current state (slug taken, version clash) — 409."""

    http_status = 409
    code = "version_conflict"


class MachineBusyError(PotterError):
    """Every run slot on the machine is taken — 409, carrying the oldest live run as the holder.
    Occupancy is not relative to who asks, so the holder may be the caller's OWN run and the
    message stays neutral."""

    http_status = 409
    code = "machine_busy"

    def __init__(
        self,
        *,
        holder_user: str,
        campaign_id: str,
        cycle_id: str,
        started_at: str | None,
    ) -> None:
        super().__init__(
            "Every run slot on this machine is taken. Try again once one finishes.",
            details={
                "holder_user": holder_user,
                "campaign_id": campaign_id,
                "cycle_id": cycle_id,
                "started_at": started_at,
            },
        )


class ContentTooLargeError(PotterError):
    """Request/target exceeds a hard size cap (too many file entries) — 413."""

    http_status = 413
    code = "too_large"


class PayloadInvalidError(PotterError):
    """Well-formed request that fails a business/shape rule — 422."""

    http_status = 422
    code = "payload_invalid"


class ServiceUnavailableError(PotterError):
    """A required dependency isn't ready (job registry, draft registry) — 503."""

    http_status = 503
    code = "service_unavailable"


class StoredConfigInvalidError(PotterError):
    """A config file the server itself persisted no longer loads — 500, because the request was fine
    and the caller cannot fix it. ``path`` and ``reason`` are required; the remedy is ``restamp``."""

    http_status = 500
    code = "stored_config_invalid"

    def __init__(self, *, path: str, reason: str) -> None:
        super().__init__(
            f"{path} is no longer readable by this build: {reason}",
            details={"path": path, "reason": reason},
        )


class RequestTooLargeError(RuntimeError):
    """A single LLM request exceeds the provider's per-minute token cap. Terminal — retrying will
    not help, so the caller surfaces the message without a traceback."""

    def __init__(
        self,
        *,
        provider_name: str,
        limit: int,
        requested: int,
    ) -> None:
        self.provider_name = provider_name
        self.limit = limit
        self.requested = requested
        super().__init__(
            f"{provider_name}: single request exceeds tier TPM cap "
            f"(limit={limit}, requested={requested}). Retrying will not help — "
            f"the request alone is larger than the per-minute token allowance.\n"
            f"This is one optimizer (L1) call; n_variants is the per-round candidate "
            f"count (parallel calls), NOT a single-request lever — lowering it does "
            f"not shrink this request. Biggest lever first:\n"
            f"  - point the optimizer node `provider` in "
            f"`promptpotter/assets/optimizer/pipeline.yaml` at a tier whose per-minute cap "
            f"exceeds {requested} tokens (e.g. OpenRouter, or a paid Groq tier) — "
            f"the free Groq on_demand tier caps at {limit}\n"
            f"  - or shorten the optimizer prompt (task_description.md)."
        )


class RulerCoverageError(PotterError):
    """A θ was asked for on a warm δ ruler that does not carry the cell.

    Coverage is a POSTCONDITION of ``Cycle.calibrate_ruler``, so this is an engine fault and never
    a data condition: it means a measured cell reached a θ read without passing the extension seam.
    Deliberately caught nowhere — it must surface as a crashed cycle with a traceback, because the
    only quiet alternative is a δ=0 default, which reads an off-ruler cell as easier than anything
    ever measured and depresses every θ downstream of it.
    """

    code = "ruler_coverage"

    def __init__(self, missing: Sequence[int], *, anchor_id: str = "") -> None:
        shown = ", ".join(str(sid) for sid in list(missing)[:10])
        more = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
        super().__init__(
            f"δ ruler {anchor_id or '<unknown>'} does not carry {len(missing)} measured "
            f"sample(s): {shown}{more}. The round was scored on cells the ruler never absorbed.",
            details={"missing_sample_ids": list(missing)[:50], "anchor_id": anchor_id},
        )


class RulerUnpersistedError(PotterError):
    """This cycle's rounds were read on a WARM δ ruler that its ledger cannot reproduce.

    ``write_ruler`` appends the ``RulerRecord`` BEFORE the round document that names it, so a
    live run cannot reach this: it means the ledger was truncated, or the rounds predate the
    record existing at all. Resuming would re-derive a ruler from an archive that has grown since
    the lock, putting a second scale under one cycle — two ``ruler_id``s across its rounds, and
    every θ read on the later one incomparable with the earlier. Refuse, and name the campaign.
    """

    code = "ruler_unpersisted"

    def __init__(self, stamped_id: str, *, campaign_id: str, cycle_id: str) -> None:
        super().__init__(
            f"cycle {cycle_id} (campaign {campaign_id}) stamps δ ruler {stamped_id} on its "
            "rounds but its ledger holds no RulerRecord, so those θ cannot be reproduced and "
            "resuming would silently continue on a different scale. Start a fresh campaign; the "
            "measurement archive is content-addressed and re-scores its cells from cache.",
            details={"ruler_id": stamped_id, "campaign_id": campaign_id, "cycle_id": cycle_id},
        )


class ResumeDivergenceError(RuntimeError):
    """A recorded decision re-derives differently under the active scorer: every decision is a pure
    function of scored results. Branch with ``resume --fork-on-divergence``, or revert the formula."""

    def __init__(
        self,
        *,
        round_num: int,
        kind: str,
        recorded_outcome: Any,
        current_outcome: Any,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        self.round_num = round_num
        self.kind = kind
        self.recorded_outcome = recorded_outcome
        self.current_outcome = current_outcome
        self.diagnostics = diagnostics or {}
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [
            f"Resume divergence at round {self.round_num}, decision {self.kind!r}:",
            f"  recorded: {self.recorded_outcome}",
            f"  current:  {self.current_outcome}",
        ]
        for k, v in self.diagnostics.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


class DatasetIdentityError(RuntimeError):
    """The rows under this dataset name changed. ``sample_id`` is a POSITION within a name, so the
    archive can only answer "what was measured at slot N" — replaying it would attribute a score to a
    question that did not produce it, with no error anywhere. Re-cut rows need a new name."""

    _PREVIEW = 160

    def __init__(
        self,
        *,
        dataset_name: str,
        sample_id: int,
        stored: tuple[str, str],
        current: tuple[str, str],
    ) -> None:
        self.dataset_name = dataset_name
        self.sample_id = sample_id
        lines = [
            f"Dataset {dataset_name!r}: sample_id {sample_id} was measured against different "
            "content than the row now sitting at that position.",
        ]
        for field, was, now in (
            ("query", stored[0], current[0]),
            ("ground_truth", stored[1], current[1]),
        ):
            if was != now:
                lines.append(f"  {field} measured: {was[: self._PREVIEW]!r}")
                lines.append(f"  {field} now:      {now[: self._PREVIEW]!r}")
        lines.append(
            "The measurement cache keys on (dataset_name, node_configs, sample_id) — the query "
            "text is not in the key, so serving these priors would carry a score for one question "
            "onto another. Cut the changed rows under a NEW dataset name; the old name keeps its "
            "measurements and stays replayable."
        )
        super().__init__("\n".join(lines))


def is_error_result(result: Mapping[str, Any]) -> bool:
    """Detection rides the typed ``error_category`` channel — the single owner of "this sample
    errored". ``predicted == "ERROR"`` is a display token, and ``error`` a human message."""
    return result.get("error_category") is not None


def has_pipeline_warnings(result: Mapping[str, Any]) -> bool:
    """A sample carries pipeline warnings iff ``pipeline_data.diagnostics.warnings`` is non-empty —
    the sibling of :func:`is_error_result`, where the backend failed outright."""
    return bool((result.get("pipeline_data") or {}).get("diagnostics", {}).get("warnings"))


@contextmanager
def graceful(msg: str) -> Iterator[None]:
    """Suppress non-interrupt exceptions with a log message. ``KeyboardInterrupt`` and
    ``asyncio.CancelledError`` re-raise, so graceful shutdown is never swallowed."""
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception:
        logger.warning(msg, exc_info=True)


def error_category(result: Mapping[str, Any]) -> ErrorCategory | None:
    """Read the typed error category off a measurement, ``None`` when clean. Tolerates the on-disk
    round-trip form, where a persisted row carries the bare ``StrEnum`` value as a plain ``str``."""
    cat = result.get("error_category")
    if cat is None:
        return None
    if isinstance(cat, ErrorCategory):
        return cat
    try:
        return ErrorCategory(cat)
    except ValueError:
        return None


__all__ = [
    "BadRequestError",
    "ConflictError",
    "ContentTooLargeError",
    "DatasetIdentityError",
    "ErrorCategory",
    "MachineBusyError",
    "NotFoundError",
    "PayloadInvalidError",
    "PotterError",
    "RequestTooLargeError",
    "ResumeDivergenceError",
    "RulerCoverageError",
    "RulerUnpersistedError",
    "ServiceUnavailableError",
    "StoredConfigInvalidError",
    "UnauthorizedError",
    "error_category",
    "graceful",
    "has_pipeline_warnings",
    "is_error_result",
]
