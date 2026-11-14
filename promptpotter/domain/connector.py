from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol, get_args

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.said import Tone
from promptpotter.shared.errors import PotterError
from promptpotter.shared.extras import MissingExtraError
from promptpotter.shared.hashing import shapes_optimizer_prompt
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    import httpx

    from promptpotter.domain.sample import Sample


ConnectorExecution = Literal["remote_http", "in_process"]

MeasuredUnit = Literal["sample", "cell"]


@shapes_optimizer_prompt
def unit_plural(unit: MeasuredUnit) -> str:
    return f"{unit}s"


@shapes_optimizer_prompt
def unit_count(n: int, unit: MeasuredUnit) -> str:
    return f"{n} {unit if n == 1 else unit_plural(unit)}"


class BackendUnreachableError(PotterError):
    http_status = 503
    code = "backend_unreachable"

    def __init__(self, backend_type: str, backend_url: str, detail: str = "") -> None:
        self.backend_type = backend_type
        self.backend_url = backend_url
        self.detail = detail
        # A connector that DIAGNOSED the fault leads with it; the URL names a port nothing serves for an in-process one.
        super().__init__(
            f"Backend '{backend_type}' is not ready: {detail}"
            if detail
            else f"Backend '{backend_type}' at {backend_url} is not reachable. "
            f"Start the backend and try again.",
            details={"backend_type": backend_type, "backend_url": backend_url},
        )


#: `missing_extra` is this install's defect, `unreachable` the backend's.
BackendReachability = Literal["live", "unreachable", "missing_extra"]


class BackendReachabilityInfo(NamedTuple):
    label: str
    tone: Tone


BACKEND_REACHABILITY_INFO: Worded[BackendReachability, BackendReachabilityInfo] = Worded(
    {
        "live": BackendReachabilityInfo("Backend reachable", "success"),
        "unreachable": BackendReachabilityInfo("Backend unreachable", "warn"),
        "missing_extra": BackendReachabilityInfo("Python extra not installed", "warn"),
    }
)
assert BACKEND_REACHABILITY_INFO.keys() == set(get_args(BackendReachability))


def backend_reachability(
    down: BackendUnreachableError | MissingExtraError | None,
) -> BackendReachability:
    """What the launch's own probe (`admission.py::probe_backend`) read, as one status."""
    match down:
        case None:
            return "live"
        case MissingExtraError():
            return "missing_extra"
        case BackendUnreachableError():
            return "unreachable"


class WireAdapter(Protocol):
    def __call__(
        self,
        sample: Sample,
        pipeline_params: PipelineParams,
    ) -> dict[str, Any]: ...


# A reply's JSON object -> the `data` of a `/matches` body; `None`: it holds nothing to read.
ReplyReader = Callable[[dict[str, Any]], dict[str, Any] | None]


class AuthScheme(NamedTuple):
    """The header a backend's token rides in, sent as ``prefix + token``."""

    header: str
    prefix: str


def bearer() -> AuthScheme:
    return AuthScheme("Authorization", "Bearer ")


def data_envelope(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    return data if isinstance(data, dict) else {}


class CellEnvelopeSeconds(Protocol):
    """The NUMBER only; the bound it puts in force is ``scoring/cell_envelope.py::CellEnvelope``."""

    def __call__(self, sample: Sample, pipeline_params: PipelineParams, /) -> float: ...


class CellHop(Protocol):
    """The cycle one cell in flight runs, in the measuring cycle's own sandbox; ``None``: not yet one."""

    def __call__(
        self,
        sample: Sample,
        pipeline_params: PipelineParams,
    ) -> CycleHop | None: ...


class SessionProtocol(Protocol):
    """Also how this backend's error replies READ, so ``BackendClient`` parses no backend's envelope."""

    async def open(
        self,
        http: httpx.AsyncClient,
        base_url: str,
        samples: Sequence[Sample],
        library: Sequence[str],
        /,
    ) -> None:
        """Once per launch, before a cell is sent: what the backend indexes it derives itself."""

    async def recover(
        self, http: httpx.AsyncClient, base_url: str, reply: httpx.Response, /
    ) -> bool: ...

    def resend_refused(self, reply: httpx.Response, /) -> str | None: ...
