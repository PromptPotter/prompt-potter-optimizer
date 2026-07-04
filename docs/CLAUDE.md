# docs/ — documentation tree

This file is the **index for AI/agent readers** over `docs/`. The operator-facing index is [`README.md`](README.md). Load only the subtree you need.

> **Read [`architecture.md`](architecture.md) first.** §0 is the shape on one page; §0.5 is the load-bearing surface that every PR measures against. Everything below is progressive disclosure off §0.

## Top-level

| File | Role |
|---|---|
| [`architecture.md`](architecture.md) | **§0 + §0.5 — the single page every PR measures against.** AI entry point. |
| [`glossary.md`](glossary.md) | Domain vocabulary; one line per term with canonical implementation file. Read before introducing a new word. |
| [`README.md`](README.md) | Operator-facing index (the friendly door) + the short status/roadmap summary; the live forward plan is [`specs/roadmap.md`](specs/roadmap.md). |

## Subtrees

| Folder | When to load | Index |
|---|---|---|
| [`manual/`](manual/) | Operator onboarding: install → first campaign → reading output → troubleshooting → going deeper. Numbered chapters. | [`manual/README.md`](manual/README.md) |
| [`concepts/`](concepts/) | How the loop works conceptually — the three-layer loop, scoring + memory, candidate-elimination, **optimizer-of-the-optimizer (L4 recursion)**, campaign tree, paired-sample PoBB. Read before the developer docs. | [`concepts/README.md`](concepts/README.md) |
| [`developer/`](developer/) | Implementation specs — Python names, data contracts, node wiring, `pipeline.json` contract, dispatch hub + L1 layout, L1-candidate-analysis checklist (incl. the meta-campaign lookup), self-healing internals, **`conventions.md` (full style + code-shape rules)**, `stable-api.md` (v1 fork-readiness surface). | [`developer/README.md`](developer/README.md) |
| [`operations/`](operations/) | Running it — CLI reference, env, persistence + recovery, observability, backend integration, **`secure-hosting.md`** (allowlist admin via the on-box bot), **`adding-a-dataset.md`**, **`dataset-selection-rationale.md`**, **`dataset-reasoning-matrix.md`** (per-dataset model + `reasoning_effort` + `max_tokens` defaults). | [`operations/README.md`](operations/README.md) |
| [`methods/`](methods/) | The two spend-control procedures: PoBB elimination + hard-sample leaderboard. | [`methods/README.md`](methods/README.md) |
| [`research/`](research/) | Benchmarks (BBEH comparison + the PEvol-Bench definition), metrics, related-work table (incl. MCTS comparison). | [`research/README.md`](research/README.md) |
| [`specs/`](specs/) | Forward direction in one [`roadmap.md`](specs/roadmap.md) + living contracts (verdict-resolution, frontend-surface-contract, chat-foundation, the two control-plane YAMLs) + the debt backlog. **Specs index has its own CLAUDE.md.** | [`specs/CLAUDE.md`](specs/CLAUDE.md) |
| `assets/` | Images and diagrams; no contract. | n/a |

## Anchor docs for hot questions

| Question | Read first |
|---|---|
| Where does concept X live? | [`developer/concept-map.md`](developer/concept-map.md) |
| What is the shape of this project? | [`architecture.md`](architecture.md) §0 |
| What is the load-bearing surface? | [`architecture.md`](architecture.md) §0.5 |
| What does this domain word mean? | [`glossary.md`](glossary.md) |
| What's the difference between origin, check-in, and round-0/C0? | [`architecture.md`](architecture.md) §0.5 (the start-definitions bullet) |
| Under which fitness formula? active / what-if / lens / replay, `composite_fitness` vs `accuracy` | [`architecture.md`](architecture.md) §0.5 (Composite-fitness resolution chain) + [`concepts/scoring-and-memory.md`](concepts/scoring-and-memory.md) |
| The situational reasoning doctrines (simplify-the-problem / surface-ledger / reach-the-operator)? | [`developer/conventions.md`](developer/conventions.md) § Reasoning doctrine (the two universal gates stay in root [`CLAUDE.md`](../CLAUDE.md)) |
| Debugging the PP↔TermNorm highway (async hygiene, `--reload` session wipe, latency)? | [`operations/backend-integration.md`](operations/backend-integration.md) § Debugging the highway |
| How does information flow through L1 / L2 / L3? | [`developer/dispatch-hub.md`](developer/dispatch-hub.md) |
| How do I add a record / injection / view-field / connector without half-wiring it? | [`developer/adding-a-surface.md`](developer/adding-a-surface.md) (recipe + the CI guard per surface) |
| How does a layer heal a failure? | [`developer/self-healing-internals.md`](developer/self-healing-internals.md) |
| How is L1's evidence surface built? | [`developer/dispatch-hub.md`](developer/dispatch-hub.md) § L1 layout |
| **What is L4 / how does PromptPotter optimize itself?** | [`concepts/optimizer-of-the-optimizer.md`](concepts/optimizer-of-the-optimizer.md) + [`specs/roadmap.md`](specs/roadmap.md) |
| What datasets do we use? Why didn't we use Y? | [`operations/dataset-selection-rationale.md`](operations/dataset-selection-rationale.md) |
| What model + `reasoning_effort` for this dataset? | [`operations/dataset-reasoning-matrix.md`](operations/dataset-reasoning-matrix.md) (canonical — NOT meta-campaign NOTES.md) |
| What's the canonical split for this benchmark? | [`operations/adding-a-dataset.md`](operations/adding-a-dataset.md) |
| How do I run the auth-on dashboard locally? | [`developer/local-oidc.md`](developer/local-oidc.md) (Dex harness at `dev/oidc-local/`) — only needed for the real OIDC login round-trip |
| How do I drive the authed/live UI surface (no OIDC, no spend)? | Relaunch with `PROMPTPOTTER_AUTH=off` → reads your real on-disk campaigns. Recipe + the per-control behavior bar: [`../webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture + [`specs/frontend-surface-contract.md`](specs/frontend-surface-contract.md) |
| How do I manage the sign-in allowlist / host securely? | [`operations/secure-hosting.md`](operations/secure-hosting.md) + [`adr/0004-operator-admin-channels.md`](adr/0004-operator-admin-channels.md) |
| How do I freeze a buggy cycle as a test fixture? | [`developer/cycle-fixtures.md`](developer/cycle-fixtures.md) (`tests/fixtures/cycles/`) |

## L4 — the recursion case (project's closing focus)

L4 (PromptPotter optimizing its own meta-prompts) **recursion is SHIPPED & live-validated**; the project is now finishing it into a **distributable `promptpotter-self`**. An AI agent driving L4 reads **(2) first** — it is the living finish-line plan + the SoT.

1. [`concepts/optimizer-of-the-optimizer.md`](concepts/optimizer-of-the-optimizer.md) — why, the composed outer fitness (lift × quality × efficiency + the candidate-gradient law), cost realism (status note points back to the plan).
2. **[`specs/l4-outer-loop.md`](specs/l4-outer-loop.md) — the living finish-line plan: § Finish line (distributable goal), § Live-run learnings (MAX_PATH flat `.inner/` registry, gsm8k→`justlogic` headroom, slice-3-is-gating), the slice order, the named seams.** Read first.
3. [`../promptpotter/connectors/CLAUDE.md`](../promptpotter/connectors/CLAUDE.md) — the connector boundary + the shipped `in_process` seam + the flat sandbox registry.

The dataset side: [`../datasets/CLAUDE.md`](../datasets/CLAUDE.md) § L4 — `promptpotter-self`.

## Out-of-bounds

- Documentation does **not** define behavior — code does. When a doc and the code disagree, the code wins and the doc gets updated. (Exception: `architecture.md` § 0 / 0.5, which IS a contract — code that disagrees gets fixed.)
- Specs in `specs/` are forward-looking. Past-tense facts about how the system works belong in `concepts/` / `developer/` / `operations/`; `specs/` describes direction of travel.
- Per-layer CLAUDE.md files in `promptpotter/*/` are the **per-package contracts** — keep this index honest about which doc holds which fact; don't duplicate.
