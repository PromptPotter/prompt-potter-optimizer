# Milestone 8: Multi-Connector Architecture

**Version:** 0.9.0
**Date:** 2026-02-27
**Status:** Planned
**Depends on:** [Roadmap M8](roadmap.md), [ADD v0.10.0](add.md), [M6 Pipeline Composability](m6-pipeline-composability.md), [PRD P1.13](prd.md)

> **Staleness note:** Written against ADD v0.9.0 (2026-02-27), before the ADD/WBS v0.10.0 rewrite. Verify `BackendClient` signatures against current code before implementing.

---

## Context

**Current state:** All evaluation paths take `BackendClient` (a concrete TermNorm HTTP client) directly. The class is instantiated in `feedback_cycle.py` with a `base_url`. The connector contract is already documented in `docs/connectors/termnorm.md`, but no abstraction exists — every service that evaluates prompts is hardcoded to TermNorm's `/matches` endpoint.

**Goal:** Abstract `BackendClient` into a `ConnectorProtocol` (structural subtyping via `typing.Protocol`). The existing `BackendClient` satisfies the protocol without code changes. A `MockConnector` provides a test double and serves as a reference for future connectors.

---

## Current BackendClient Interface

Methods derived from actual usage across `prompt_eval.py` and `feedback_cycle.py`:

| Method | Signature | Used by |
|--------|-----------|---------|
| `init_session` | `async (terms: list[str]) -> dict[str, Any]` | `feedback_cycle.py`, `prompt_eval.py` |
| `run_match` | `async (query: str, pipeline_params: dict \| None, ranking_prompt: str \| None) -> dict[str, Any]` | `prompt_eval.py` (via `backend_reranker_eval`) |
| `fetch_experiments` | `async () -> dict[str, Any]` | `backend_client.py` (sync operations) |
| `fetch_experiment` | `async (experiment_id: str, include_traces: bool) -> dict[str, Any]` | `backend_client.py` (sync operations) |
| `extract_session_terms` | `@staticmethod (experiment_data: dict) -> list[str]` | `feedback_cycle.py`, `_campaign_lib.py` |
| `extract_replay_queries` | `@staticmethod (experiment_data: dict) -> list[dict[str, Any]]` | `_campaign_lib.py`, `DatasetLoadNode` (M6) |

---

## Connector Method Table

The protocol defines 6 methods. Two tiers: **core** (required for eval) and **sync** (required for data loading).

| # | Method | Tier | Purpose |
|---|--------|------|---------|
| 1 | `init_session` | Core | Initialize backend state (load terms, warm caches) |
| 2 | `run_match` | Core | Execute single query with optional prompt/param overrides |
| 3 | `fetch_experiments` | Sync | List available experiments for dataset selection |
| 4 | `fetch_experiment` | Sync | Get experiment data with ground-truth mappings |
| 5 | `extract_session_terms` | Sync | Parse terms from experiment data (static) |
| 6 | `extract_replay_queries` | Sync | Parse query/expected pairs from experiment data (static) |

---

## Scope Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Abstraction mechanism | `typing.Protocol` (structural subtyping) | `BackendClient` satisfies it without inheriting — zero migration cost |
| Static methods | Keep as `@staticmethod` on protocol | They parse experiment-specific data formats; each connector implements its own parsing |
| Sync operations | Include in protocol (not separate) | `DatasetLoadNode` (M6) needs them; splitting would add complexity without benefit |
| `replay_queries` | Exclude from protocol | High-level convenience method built on `init_session` + `run_match`. Each connector can implement its own. |
| `sync_experiments` / `sync_experiment` | Exclude from protocol | ProjectStore-coupled convenience methods. Stay on `BackendClient` as TermNorm-specific. |
| `MockConnector` | Both test double and reference impl | Shows exactly how to satisfy the protocol. Returns configurable canned responses. |
| Registry | Simple dict-based `ConnectorRegistry` | Discover connectors by ID at runtime. No plugin system needed yet. |

---

## Deliverables

| # | File | Action | What |
|---|------|--------|------|
| 1 | `api/services/connector_protocol.py` | CREATE | `ConnectorProtocol` as `typing.Protocol` with 6 methods |
| 2 | `api/services/mock_connector.py` | CREATE | `MockConnector` implementing protocol — configurable responses, call recording |
| 3 | `api/services/connector_registry.py` | CREATE | `ConnectorRegistry` — register/get connectors by ID, default to BackendClient |
| 4 | `api/services/prompt_eval.py` | MODIFY | Change `backend_client: BackendClient` → `backend_client: ConnectorProtocol` in function signatures |
| 5 | `api/services/feedback_cycle.py` | MODIFY | Change `BackendClient` import → `ConnectorProtocol` type annotation |
| 6 | `api/services/campaign/feedback_cycle.py` | MODIFY | Change `BackendClient` instantiation to use `ConnectorRegistry` or accept connector |
| 7 | `docs/connectors/connector-protocol.md` | CREATE | Developer guide: how to implement a new connector |
| 8 | `tests/test_connector_protocol.py` | CREATE | Protocol conformance tests, MockConnector tests, registry tests |

---

## ConnectorProtocol Definition

```python
from typing import Protocol, Any

class ConnectorProtocol(Protocol):
    """Async backend connector for PromptPotter optimization.

    Any class with these methods satisfies the protocol via structural subtyping.
    BackendClient already satisfies this without changes.
    """

    async def init_session(self, terms: list[str]) -> dict[str, Any]:
        """Initialize backend state with terms (e.g., load search index)."""
        ...

    async def run_match(
        self,
        query: str,
        pipeline_params: dict[str, Any] | None = None,
        ranking_prompt: str | None = None,
    ) -> dict[str, Any]:
        """Execute single query through backend pipeline.

        Returns dict with at minimum: 'results' (list of candidates),
        'top_candidate' (best match string), 'confidence' (float).
        """
        ...

    async def fetch_experiments(self) -> dict[str, Any]:
        """List available experiments with metadata."""
        ...

    async def fetch_experiment(
        self, experiment_id: str, include_traces: bool = True,
    ) -> dict[str, Any]:
        """Fetch single experiment with ground-truth data."""
        ...

    @staticmethod
    def extract_session_terms(experiment_data: dict) -> list[str]:
        """Extract initialization terms from experiment data."""
        ...

    @staticmethod
    def extract_replay_queries(experiment_data: dict) -> list[dict[str, Any]]:
        """Extract query/expected pairs for evaluation.

        Returns list of dicts with at minimum: 'query' (str),
        'expected' (str), and any additional metadata fields.
        """
        ...
```

---

## MockConnector Design

```python
class MockConnector:
    """Test double and reference ConnectorProtocol implementation.

    Configurable responses for testing optimization pipelines
    without a live backend.
    """

    def __init__(
        self,
        default_top_candidate: str = "mock_match",
        accuracy_rate: float = 0.5,
        latency_ms: float = 0.0,
    ):
        self.calls: list[dict] = []  # call recording for assertions

    async def init_session(self, terms: list[str]) -> dict[str, Any]: ...
    async def run_match(self, query: str, **kwargs) -> dict[str, Any]: ...
    async def fetch_experiments(self) -> dict[str, Any]: ...
    async def fetch_experiment(self, experiment_id: str, ...) -> dict[str, Any]: ...

    @staticmethod
    def extract_session_terms(experiment_data: dict) -> list[str]: ...
    @staticmethod
    def extract_replay_queries(experiment_data: dict) -> list[dict[str, Any]]: ...

    # Test helpers
    def set_responses(self, query_to_result: dict[str, str]) -> None:
        """Map query strings to top_candidate responses."""
    def get_call_count(self, method: str) -> int: ...
    def reset(self) -> None: ...
```

The `accuracy_rate` parameter controls what fraction of `run_match` calls return the expected value as `top_candidate` (for testing optimization convergence without a real backend).

---

## ConnectorRegistry Design

```python
class ConnectorRegistry:
    """Simple registry mapping connector IDs to instances."""

    _connectors: dict[str, ConnectorProtocol]

    def register(self, connector_id: str, connector: ConnectorProtocol) -> None: ...
    def get(self, connector_id: str) -> ConnectorProtocol: ...
    def list_connectors(self) -> list[str]: ...
    def has(self, connector_id: str) -> bool: ...


# Module-level singleton
_registry: ConnectorRegistry | None = None

def get_connector_registry() -> ConnectorRegistry: ...
```

---

## Migration Path

The type annotation change is mechanical and non-breaking:

```python
# Before (M6)
from api.services.backend_client import BackendClient

async def backend_reranker_eval(
    query_data: dict,
    backend_client: BackendClient,   # concrete type
    ...
) -> dict:

# After (M8)
from api.services.connector_protocol import ConnectorProtocol

async def backend_reranker_eval(
    query_data: dict,
    backend_client: ConnectorProtocol,  # structural type
    ...
) -> dict:
```

`BackendClient` satisfies `ConnectorProtocol` via structural subtyping — no inheritance needed, no code changes to `BackendClient` itself.

---

## Work Packages

| ID | Work Package | Sessions | Depends on | Description |
|----|-------------|:--------:|------------|-------------|
| 8.0 | Write M8 spec | 1 | — | This document |
| 8.1 | ConnectorProtocol + MockConnector | 1 | 8.0 | Create `connector_protocol.py` and `mock_connector.py`. Protocol conformance tests verifying BackendClient satisfies protocol. MockConnector unit tests. |
| 8.2 | ConnectorRegistry | 1 | 8.1 | Create `connector_registry.py`. Register/get/list connectors. Integration with `runtime_config` from M6. |
| 8.3 | Service migration | 1 | 8.1 | Change type annotations in `prompt_eval.py`, `feedback_cycle.py`. Update connector instantiation to use registry. |
| 8.4 | Docs + integration test | 1 | 8.2, 8.3 | Write `docs/connectors/connector-protocol.md`. Integration test: run feedback cycle with MockConnector. |
| 8.5 | OPTIMIZER_PIPELINE_SCHEMA | 1 | 8.1 | Describe the optimizer's own 4-step pipeline as a `PipelineSchema` instance (moved from M7 Wave E2). Enables `GET /optimizer/pipeline` for L4 self-optimization. |

### Reading list per work package

| WP | Read first |
|----|-----------|
| 7.1 | `api/services/backend_client.py` (full class), `docs/connectors/termnorm.md` (contract), `typing.Protocol` docs |
| 7.2 | `api/services/llm_client.py` (singleton pattern reference: `get_llm_client()`) |
| 7.3 | `api/services/prompt_eval.py` (backend_reranker_eval, evaluate_prompt_cached), `api/services/campaign/feedback_cycle.py` (BackendClient usage) |
| 7.4 | `docs/connectors/termnorm.md` (existing connector doc), `tests/test_campaign_registry.py` (E2E test pattern) |

---

## Entry Criteria

- M6 exit gate passed (workflow engine active, runtime_config working)
- All existing tests pass (`pytest -v --tb=short`)

## Exit Criteria

- `ConnectorProtocol` defined with 6 methods
- `BackendClient` satisfies `ConnectorProtocol` (verified by test)
- `MockConnector` satisfies `ConnectorProtocol` (verified by test)
- `prompt_eval.py` and `feedback_cycle.py` use `ConnectorProtocol` type annotations
- `ConnectorRegistry` can register and retrieve connectors
- Feedback cycle runs successfully with `MockConnector` (no live backend needed)
- `docs/connectors/connector-protocol.md` documents how to implement a new connector
- All existing tests still pass

## Test Strategy

| Test | Type | What it verifies |
|------|------|-----------------|
| `test_backend_client_satisfies_protocol` | Unit | `BackendClient` is structurally compatible with `ConnectorProtocol` (runtime_checkable) |
| `test_mock_connector_satisfies_protocol` | Unit | `MockConnector` is structurally compatible |
| `test_mock_connector_accuracy_rate` | Unit | `accuracy_rate` controls hit/miss ratio |
| `test_mock_connector_call_recording` | Unit | `calls` list records method invocations |
| `test_mock_connector_set_responses` | Unit | `set_responses()` maps queries to results |
| `test_registry_register_get` | Unit | Register and retrieve connectors by ID |
| `test_registry_missing_connector` | Unit | `get()` raises `KeyError` for unknown ID |
| `test_eval_with_mock_connector` | Integration | `backend_reranker_eval()` works with `MockConnector` |
| `test_feedback_cycle_with_mock` | Integration | `run_feedback_cycle()` completes with `MockConnector` |
| `test_workflow_with_mock` | Integration | `optimization_campaign.yaml` runs with `MockConnector` via registry |
