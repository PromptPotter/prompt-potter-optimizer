# docs/ — index for agent readers

The operator-facing index is [`README.md`](README.md). Read [`architecture.md`](architecture.md) §0 + §0.5 first; load only the subtree you need.

## Subtrees

| Folder | Holds |
|---|---|
| [`manual/`](manual/) | Operator onboarding, numbered chapters — [`manual/README.md`](manual/README.md) |
| [`concepts/`](concepts/) | How the loop works: [the-loop](concepts/the-loop.md) · [scoring-and-memory](concepts/scoring-and-memory.md) · [structured-output](concepts/structured-output.md) |
| [`developer/`](developer/) | [conventions](developer/conventions.md) · [dispatch-hub](developer/dispatch-hub.md) · [adding-a-surface](developer/adding-a-surface.md) · [self-healing-internals](developer/self-healing-internals.md) · [node-standard](developer/node-standard.md) · [stable-api](developer/stable-api.md) · [dspy-optimizer](developer/dspy-optimizer.md) · [offline-run](developer/offline-run.md) · [event-stream](developer/event-stream.md) |
| [`operations/`](operations/) | Run/watch: [persistence-and-state](operations/persistence-and-state.md) · [observability](operations/observability.md) · [backend-integration](operations/backend-integration.md) · [package-cache](operations/package-cache.md). Datasets: [datasets](operations/datasets.md). Security: [access-model](operations/access-model.md). Carry-over: [mask-projection](operations/mask-projection.md) |
| [`methods/`](methods/) | Which samples: [verdict-resolution](methods/verdict-resolution.md) (Rasch θ/δ). Which candidate: [candidate-elimination](methods/candidate-elimination.md) (PoBB, the θ stop rule) |
| [`research/`](research/) | Publication-facing; how a source is read is [`research/CLAUDE.md`](research/CLAUDE.md)'s |
| [`specs/`](specs/) | Direction of travel: [`roadmap.md`](specs/roadmap.md) (its Status column is truth), living contracts, the debt backlog |
| [`decisions.md`](decisions.md) | The permanent decisions, one section per ADR |

## Anchor docs for hot questions

| Question | Read first |
|---|---|
| How does information flow through L1 / L2 / L3? | [`developer/dispatch-hub.md`](developer/dispatch-hub.md) |
| How does a layer heal a failure? | [`developer/self-healing-internals.md`](developer/self-healing-internals.md) |
| Does my change move what the loop decides? | [`developer/offline-run.md`](developer/offline-run.md) |
| Which model + `reasoning_effort` for this dataset? | [`operations/datasets.md`](operations/datasets.md) § The model a dataset runs |
| The access/security model? | [`operations/access-model.md`](operations/access-model.md) |
| Freeze a buggy cycle as a fixture? | You do not — [`../tests/CLAUDE.md`](../tests/CLAUDE.md) § Frozen cycle fixtures |
| Changed the formula / PoBB / the engine — carry over or fork? | [`operations/mask-projection.md`](operations/mask-projection.md) |

**L4 has two owners**: [`specs/l4-outer-loop.md`](specs/l4-outer-loop.md) says what is TRUE, `.claude/skills/potter-self/` says what to DO. No other file holds L4's status or open list.

## What may live in `specs/`

**Direction of travel only.** A fact about shipped behavior belongs in `concepts/`, `developer/` or `operations/`; a fully shipped spec is deleted (`git log` keeps it). Why a boundary sits where it does lives in [`decisions.md`](decisions.md), which is never deleted on shipping.

## Editing a doc

**One fact, one owner.** The doc governing the artifact a rule constrains owns it; elsewhere write at most an **obligation line** — the rule's name in bold, "owned by" + link, one clause of what THIS layer must do.

**The Recompute Test — docs hold rules, not state.** A line a reader could recompute differently later is state: name the symbol, the enumerator or the counting command instead. A line not recomputable at all is a decision, and belongs. A war story keeps only its decision; the narrative is git's.

Write instead of: line numbers → `<file>.py::<symbol>` · a count of a code-owned set → the enumerator · a status word → a link to the one status owner · a count of on-disk data → the command · `§ Name` → a string matching exactly one heading in the linked file.

**A one-X / no-second-Y claim carries its probe** — `<!-- probe: git grep -cE "<pattern>" -- <path> == 0 -->`, run by `gate.py --only doc-claims`; a claim the code does not keep yet goes in `gate.py::_KNOWN_FALSE`.

**Code defines behavior; where a doc disagrees, the doc is fixed** — except [`architecture.md`](architecture.md) §0 / §0.5, a contract the code is fixed to.
