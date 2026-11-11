# PromptPotter Documentation

PromptPotter tunes prompts and pipeline configs against a labelled dataset: maximize fitness, minimize spend.

**New here? → [`manual/`](manual/README.md).** Status and the forward plan: [`specs/roadmap.md`](specs/roadmap.md).

| Folder | Start at |
|--------|----------|
| [`manual/`](manual/README.md) | Install → first campaign → reading output → troubleshooting |
| [`concepts/`](concepts/the-loop.md) | How the loop works |
| [`operations/`](operations/persistence-and-state.md) | Running it: the `.promptpotter/` tree, resume/rewind/fork, backends, observability |
| [`methods/`](methods/verdict-resolution.md) | The θ model and the spend-control procedures that read it |
| [`research/`](research/benchmarks.md) | Benchmarks, the admission bar, what a number may claim |
| [`specs/`](specs/roadmap.md) | Roadmap and forward-looking specs |
| [`decisions.md`](decisions.md) | Why each boundary sits where it does |

## Developer — seams no single file shows

Every change is placed on [`architecture.md`](architecture.md) §0 + §0.5.

| Page | Owns |
|---|---|
| [conventions](developer/conventions.md) | Style, code shape, naming, git |
| [dispatch-hub](developer/dispatch-hub.md) | How an optimizer prompt is filled: injections, bounds, the L1 layout, L2 |
| [node-standard](developer/node-standard.md) | The `pipeline.yaml` contract and the node types |
| [stable-api](developer/stable-api.md) | What an outside program may rely on, the SSE contract included |
| [offline-run](developer/offline-run.md) | Proving a change does not move what the loop decides |
| [refactor-arcs](developer/refactor-arcs.md) | How a debt arc is sized, landed, reviewed and stopped |
| [dspy-optimizer](developer/dspy-optimizer.md) | PromptPotter as a DSPy optimizer |

Agents read [`CLAUDE.md`](CLAUDE.md) instead.
