from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter.domain.cell_terms import CellTerm
from promptpotter.domain.connector import (
    AuthScheme,
    CellEnvelopeSeconds,
    CellHop,
    ConnectorExecution,
    MeasuredUnit,
    ReplyReader,
    SessionProtocol,
    WireAdapter,
    bearer,
    data_envelope,
)
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_schema import NodeSpendBound
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import KeySource
from promptpotter.domain.value_tree import Delivery
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS
from promptpotter.shared.extras import Extra

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True)
class InProcessWorkload:
    experiment: Mapping[str, Any] | None
    # Where `experiment` was read: a local path a panel names resolves against it.
    experiment_dir: Path | None
    program: object | None
    # The tenant tree whose catalogue snapshot holds each model's card.
    workspace: Path | None


PROBE_WORKLOAD = InProcessWorkload(
    experiment=None, experiment_dir=None, program=None, workspace=None
)


class NoopSession:
    __slots__ = ()

    async def open(
        self,
        _http: httpx.AsyncClient,
        _base_url: str,
        _samples: Sequence[Sample],
        _library: Sequence[str],
    ) -> None:
        return None

    async def recover(
        self, _http: httpx.AsyncClient, _base_url: str, _reply: httpx.Response
    ) -> bool:
        return False

    def resend_refused(self, _reply: httpx.Response) -> str | None:
        return None


# `(workload, sample, wire_adapter payload) -> {"data": {…}}`, the shape of a `/matches` body.
InProcessRun = Callable[[InProcessWorkload, Sample, dict[str, Any]], Awaitable[dict[str, Any]]]

ExperimentResolver = Callable[["Stores", Mapping[str, Any]], dict[str, Any]]

# `(http, base_url) -> the backend's self-reported revision`, `None` where it is silent.
VersionCheck = Callable[["httpx.AsyncClient", str], Awaitable[str | None]]

# `backend_url -> why the backend is down`, `None` where it is up; read through `Connector.unreachable`.
PreflightFn = Callable[[str], Awaitable[str | None]]

# Read as the client opens its connection, never at import; `None` sends no auth header.
AuthTokenFn = Callable[[], str | None]


@dataclass(frozen=True)
class Endpoint:
    """Where a ``remote_http`` backend listens: the operator's config, never a request's word."""

    setting: str
    """The ``.env`` key holding it, which an unset address names."""

    read: Callable[[], str]


# `(node name, node config, the run's workload) -> what one run of that node can bill`; an LLM
# node's is `capabilities.py::sent_llm_bound`'s.
SentSpendBound = Callable[[str, NodeCall, InProcessWorkload], NodeSpendBound | None]

# `pipeline_params -> Delivery`, resolved from the params a run hashes.
PromptDelivery = Callable[["PipelineParams"], Delivery]


def _in_the_request(_pipeline_params: PipelineParams) -> Delivery:
    return "request"


def cell_envelope_of(seconds: float) -> CellEnvelopeSeconds:
    """One envelope for every cell of a backend whose cells do not vary in length."""

    def envelope(_sample: Sample, _pipeline_params: PipelineParams) -> float:
        return seconds

    return envelope


@dataclass(frozen=True)
class UploadDefaults:
    chain: tuple[str, ...]
    """A fresh upload's ``pipelines.default``."""

    available_models: tuple[str, ...]
    """The MENU, never what a node permits; a model needing a ``max_tokens`` floor needs a registry profile."""


# The experiment-file key; its value is a scope out of `Connector.package_cache_scopes`.
PACKAGE_CACHE_KEY = "package_cache"


@dataclass(frozen=True)
class Connector:
    name: str
    """Lowercase; matches ``dataset.yaml::pipeline.connector``."""

    wire_adapter: WireAdapter

    cell_envelope_s: CellEnvelopeSeconds
    """Bounds the SUM of one cell's awaits; reaching it is ``ErrorCategory.HALTED``, never a zero."""

    cell_payer: KeySource
    """Whose wallet a cell's sends draw, whatever keys the account holds: ``"host"`` draws the
    host's coupon."""

    sent_spend_bound: SentSpendBound | None
    """The bound THIS process sends the backend; ``None`` reads the served node's ``spend_bound``."""

    cells_priced: bool
    """A cell's dollars are bounded before it is sent; ``False`` refuses a dollar ceiling at the mint."""

    cell_path: str = "/matches"
    """Appended to ``base_url()`` for a ``remote_http`` cell's POST; ``""`` posts to the endpoint itself."""

    read_reply: ReplyReader = data_envelope
    """A cell reply's JSON object, read into the ``/matches`` ``data`` shape."""

    round_trip_is_latency: bool = False
    """The backend reports no timings: an answered POST is clocked here as its ``terminal_node``'s."""

    session_factory: Callable[[], SessionProtocol] = NoopSession
    """Called per ``BackendClient``: a session holds per-client state."""

    self_optimization: bool = False
    """The recursion: a campaign on it is an L4 outer loop."""

    extract_experiment: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None
    """Set = the dataset's ``panel`` OWNS its rows, else the row ladder's. Rows in panel order:
    ``source_pin`` enters ``Sample.key``; a ``None`` ground truth IS the shape."""

    resolve_experiment: ExperimentResolver | None = None
    """Applied by ``dataset_access.py::dataset_experiment`` to every read of the panel."""

    execution: ConnectorExecution = "remote_http"
    """``BackendClient.run_query`` dispatches on this, never on ``name``."""

    max_cells_in_flight: int = 2
    """Cells and their catch-up calls together; ``1`` opts out. Never how long an arming lasts."""

    cells_hold_the_machine: bool = False
    """A cell holds a container, so ``max_cells_in_flight`` bounds the MACHINE: one pool across runs."""

    compose_overlay: Path | None = None
    """Taking a machine slot sweeps what a killed run left under it; ``None``: labelled containers."""

    package_cache_scopes: frozenset[str] = frozenset()
    """Run init refuses a dataset declaring any other scope, so the empty default refuses the key."""

    holds_own_sends: bool = False
    """Each paid send is admitted and billed where it is made; ``False``: the cell is ONE send."""

    model_names_provider: bool = False
    """Model strings are litellm's ``provider/model``, priced as ``pricing.py::priced_pair`` splits."""

    cancel_stops_billing: bool = False
    """``False``: a sent cell is left to land, since the provider bills it whether or not anyone waits."""

    cell_hop: CellHop | None = None
    """Rides the cell's ONE heartbeat as an ADDRESS, so a cell that runs a whole campaign names it."""

    cell_attempts: int = SEND_ATTEMPTS
    """Sends in all, the first included: only the backend knows what a resend costs."""

    measured_unit: MeasuredUnit = "sample"
    """Declared, never sniffed off a row."""

    prompt_delivery: PromptDelivery = _in_the_request
    """Per run, off the params it hashes; ``artifact_body`` may never ARRIVE and owes an observation."""

    prompt_fields_as_node_params: bool = False
    """``True`` on the recursion alone; elsewhere a prompt node with no ``prompt_info`` is refused."""

    required_observation_keys: tuple[str, ...] = ()
    """Keys the backend ALWAYS emits: run init raises unless the schema maps each."""

    cell_terms: tuple[CellTerm, ...] = ()
    """The per-cell terms its cells add to the core's, each the answer key of its name, which
    lands on ``PipelineData.cell_report``; served on the pipeline answer, so only its campaigns
    offer them."""

    reported_keys: tuple[str, ...] = ()
    """Answer keys beyond its terms that land on ``PipelineData.cell_report``: a core channel no
    node usage answers (an L4 cell's inner spend), or the facts its tape reads."""

    @property
    def cell_report_keys(self) -> frozenset[str]:
        return frozenset({*(t.name for t in self.cell_terms), *self.reported_keys})

    answer_key: str | None = None
    """The ``data`` key holding the answer TEXT; ``None``: ``predicted`` is the terminal ranker's."""

    in_process_run: InProcessRun | None = None

    expected_revision: str | None = None

    version_check: VersionCheck | None = None

    extra: Extra | None = None
    """What a cell imports beyond core; absent, launch refuses and the listing blocks before a read."""

    preflight: PreflightFn | None = None
    """``None`` opts out: an in-process backend has nothing to probe."""

    endpoint: Endpoint | None = None

    auth_token: AuthTokenFn | None = None

    auth_scheme: Callable[[], AuthScheme] = bearer
    """Read beside ``auth_token``, so the header is operator config too."""

    completion_check: Callable[[], None] | None = None
    """Run where the table completes, so a raise stops the server at boot and a run at init."""

    pipeline_declaration: Callable[[Stores, Mapping[str, Any] | None], dict[str, Any]] | None = None
    """The backend's graph off the panel's experiment, read in place of ``GET /pipeline``;
    ``dataset.yaml::pipeline`` overlays it."""

    identity_config: (
        Callable[[Stores, Mapping[str, Any] | None], dict[str, dict[str, Any]]] | None
    ) = None
    """Identity-only, never on the wire: what a panel is measured WITH, never which cells it holds."""

    uploads: UploadDefaults | None = None
    """Set = a check-in may name it: it runs an uploaded table's rows, and nothing beside them."""

    node_templates: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """Per node name, laid under every definition naming this connector that declares that node."""

    @property
    def serves_uploads(self) -> bool:
        return self.uploads is not None

    @property
    def owns_panel(self) -> bool:
        return self.extract_experiment is not None

    @property
    def serves_pipeline(self) -> bool:
        return self.execution == "remote_http" and self.pipeline_declaration is None

    def base_url(self) -> str:
        return self.endpoint.read().rstrip("/") if self.endpoint is not None else ""

    def unconfigured(self) -> str | None:
        """Why a ``remote_http`` backend has no address; ``None`` where it holds one or has no wire."""
        if self.endpoint is None or self.base_url():
            return None
        return f"{self.endpoint.setting} is unset: name the {self.name} backend in .env."

    def credential_header(self) -> dict[str, str]:
        """The header the operator's credential rides in on every request; empty where config holds none."""
        token = self.auth_token() if self.auth_token is not None else None
        if not token:
            return {}
        scheme = self.auth_scheme()
        return {scheme.header: f"{scheme.prefix}{token}"}

    async def unreachable(self) -> str | None:
        """Why the backend cannot be reached now; ``None`` where it answers or probes nothing. A
        failed probe's transport error is the reason, so no ``preflight`` catches its own."""
        if (unset := self.unconfigured()) is not None:
            return unset
        if self.preflight is None:
            return None
        import httpx

        base_url = self.base_url()
        try:
            return await self.preflight(base_url)
        except httpx.TransportError as exc:
            return f"{type(exc).__name__} at {base_url}: {str(exc).strip() or 'no answer'}"
