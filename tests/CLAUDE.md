# tests — Testing Conventions

## Running tests

```bash
pytest                              # all tests, quiet output (default: -q --tb=short)
pytest -m "not slow"                # skip orchestrator integration tests (~4s)
pytest tests/test_prompt_state.py   # single file
pytest tests/test_prompt_state.py::test_create_and_derive  # single function
pytest -v                           # verbose override when debugging a failure
```

## Markers

| Marker | Meaning |
|--------|---------|
| `slow` | Full orchestrator integration tests (feedback cycle, e2e, cycle resume). ~1-2s each. |
| `asyncio` | Async tests (auto-configured via `asyncio_mode = "auto"`) |

## pytest config (pyproject.toml)

- `asyncio_mode = "auto"` — async tests run automatically, no manual event loop setup
- `pythonpath = ["tests"]` — allows direct imports from `tests/` (e.g. `from _helpers import ...`)
- `testpaths = ["tests"]`

## Fixtures (`conftest.py`)

| Fixture | Description |
|---------|-------------|
| `mock_llm_client` | Swaps global `_llm_client` singleton with `MockLLMClient`; restores after test |
| `tmp_store` / `store` | `ProjectStore` backed by `tmp_path`; use for all file I/O tests |
| `eval_data` | Standard 3-query dataset: aspirin, ibuprofen, acetaminophen |
| `cycle_config` | Standard `CycleConfig` for feedback cycle tests (max_rounds=5, patience=2, n_variants=3) |
| `_reset_langfuse` | **Autouse** — resets `LangfuseLogger` singleton after every test |

## Helpers (`_helpers.py`)

| Helper | Purpose |
|--------|---------|
| `apply_init_mock(monkeypatch)` | Mocks `restructure_context` for InitNode |
| `apply_llm_mock(monkeypatch)` | Mocks `get_llm_client` to return `MockLLMClient` |
| `apply_grow_mock(monkeypatch)` | Mocks `generate_candidates` with deterministic variants |
| `apply_eval_mock(monkeypatch, round_hits)` | Mocks `evaluate_prompt_cached`; returns `call_count` list for tracking |
| `MockLangfuseLogger` | Records all Langfuse calls (traces, spans, scores, generations, dataset API) |
| `MockCompletion` | Fake OpenAI-compatible completion response |
| `rp_hash(text)` | Compute `rendered_prompt_hash` matching `build_dataset_run_data` |
| `make_baseline_ps(**overrides)` | Build a baseline `PromptState` with sensible defaults |
| `make_dataset_run(run_id, ...)` | Build a minimal `dataset_run` dict with configurable fields |
| `build_eval_results(data, hits)` | Build `(results, scores)` tuple for eval mocking |
| `make_http_error(status_code)` | Create a mock HTTP error exception with `status_code` attribute |

## Mock strategy

- **`monkeypatch`** for async service mocking (preferred) — patches module-level functions
- **`MagicMock`** for dependency injection into functions
- No pytest-mock plugin; use stdlib `unittest.mock` when needed

## Patterns

- **Async tests**: `@pytest.mark.asyncio` + inline `async def` mock functions
- **File I/O**: Always use `tmp_store` fixture, never raw temp dirs
- **Class-based grouping**: Related assertions in test classes (e.g. `TestFullTraceExtraction`)
- **Naming**: `test_{module}.py` mirrors `api/services/{module}.py`
