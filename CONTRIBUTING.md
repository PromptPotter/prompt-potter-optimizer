# Contributing to PromptPotter

PromptPotter is **LLM-driven program evolution** for prompts and pipeline
params. Start with [`docs/architecture.md`](docs/architecture.md) — §0 + §0.5
are what every change is measured against.

## Setup

```bash
pip install -e ".[all,dev]"
git config core.hooksPath .githooks   # one-time per clone — enables the
                                      # pre-commit ruff format + check
```

## Before you commit

CI runs this exact chain — match it locally:

```bash
ruff check . && ruff format --check . && deptry . && mypy promptpotter/ && pytest -q
```

Webapp changes additionally run, and CI gates,
`cd webapp && npm run lint && npx tsc --noEmit && npm run build`.

## Type checking

`mypy` runs in `strict` mode. The pure core — `domain/`, `shared/`,
`config/` — is fully migrated; new code there must pass strict (typed
defs, parameterized `dict`/`list`, no `Any` returns). The I/O layers
(`application/`, `infrastructure/`, `presentation/`, `connectors/`) sit
behind a relaxation override in `pyproject.toml` — the strict-migration
ledger. A PR with substantial reach into one of those packages should
migrate it: drop its pattern from the override's `module` list and fix
the annotations it surfaces. New `# type: ignore` comments must carry an
error code (`# type: ignore[code]`).

## The two rules that get broken most

- **No backward compatibility, ever.** Zero released versions — nothing to be
  compatible with. When you rename or restructure, update every caller and
  delete the old definition: no re-export shim, no fallback chain, no
  `# legacy` comment. The full rule is the **STOP** section of
  [`CLAUDE.md`](CLAUDE.md).
- **The test suite is subtractive.** Read [`tests/CLAUDE.md`](tests/CLAUDE.md)
  before touching tests. Changing a contract means delete the old test and
  write the new one — never a compatibility test.

## The pre-flight gate

Before adding any new concept (class, projection, injection, prompt, field,
dict, file), answer the eight questions in the [`CLAUDE.md`](CLAUDE.md)
pre-flight gate. The pull-request template turns them into a checklist.
"I don't know" or "kind of" on any answer is a hard block.

## Conventions

Domain vocabulary, style, and code-shape rules live in
[`docs/developer/conventions.md`](docs/developer/conventions.md) and
[`docs/glossary.md`](docs/glossary.md). Use conventional commits; keep commit
messages under 800 characters.
