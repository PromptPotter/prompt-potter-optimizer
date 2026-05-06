# PromptPotter Documentation

PromptPotter tunes prompts and pipeline configs against a labelled dataset. The whole design is **maximize fitness, minimize spend**.

**New here? → [`manual/`](manual/README.md).** Numbered chapters: install → first run → reading output → troubleshooting.

| Folder | Purpose |
|--------|---------|
| [![manual/](https://img.shields.io/badge/manual%2F-red?style=for-the-badge)](manual/README.md) | User walkthrough |
| [![concepts/](https://img.shields.io/badge/concepts%2F-black?style=for-the-badge)](concepts/README.md) | How it works — three-layer loop with CONTEXT and PLAN, spend control, self-healing. Concept-first. |
| [![developer/](https://img.shields.io/badge/developer%2F-red?style=for-the-badge)](developer/README.md) | Implementation spec — Python names, data contracts, node wiring. Includes per-field surface tables. |
| [![operations/](https://img.shields.io/badge/operations%2F-black?style=for-the-badge)](operations/README.md) | Running it — CLI + env, backend integration, persistence + recovery, observability |
| [![methods/](https://img.shields.io/badge/methods%2F-red?style=for-the-badge)](methods/README.md) | The two spend-control procedures: PoBB elimination + hard-sample dashboard |
| [![research/](https://img.shields.io/badge/research%2F-black?style=for-the-badge)](research/README.md) | Benchmarks, metrics, related work |
| [![specs/](https://img.shields.io/badge/specs%2F-red?style=for-the-badge)](specs/CLAUDE.md) | Roadmap + forward-looking specs |
| ![assets/](https://img.shields.io/badge/assets%2F-black?style=for-the-badge) | Images and diagrams |
