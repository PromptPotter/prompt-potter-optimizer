from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.domain.connector import (
    CellEnvelopeSeconds,
    ConnectorExecution,
    MeasuredUnit,
    SessionProtocol,
    WireAdapter,
)
from promptpotter.domain.pipeline_schema import NodeRole, NodeSpendBound
from promptpotter.domain.sample import Sample
from promptpotter.domain.value_tree import Delivery
from promptpotter.infrastructure.llm.send_pacing import CELL_WAIT_S, SEND_ATTEMPTS

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True)
class InProcessWorkload:
    experiment: Mapping[str, Any] | None
    program: object | None


PROBE_WORKLOAD = InProcessWorkload(experiment=None, program=None)


class NoopSession:
    __slots__ = ()

    async def set_terms(
        self, http: httpx.AsyncClient, base_url: str, terms: list[str]
    ) -> dict[str, Any]:
        return {"status": "noop", "terms_count": len(terms)}

    async def recover(self, http: httpx.AsyncClient, base_url: str, reply: httpx.Response) -> bool:
        return False

    def resend_refused(self, reply: httpx.Response) -> str | None:
        return None


# `(workload, sample, wire_adapter payload) -> {"data": {…}}`, the shape of a `/matches` body.
InProcessRun = Callable[[InProcessWorkload, Sample, dict[str, Any]], Awaitable[dict[str, Any]]]

ExperimentResolver = Callable[[Mapping[str, Any]], dict[str, Any]]

# `(http, base_url) -> the backend's self-reported revision`, `None` where it is silent.
VersionCheck = Callable[["httpx.AsyncClient", str], Awaitable[str | None]]

# `backend_url -> why the backend is down`, `None` where it is up.
PreflightFn = Callable[[str], Awaitable[str | None]]

# Read as the client opens its connection, never at import; `None` sends no auth header.
AuthTokenFn = Callable[[], str | None]

# `(node name, node config) -> what one run of that node can bill`.
SentSpendBound = Callable[[str, Mapping[str, Any]], NodeSpendBound | None]

# `pipeline_params -> Delivery`, resolved from the params a run hashes.
PromptDelivery = Callable[[dict[str, Any] | None], Delivery]


def _in_the_request(pipeline_params: dict[str, Any] | None) -> Delivery:
    return "request"


# The experiment-file key; its value is a scope out of `Connector.package_cache_scopes`.
PACKAGE_CACHE_KEY = "package_cache"


@dataclass(frozen=True)
class Connector:
    name: str
    """Lowercase; matches ``pipeline.yaml::backend_type``."""

    wire_adapter: WireAdapter

    session_factory: Callable[[], SessionProtocol] = NoopSession
    """Called per ``BackendClient``: a session holds per-client state."""

    experiment_file: str = ""
    """A panel file in the dataset's config dir that OWNS its rows; empty: the loader registry's."""

    extract_experiment: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None
    """Rows in panel order: ``source_pin`` enters ``Sample.key``; a ``None`` ground truth IS the shape."""

    resolve_experiment: ExperimentResolver | None = None
    """Applied by ``dataset_access.py::dataset_experiment`` to every read of the file."""

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

    sent_spend_bound: SentSpendBound | None = None
    """The bound THIS process sends the backend; answering ``None`` leaves no spend ceiling to hold."""

    model_names_provider: bool = False
    """Model strings are litellm's ``provider/model``, priced as ``pricing.py::inline_route`` splits."""

    cancel_stops_billing: bool = False
    """``False``: a sent cell is left to land, since the provider bills it whether or not anyone waits."""

    cell_envelope_s: CellEnvelopeSeconds | None = None
    """Bounds the SUM of one cell's awaits; reaching it is ``ErrorCategory.HALTED``, never a zero."""

    cell_attempts: int = SEND_ATTEMPTS
    """Sends in all, the first included: only the backend knows what a resend costs."""

    cell_wait_s: float = CELL_WAIT_S
    """One request's wait on its far end; running out is no cut of ours, so the cell ends unreported."""

    measured_unit: MeasuredUnit = "sample"
    """Declared, never sniffed off a row."""

    prompt_delivery: PromptDelivery = _in_the_request
    """Per run, off the params it hashes; ``artifact_body`` may never ARRIVE and owes an observation."""

    prompt_fields_as_node_params: bool = False
    """``True`` on the recursion alone; elsewhere a prompt node with no ``prompt_info`` is refused."""

    required_observation_keys: tuple[str, ...] = ()
    """Keys the backend ALWAYS emits: run init raises unless the schema maps each."""

    answer_key: str | None = None
    """The ``data`` key holding the answer TEXT; ``None``: ``predicted`` is the terminal ranker's."""

    in_process_run: InProcessRun | None = None

    expected_revision: str | None = None

    version_check: VersionCheck | None = None

    preflight: PreflightFn | None = None
    """``None`` opts out: an in-process backend has nothing to probe."""

    auth_token: AuthTokenFn | None = None

    completion_check: Callable[[], None] | None = None
    """Run where the table completes, so a raise stops the server at boot and a run at init."""

    pipeline_declaration: Callable[[Stores, Mapping[str, Any] | None], dict[str, Any]] | None = None
    """An ``in_process`` backend's answer in place of ``GET /pipeline``; ``pipeline.yaml`` overlays it."""

    identity_config: (
        Callable[[Stores, Path, Mapping[str, Any] | None], dict[str, dict[str, Any]]] | None
    ) = None
    """Identity-only, never on the wire: what a panel is measured WITH, never which cells it holds."""

    default_pipeline: tuple[str, ...] = ()
    """Seeds a fresh upload's ``pipelines.default``; empty: the backend's own default."""

    default_optimization: tuple[tuple[str, Any], ...] = ()
    """Seeds ``campaign.json::optimization``; ``degradation_threshold`` has no schema default."""

    node_roles: Mapping[str, NodeRole] = field(default_factory=dict)
    """Lets ingest detect a pipeline's required inputs BEFORE the backend is reached."""

    default_node_config: Mapping[str, Any] = field(default_factory=dict)
    """Seeds a fresh ``pipeline.yaml``; its ``param_allowed_values`` is a DEFAULT, never a cost rail."""

    available_models: tuple[str, ...] = ()
    """The MENU, never what a node permits; a model needing a ``max_tokens`` floor needs a registry profile."""


__all__ = [
    "PACKAGE_CACHE_KEY",
    "PROBE_WORKLOAD",
    "AuthTokenFn",
    "Connector",
    "ExperimentResolver",
    "InProcessRun",
    "InProcessWorkload",
    "NoopSession",
    "PreflightFn",
    "PromptDelivery",
    "VersionCheck",
]
