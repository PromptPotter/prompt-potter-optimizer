# PromptPotter Documentation

PromptPotter tunes prompts and pipeline configs against a labelled dataset: maximize fitness, minimize spend.

**New here? → [`manual/`](manual/README.md).** Status and the forward plan: [`specs/roadmap.md`](specs/roadmap.md).

| Folder | Start at |
|--------|----------|
| [`manual/`](manual/README.md) | Install → first campaign → reading output → troubleshooting |
| [`concepts/`](concepts/the-loop.md) | How the loop works |
| [`developer/`](developer/README.md) | Implementation: Python names, data contracts, node wiring |
| [`operations/`](operations/persistence-and-state.md) | Running it: the `.promptpotter/` tree, resume/rewind/fork, backends, observability |
| [`methods/`](methods/verdict-resolution.md) | The θ model and the spend-control procedures that read it |
| [`research/`](research/benchmarks.md) | Benchmarks, the admission bar, what a number may claim |
| [`specs/`](specs/roadmap.md) | Roadmap and forward-looking specs |
| [`decisions.md`](decisions.md) | Why each boundary sits where it does |

Agents read [`CLAUDE.md`](CLAUDE.md) instead.
