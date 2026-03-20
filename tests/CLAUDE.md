# tests — Testing Conventions

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
| `apply_llm_mock(monkeypatch)` | Mocks `get_llm_client` to return `MockLLMClient` |
| `apply_grow_mock(monkeypatch)` | Mocks `generate_candidates` with deterministic variants |
| `apply_eval_mock(monkeypatch, round_hits)` | Mocks `evaluate_prompt_cached`; returns `call_count` list for tracking |
| `run_simple_cycle(monkeypatch, eval_data, config, *, round_hits, **kwargs)` | Apply standard mocks + run feedback cycle; returns `CycleResult` |
| `MockLangfuseLogger` | Records all Langfuse calls (traces, spans, scores, generations, dataset API) |
| `MockCompletion` | Fake OpenAI-compatible completion response |
| `rp_hash(text)` | Compute `rendered_prompt_hash` matching `build_dataset_run_data` |
| `make_baseline_ps(**overrides)` | Build a baseline `PromptState` with sensible defaults |
| `make_dataset_run(run_id, ...)` | Build a minimal `dataset_run` dict with configurable fields |
| `build_eval_results(data, hits)` | Build `(results, scores)` tuple for eval mocking |
| `make_http_error(status_code)` | Create a mock HTTP error exception with `status_code` attribute |

## Mock strategy

No pytest-mock plugin; use `monkeypatch` for async service mocking, stdlib `unittest.mock` when needed.
