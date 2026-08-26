# PromptPotter Documentation

PromptPotter tunes prompts and pipeline configs against a labelled dataset. The whole design is **maximize fitness, minimize spend**.

**New here? → [`manual/`](manual/README.md).** Numbered chapters: install → first run → reading output → troubleshooting.

**Status & roadmap** have one owner — the lane table in [`specs/roadmap.md`](specs/roadmap.md), whose Status column is the truth. Restating it here is how the two came to disagree.

Each folder's entry page:

| Folder | Start at |
|--------|----------|
| [`manual/`](manual/README.md) | User walkthrough — install → first campaign → reading output → troubleshooting |
| [`concepts/`](concepts/the-loop.md) | How it works — the three-layer loop with CONTEXT and PLAN, spend control, self-healing |
| [`developer/`](developer/README.md) | Implementation spec — Python names, data contracts, node wiring |
| [`operations/`](operations/persistence-and-state.md) | Running it — the `.promptpotter/` tree, resume/rewind/fork, then backends and observability beside it |
| [`methods/`](methods/verdict-resolution.md) | The θ model and the two spend-control procedures that read it |
| [`research/`](research/benchmarks.md) | Benchmarks, metrics, related work |
| [`specs/`](specs/roadmap.md) | Roadmap + forward-looking specs |
| `assets/` | Images and diagrams |

Agents read [`CLAUDE.md`](CLAUDE.md) instead — same tree, routed by what the ask is.
