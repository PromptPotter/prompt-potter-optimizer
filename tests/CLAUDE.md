# tests — Testing Conventions

Minimal test suite — only stable contracts are tested. See root CLAUDE.md for rationale.

## Fixtures (`conftest.py`)

| Fixture | Description |
|---------|-------------|
| `_reset_langfuse` | **Autouse** — resets `LangfuseLogger` singleton after every test |

## Helpers (`_helpers.py`)

| Helper | Purpose |
|--------|---------|
| `MockCompletion` | Fake OpenAI-compatible completion response |
| `make_http_error(status_code)` | Create a mock HTTP error exception with `status_code` attribute |

## Mock strategy

No pytest-mock plugin; use `monkeypatch` for async service mocking, stdlib `unittest.mock` when needed.
