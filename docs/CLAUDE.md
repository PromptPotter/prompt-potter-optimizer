# docs/ — documentation tree

This file is the **index for AI/agent readers** over `docs/`. The operator-facing index is [`README.md`](README.md). Load only the subtree you need.

> **Read [`architecture.md`](architecture.md) first.** §0 is the shape on one page; §0.5 is the load-bearing surface that every PR measures against. Everything below is progressive disclosure off §0.

## Top-level

| File | Role |
|---|---|
| [`architecture.md`](architecture.md) | **§0 + §0.5 — the single page every PR measures against.** AI entry point. |
| [`glossary.md`](glossary.md) | Domain vocabulary; one line per term with canonical implementation file. Read before introducing a new word. |
| [`roadmap.md`](roadmap.md) | Milestone-level direction; the live forward-looking version lives at [`specs/roadmap.md`](specs/roadmap.md). |
| [`README.md`](README.md) | Operator-facing index (the friendly door). |

## Subtrees

| Folder | When to load | Index |
|---|---|---|
| [`manual/`](manual/) | Operator onboarding: install → first campaign → reading output → troubleshooting → going deeper. Numbered chapters. | [`manual/README.md`](manual/README.md) |
| [`concepts/`](concepts/) | How the loop works conceptually — the three-layer loop, scoring + memory, candidate-elimination, **optimizer-of-the-optimizer (L4 recursion)**, campaign tree, paired-sample PoBB. Read before the developer docs. | [`concepts/README.md`](concepts/README.md) |
| [`developer/`](developer/) | Implementation specs — Python names, data contracts, node wiring, `pipeline.json` contract, dispatch hub, L1-generate surface, L1-candidate-analysis checklist, self-healing internals, **`conventions.md` (full style + code-shape rules)**, `stable-api.md` (v1 fork-readiness surface). | [`developer/README.md`](developer/README.md) |
| [`operations/`](operations/) | Running it — CLI reference, env, persistence + recovery, observability, backend integration, **`secure-hosting.md`** (allowlist admin via the on-box bot), **`adding-a-dataset.md`**, **`dataset-selection-rationale.md`**, **`dataset-reasoning-matrix.md`** (per-dataset model + `reasoning_effort` + `max_tokens` defaults). | [`operations/README.md`](operations/README.md) |
| [`methods/`](methods/) | The two spend-control procedures: PoBB elimination + hard-sample leaderboard. | [`methods/README.md`](methods/README.md) |
| [`research/`](research/) | Benchmarks (BBEH comparison, pEvol-bench), metrics, related-work table (incl. MCTS comparison). | [`research/README.md`](research/README.md) |
| [`specs/`](specs/) | Forward-looking specs — identity-foundation, multi-connector + L4 closure, control plane, publication benchmarks, spend + tenancy, M13 chat-first user web. **Specs index has its own CLAUDE.md.** | [`specs/CLAUDE.md`](specs/CLAUDE.md) |
| [`template/`](template/) | Fork recipe — framework/specifics split, what to replace, what you get for free. Read when starting a new Python + webtech project on top of PromptPotter. | [`template/README.md`](template/README.md) |
| `assets/` | Images and diagrams; no contract. | n/a |

## Anchor docs for hot questions

| Question | Read first |
|---|---|
| What is the shape of this project? | [`architecture.md`](architecture.md) §0 |
| What is the load-bearing surface? | [`architecture.md`](architecture.md) §0.5 |
| What does this domain word mean? | [`glossary.md`](glossary.md) |
| How does information flow through L1 / L2 / L3? | [`developer/dispatch-hub.md`](developer/dispatch-hub.md) |
| How do I add a record / injection / view-field / connector without half-wiring it? | [`developer/adding-a-surface.md`](developer/adding-a-surface.md) (recipe + the CI guard per surface) |
| How does a layer heal a failure? | [`developer/self-healing-internals.md`](developer/self-healing-internals.md) |
| How is L1's evidence surface built? | [`developer/l1-generate-surface.md`](developer/l1-generate-surface.md) |
| **What is L4 / how does PromptPotter optimize itself?** | [`concepts/optimizer-of-the-optimizer.md`](concepts/optimizer-of-the-optimizer.md) + [`specs/m12-multi-connector.md`](specs/m12-multi-connector.md) |
| What datasets do we use? Why didn't we use Y? | [`operations/dataset-selection-rationale.md`](operations/dataset-selection-rationale.md) |
| What model + `reasoning_effort` for this dataset? | [`operations/dataset-reasoning-matrix.md`](operations/dataset-reasoning-matrix.md) (canonical — NOT meta-campaign NOTES.md) |
| What's the canonical split for this benchmark? | [`operations/adding-a-dataset.md`](operations/adding-a-dataset.md) |
| How do I run the auth-on dashboard locally? | [`developer/local-oidc.md`](developer/local-oidc.md) (Dex harness at `dev/oidc-local/`) — only needed for the real OIDC login round-trip |
| How do I drive the authed/live UI surface (no OIDC, no spend)? | Relaunch with `PROMPTPOTTER_AUTH=off` → reads your real on-disk campaigns. Recipe + the per-control behavior bar: [`../webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture + [`specs/frontend-surface-contract.md`](specs/frontend-surface-contract.md) |
| How do I manage the sign-in allowlist / host securely? | [`operations/secure-hosting.md`](operations/secure-hosting.md) + [`adr/0004-operator-admin-channels.md`](adr/0004-operator-admin-channels.md) |
| How do I freeze a buggy cycle as a test fixture? | [`developer/cycle-fixtures.md`](developer/cycle-fixtures.md) (`tests/fixtures/cycles/`) |

## L4 — the recursion case

L4 (PromptPotter optimizing its own meta-prompts) lives at the **intersection** of three docs — they form a coherent triple, read them together:

1. [`concepts/optimizer-of-the-optimizer.md`](concepts/optimizer-of-the-optimizer.md) — why, the three composable proxies, cost realism.
2. [`specs/m12-multi-connector.md`](specs/m12-multi-connector.md) § Track 1.5 — the spec, including the two inner-cycle execution design options (localhost endpoint vs in-process dispatch).
3. [`../promptpotter/connectors/CLAUDE.md`](../promptpotter/connectors/CLAUDE.md) — what the connector boundary taught us; the second connector's noop session lessons; the open execution path.

The dataset side: [`../datasets/CLAUDE.md`](../datasets/CLAUDE.md) § L4 — `promptpotter-self`.

## Out-of-bounds

- Documentation does **not** define behavior — code does. When a doc and the code disagree, the code wins and the doc gets updated. (Exception: `architecture.md` § 0 / 0.5, which IS a contract — code that disagrees gets fixed.)
- Specs in `specs/` are forward-looking. Past-tense facts about how the system works belong in `concepts/` / `developer/` / `operations/`; `specs/` describes direction of travel.
- Per-layer CLAUDE.md files in `promptpotter/*/` are the **per-package contracts** — keep this index honest about which doc holds which fact; don't duplicate.
