# PromptPotter Documentation

PromptPotter tunes prompts and pipeline configs against a labelled dataset. The whole design is **maximize fitness, minimize spend**.

**New here? → [`manual/`](manual/README.md).** Numbered chapters: install → first run → reading output → troubleshooting.

| Folder | Purpose |
|--------|---------|
| [`manual/`](manual/README.md) | User walkthrough |
| [`concepts/`](concepts/README.md) | How it works — three-layer loop with CONTEXT and PLAN, spend control, self-healing. Concept-first. |
| [`developer/`](developer/README.md) | Implementation spec — Python names, data contracts, node wiring. Includes per-field surface tables. |
| [`operations/`](operations/README.md) | Running it — CLI + env, backend integration, persistence + recovery, observability |
| [`methods/`](methods/README.md) | The two spend-control procedures: PoBB elimination + hard-sample dashboard |
| [`research/`](research/README.md) | Benchmarks, metrics, related work |
| [`specs/`](specs/CLAUDE.md) | Roadmap + forward-looking specs |
| `assets/` | Images and diagrams |
