from __future__ import annotations

import asyncio
import enum
import logging
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Annotated, Any

from pydantic import BaseModel, ValidationError

from promptpotter.shared.account_tabs import AccountTab
from promptpotter.shared.hashing import shapes_optimizer_prompt

logger = logging.getLogger(__name__)


class ErrorCategory(enum.StrEnum):
    CLIENT = "CLIENT"
    SERVER = "SERVER"
    CONNECTION = "CONNECTION"
    # Refusals no retry clears: a hole, like CONNECTION, since every later cell meets the same one.
    PROVIDER_CREDIT = "PROVIDER_CREDIT"
    PROVIDER_THROTTLED = "PROVIDER_THROTTLED"
    SPEND_CEILING = "SPEND_CEILING"
    TOKEN_CEILING = "TOKEN_CEILING"
    # Not SPEND_CEILING: the campaign's ceiling is the operator's; this is the host's coupon.
    HOST_ALLOWANCE = "HOST_ALLOWANCE"
    WALL_CLOCK = "WALL_CLOCK"
    # Never sent, so not CONNECTION: the backend the cell would have reached is untouched.
    PRICE_LIST_UNREACHABLE = "PRICE_LIST_UNREACHABLE"
    # Not SPEND_CEILING: no amount of room admits a send no rate bounds.
    NO_RATE = "NO_RATE"
    PIPELINE = "PIPELINE"
    # A bound WE declared: re-measuring ends at the same place, so a repair leaves it alone.
    HALTED = "HALTED"
    UNSCOREABLE = "UNSCOREABLE"
    UNKNOWN = "UNKNOWN"


class CellUnscoreableError(RuntimeError):
    """``spent``: ``{}`` = already ledgered or never sent; ``None`` = unknown, holds the bound."""

    category: ErrorCategory = ErrorCategory.UNSCOREABLE

    def __init__(self, message: str, *, spent: Mapping[str, Mapping[str, object]] | None) -> None:
        super().__init__(message)
        self.spent = spent


class CellHaltedError(CellUnscoreableError):
    category = ErrorCategory.HALTED


class CellInfrastructureError(CellUnscoreableError):
    category = ErrorCategory.CONNECTION


class CellThrottledError(CellUnscoreableError):
    """``BackendClient.run_query`` catches it and re-sends the cell under ``Backpressure``."""

    category = ErrorCategory.PROVIDER_THROTTLED


class CellSendRefusedError(CellInfrastructureError):
    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory,
        spent: Mapping[str, Mapping[str, object]] | None,
    ) -> None:
        super().__init__(message, spent=spent)
        self.category = category


def cell_failure(
    message: str, category: ErrorCategory, *, spent: Mapping[str, Mapping[str, object]] | None
) -> CellUnscoreableError:
    if category is ErrorCategory.PROVIDER_THROTTLED:
        return CellThrottledError(message, spent=spent)
    if category is ErrorCategory.CONNECTION:
        return CellInfrastructureError(message, spent=spent)
    return CellSendRefusedError(message, category=category, spent=spent)


# OpenRouter's three (HTTP 402 / 403) and Anthropic's, which arrives as an HTTP 400.
_PROVIDER_CREDIT_REFUSAL = re.compile(
    r"requires more credits|[Ii]nsufficient credits|Key limit exceeded|credit balance is too low"
)


def is_provider_credit_refusal(detail: str) -> bool:
    return _PROVIDER_CREDIT_REFUSAL.search(detail) is not None


class SendRefusedError(RuntimeError):
    """The run ENDS on the stop ``category`` maps to (``domain/phases.py::REFUSAL_STOPS``).

    ``tab`` is the Account tab its remedy lives in, as a KEY: each surface words it
    (`domain/account_words.py::refusal_words`), so no navigation wording rides the sentence."""

    tab: AccountTab | None = None

    def __init__(self, message: str, *, category: ErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class PotterError(Exception):
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


def _classes(of: type[PotterError]) -> dict[str, type[PotterError]]:
    found = {of.__name__: of}
    for sub in of.__subclasses__():
        found |= _classes(sub)
    return found


def raised_again(error: str, code: str, detail: str) -> PotterError:
    """The error an act raised, as its command's end kept it (class name, code, words), raised
    again by a door that answers it later: the class it was, ``PotterError`` for an error that was
    none (an internal raise)."""
    cls = _classes(PotterError).get(error, PotterError)
    again = cls.__new__(cls)
    PotterError.__init__(again, detail, code=code or None)
    return again


class BadRequestError(PotterError):
    http_status = 400
    code = "bad_request"


class UnauthorizedError(PotterError):
    http_status = 401
    code = "unauthenticated"


class NotFoundError(PotterError):
    http_status = 404
    code = "not_found"


class DatasetAccessError(NotFoundError):
    """A 404, never a 403: the existence-leak posture. Where no research tier is in reach (a wheel),
    every not-found says so alike, since a research name is not found there by construction."""

    def __init__(self, name: str, *, research_tier: bool) -> None:
        said = f"Dataset '{name}' not found"
        if not research_tier:
            said += (
                ". This install has no research tier: research definitions (an L4 panel among "
                "them) live in a source checkout's research/datasets/, which a wheel does not carry"
            )
        super().__init__(said)
        self.name = name


class ConflictError(PotterError):
    http_status = 409
    code = "version_conflict"


class DiagnosticError(PotterError):
    http_status = 409
    code = "diagnostic_refused"


class TaskUncancellableError(ConflictError):
    """A task whose act no command cancels, refused before any of its jobs is touched."""

    code = "task_uncancellable"


class IdempotencyKeyReusedError(PotterError):
    """A key a ledger already holds for another act: a retry names the act it retries."""

    http_status = 409
    code = "idempotency_key_reused"


class MachineBusyError(PotterError):
    """The holder may be the caller's OWN run, so the message stays neutral."""

    http_status = 409
    code = "machine_busy"

    def __init__(
        self,
        *,
        holding: str,
        holder_user: str,
        started_at: str | None,
    ) -> None:
        super().__init__(
            "Every run slot on this machine is taken. Try again once one finishes.",
            details={
                "holding": holding,
                "holder_user": holder_user,
                "started_at": started_at,
            },
        )


class CycleBusyError(PotterError):
    http_status = 409
    code = "cycle_busy"

    def __init__(
        self,
        *,
        job_id: str,
        status: str,
        holder_user: str,
        campaign_id: str,
        cycle_id: str,
        started_at: str | None,
    ) -> None:
        super().__init__(
            f"Cycle {cycle_id} of {campaign_id} already has a {status} run (job {job_id}, "
            f"started {started_at or 'not yet'}). Pause or cancel it, or wait for it to finish.",
            details={
                "job_id": job_id,
                "status": status,
                "holder_user": holder_user,
                "campaign_id": campaign_id,
                "cycle_id": cycle_id,
                "started_at": started_at,
            },
        )


class NoApiKeyError(SendRefusedError, PotterError):
    """Refused before the wire, so nothing is billed: neither the account nor the box holds a key."""

    http_status = 422
    code = "no_api_key"

    def __init__(self, *, provider: str, key: str) -> None:
        message = (
            f"No {provider} key is held by this account or this box, so nothing was sent. Add "
            f"your own {provider} key to this account, or the box's operator sets {key} and "
            "restarts the app."
        )
        SendRefusedError.__init__(self, message, category=ErrorCategory.CLIENT)
        self.provider = provider
        self.tab = "keys"
        self.details = {"provider": provider, "key": key}


class LaunchRefusalReason(enum.StrEnum):
    """Why a launch is refused whole: `NO_KEY` is `NoApiKeyError`'s, the rest
    `HostAllowanceExhaustedError`'s."""

    USED_UP = "used_up"
    HELD = "held"
    OVER_DECLARED = "over_declared"
    NO_KEY = "no_key"


class HostAllowanceExhaustedError(SendRefusedError, PotterError):
    """The host's coupon refuses a send that draws it: at admission, or at a send in flight, where
    it ends the run ``host_allowance``."""

    http_status = 422
    code = "host_allowance_exhausted"

    def __init__(self, facts: str, *, reason: LaunchRefusalReason, provider: str | None) -> None:
        # THE remedies, said here once: every writer of *facts* states only what binds. *provider*
        # is the one whose own key would pay instead; `None` where no key of the account's would.
        remedy = (
            "ask the host to raise this account's allowance."
            if provider is None
            else f"add your own {provider} key to this account to send on your own money."
        )
        said = (
            f"Lower the run's ceiling, or {remedy}"
            if reason is LaunchRefusalReason.OVER_DECLARED
            else f"{remedy[0].upper()}{remedy[1:]}"
        )
        SendRefusedError.__init__(self, f"{facts} {said}", category=ErrorCategory.HOST_ALLOWANCE)
        self.reason = reason
        self.provider = provider
        self.tab = None if provider is None else "keys"
        self.details = {"reason": reason, "provider": provider}


class ContentTooLargeError(PotterError):
    http_status = 413
    code = "too_large"


class PayloadInvalidError(PotterError):
    http_status = 422
    code = "payload_invalid"

    @classmethod
    def refusing(
        cls, errors: Iterable[Mapping[str, Any]], *, under: tuple[str, ...] = ()
    ) -> PayloadInvalidError:
        """*under* locates a model validated inside a larger payload."""
        found = _located(errors, under)
        return cls(_said(found, shown=len(found)), details={"errors": found})

    @classmethod
    def validating[M: BaseModel](
        cls, model: type[M], fields: object, *, under: tuple[str, ...] = ()
    ) -> M:
        """A payload a terminal or a host built, refused in the words the REST body's 422 carries."""
        try:
            return model.model_validate(fields)
        except ValidationError as exc:
            raise cls.refusing(exc.errors(), under=under) from exc


class SlugTakenError(ConflictError):
    """A new definition's name a tier already holds, refused before anything is kept."""

    def __init__(self, slug: str, suggested: str) -> None:
        super().__init__(
            f"A dataset named '{slug}' already exists. Suggested slug: {suggested}.",
            code="slug_collision",
            details={"slug": slug, "suggested_slug": suggested},
        )
        self.slug = slug
        self.suggested = suggested


def _located(errors: Iterable[Mapping[str, Any]], under: tuple[str, ...]) -> list[dict[str, Any]]:
    """Each failing field by its path and rule, never its value (a refused field may be a secret);
    a validator's own sentence where one raised."""
    return [
        {
            "loc": [*under, *(str(part) for part in error["loc"])],
            "msg": str(error["ctx"]["error"] if error["type"] == "value_error" else error["msg"]),
        }
        for error in errors
    ]


def _said(found: list[dict[str, Any]], *, shown: int) -> str:
    said = "; ".join(
        f"{'.'.join(e['loc'])}: {e['msg']}" if e["loc"] else e["msg"] for e in found[:shown]
    )
    return said + (f"; and {len(found) - shown} more" if len(found) > shown else "")


def validation_reason(exc: ValidationError, *, shown: int) -> str:
    return _said(_located(exc.errors(include_input=False, include_url=False), ()), shown=shown)


class ServiceUnavailableError(PotterError):
    http_status = 503
    code = "service_unavailable"


class FileKind(enum.Enum):
    """What a file the store refuses whole IS: its code, status and remedy, said here once."""

    def __init__(self, code: str, http_status: int, remedy: str) -> None:
        self.code = code
        self.http_status = http_status
        self.remedy = remedy

    STORE = ("file_unreadable", 409, "No reader repairs it.")
    BANK = (
        "bank_unreadable",
        409,
        "No reader repairs it: delete it, and a dataset with a source re-fetches it on its next "
        "start.",
    )
    EXPORT = ("export_unreadable", 409, "An export is disposable: re-run the cycle to export it.")
    # Never migrated: the run data it belongs to is wiped whole.
    MANIFEST = (
        "manifest_unreadable",
        409,
        "No migration exists: `python -m promptpotter reset` wipes it with the rest of the run "
        "data.",
    )
    JOB = ("job_unreadable", 409, "No migration exists: delete it once no run it names is live.")
    # 500, not 422: the request was fine and the caller cannot fix a file the server persisted.
    STORED_CONFIG = ("stored_config_invalid", 500, "Fix the file.")
    # An optimizer prompt's manifest, the install's or the operator's copy shadowing it.
    OPTIMIZER_PROMPT = (
        "optimizer_prompt_invalid",
        500,
        "Fix its template: it fills only the slots its node declares.",
    )


class FileUnreadableError(PotterError):
    """THE refusal of a file that is not a ledger, carrying its path and its `FileKind`: no reader
    yields its bytes as a dict or reads past them. Never a `ValueError`: a survey reads this type
    by name and lists its one subject unreadable, and every other door answers it as itself."""

    def __init__(self, kind: FileKind, *, path: str, reason: str) -> None:
        self.kind = kind
        super().__init__(
            f"{path} is unreadable by this build ({reason}). {kind.remedy}",
            details={"path": path, "reason": reason},
        )

    @property
    def code(self) -> str:  # type: ignore[override]  # derived from `kind`, its one holder
        return self.kind.code

    @property
    def http_status(self) -> int:  # type: ignore[override]
        return self.kind.http_status


class LedgerUnreadableError(ConflictError):
    """A ledger is run data, never migrated: a line this build cannot read refuses every reader of its kind."""

    code = "ledger_unreadable"

    def __init__(
        self, *, ledger: str, offset: int | None, reason: str, campaign_id: str | None
    ) -> None:
        """*offset* ``None``: the ledger lacks a record every reader of it relies on."""
        remedy = (
            "no verb repairs it"
            if campaign_id is None
            else f"`python -m promptpotter delete {campaign_id}` removes its campaign and keeps "
            "every measurement cache"
        )
        where = ledger if offset is None else f"{ledger} line {offset}"
        super().__init__(
            f"{where} is unreadable by this build ({reason}). No migration exists: {remedy}.",
            details={
                "ledger": ledger,
                "offset": offset,
                "reason": reason,
                "campaign_id": campaign_id,
            },
        )


class UngeneratedRequestError(RuntimeError):
    """A request refused before any generation: terminal, never resent, never billed."""


class RequestTooLargeError(UngeneratedRequestError):
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
            f"`promptpotter/assets/optimizers/potter/pipeline.yaml` at a tier whose per-minute cap "
            f"exceeds {requested} tokens (e.g. OpenRouter, or a paid Groq tier) — "
            f"the free Groq on_demand tier caps at {limit}\n"
            f"  - or shorten the optimizer prompt (the dataset's task.md)."
        )


class ContextOverflowError(UngeneratedRequestError):
    """The provider refused the prompt as past the model's window."""

    def __init__(self, *, provider_name: str, said: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"{provider_name}: the prompt exceeds the model's context window: {said}")


class ResumeDivergenceError(RuntimeError):
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


class PromptCompositionError(Exception):
    """The run halts with ``RENDER_ERROR`` rather than sending a degraded prompt."""


class OptimizerTimeoutError(TimeoutError):
    """The run halts with ``OPTIMIZER_TIMEOUT``; any other timeout is a crash."""


@shapes_optimizer_prompt
def is_error_result(result: Mapping[str, Any]) -> bool:
    """``predicted == "ERROR"`` is a display token and ``error`` a message: neither detects."""
    return result.get("error_category") is not None


@contextmanager
def graceful(msg: str) -> Iterator[None]:
    try:
        yield
    except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
        raise
    except Exception:
        logger.exception(msg)


@shapes_optimizer_prompt
def error_category(result: Mapping[str, Any]) -> ErrorCategory | None:
    cat = result.get("error_category")
    if cat is None:
        return None
    if isinstance(cat, ErrorCategory):
        return cat
    try:
        return ErrorCategory(cat)
    except ValueError:
        return None


ERROR_IS_CHARGED: Annotated[dict[ErrorCategory, bool], shapes_optimizer_prompt] = {
    ErrorCategory.CLIENT: True,
    ErrorCategory.SERVER: False,
    ErrorCategory.CONNECTION: False,
    ErrorCategory.PROVIDER_CREDIT: False,
    ErrorCategory.PROVIDER_THROTTLED: False,
    ErrorCategory.SPEND_CEILING: False,
    ErrorCategory.TOKEN_CEILING: False,
    ErrorCategory.HOST_ALLOWANCE: False,
    ErrorCategory.WALL_CLOCK: False,
    ErrorCategory.PRICE_LIST_UNREACHABLE: False,
    ErrorCategory.NO_RATE: False,
    ErrorCategory.PIPELINE: True,
    ErrorCategory.HALTED: True,
    ErrorCategory.UNSCOREABLE: False,
    ErrorCategory.UNKNOWN: True,
}
assert set(ERROR_IS_CHARGED) == set(ErrorCategory), "every ErrorCategory must take a side"
