---
status: accepted
date: 2026-08-14
deciders: [maintainer]
consulted: [m12-control-plane]
informed: []
relates:
  - docs/adr/0001-m12-control-plane.md
  - docs/developer/dspy-optimizer.md
supersedes: []
superseded-by: []
tags: [packaging, distribution, embedding, dependencies, extras]
---

# The core install is the engine; every surface above it is an extra

## Context and Problem Statement

PromptPotter is used two ways, and they pull the dependency tree in opposite directions.

**As a product**, an operator installs it and gets the whole thing — a dashboard served over
HTTP, OIDC sign-in, spreadsheet ingest, a notebook. Each of those is a package, and it is
correct for the operator to have them.

**As a library**, the loop runs inside a program someone else wrote — a DSPy module, an
ML-research agent, a harness — and that program has its own dependency tree it did not
choose to merge with ours. A web framework, an ASGI server, an SSE library and a JWS
implementation, pulled in because a prompt optimizer was imported, are a cost the host pays
for surfaces it will never open. They are also **attack surface nobody opted into**, which
is a different objection and the stronger one: a host cannot audit what it did not ask for.

The immediate forcing case was the DSPy adapter, but the constraint is not about DSPy. Any
embedding — the MCP server mode, an agent-callable tool, a third-party harness — meets the
same wall, and each would otherwise argue for its own distribution to escape our weight.
Splitting the package per consumer is the failure mode this decision exists to prevent: it
multiplies CI, release pipelines and version matrices for what is a dependency-graph problem.

## Decision Drivers

* **The loop must be embeddable without negotiation.** If importing PromptPotter is
  expensive, an embedder either forks it or does not embed it.
* **One distribution, one version.** A second package buys an independent release cadence and
  pays for it with cross-repo drift — which this repo already has machinery for
  (`Connector.expected_revision` / `version_check`) precisely because it hurts.
* **A dependency is a security decision, not a convenience one.** `benchmarks` is already
  held out of `all` on this reasoning; the same argument generalizes.
* **Reachability is measurable, so it should be measured.** Which packages a given entry
  point imports is a fact, not a judgement call.

## Decision

**Core carries the engine and nothing else. Every capability above it is an extra, and `all`
folds back the ones an operator wants.**

1. **Core is what the loop cannot run without** — the model clients, config, HTTP, file
   locking, numeric and serialization support. The measure is reachability from the two
   embedding entry points (`presentation/cli/campaign_runner.py` and
   `application/embedded_run.py`), computed per module in a fresh interpreter, not argued.
2. **An operator surface is an extra.** `api` (dashboard, OIDC, SSE), `excel`, `jupyter`,
   `stats`, `observability`, `anthropic`, `dspy`. `all` folds in the operator set;
   `benchmarks` stays out of it, because fetching a public bank pulls a large third-party
   surface that nothing on the default path imports.
3. **An extra's import is guarded where a non-installer would hit it**, and the guard names
   the extra. Two shapes, chosen by who imports the module: **function-local** when something
   on the default path imports the module (the `dspy` connector is imported eagerly by
   `connectors/__init__.py`; `openpyxl` sits on the ingest path), and **module-level** when
   the only importer is the caller who asked for the capability
   (`presentation/teleprompter.py`).
4. **A capability that needs a package in core is a design question first.** The answer is
   usually that the capability belongs in an extra, not that the package belongs in core.

## Consequences

* An embedder's cost is the engine plus what they asked for. `pip install promptpotter` went
  from 44 packages to 28 when this was applied; `promptpotter[dspy]` is that plus DSPy.
* **Serving the dashboard now requires `promptpotter[api]`.** A plain install can run
  campaigns and read the artifact tree, and `python -m uvicorn promptpotter.main:app` fails
  with a missing-module error until the extra is installed. `deploy-linux/` and `.[all,dev]`
  are unaffected.
* An `.xlsx` ingest without `[excel]` fails as an ingest error naming the extra, rather than
  as a traceback.
* **The reachability claim rots silently.** Nothing fails when a new core import creeps into a
  module the CLI reaches — it just makes the engine heavier. Re-measure when the import graph
  moves, rather than trusting this file's numbers.
* An embedded adapter lives here rather than in its own repo. Release coupling to a
  fast-moving host library (DSPy) is the accepted cost: a break there cuts a `promptpotter`
  release.
