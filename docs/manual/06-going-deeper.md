# Going deeper

You've run a campaign. Pointers below for the next layer.

## Concepts — how it works

| Page | Covers |
|------|--------|
| [Three-layer loop](../concepts/the-loop.md) | Why L1/L2/L3 exist and when each fires |
| [Self-healing](../concepts/self-healing.md) | Recovery from bad proposals + degraded runs |
| [Scoring and memory](../concepts/scoring-and-memory.md) | Traces are facts, scores are policy + the cross-campaign archive |
| [State record](../concepts/state-record.md) | The candidate's state record (`OptSearchPoint` in code) — carries task context, plan, prompt fields, L2 overrides |
| [Campaign tree](../concepts/campaign-tree.md) | Cycles, forks, and the sweep primitive |
| [Nodes and pipelines](../concepts/nodes-and-pipelines.md) | Pipeline node anatomy |
| [Glossary](../concepts/glossary.md) | Terms used across the docs |

## Operations

| Page | Covers |
|------|--------|
| [CLI reference](../operations/cli-reference.md) | Every subcommand (`optimize`, `compare`, `sweep`), flag, and env variable |
| [Backend integration](../operations/backend-integration.md) | The contract a backend must implement |
| [Persistence and state](../operations/persistence-and-state.md) | The `.promptpotter/` tree, resume, rewind, fork, scoring steer |
| [Observability](../operations/observability.md) | Langfuse integration |

## Developer — implementation

| Page | Covers |
|------|--------|
| [Developer README](../developer/README.md) | Prompt structure, request routing, the scoring step, learning from prior campaigns + per-field reference tables |
| [L2 internals](../developer/l2-internals.md) | L2 firing, output, OSP mutations, layout edits |
| [L1 layout + dispatch hub](../developer/l1-generate-surface.md) | `INJECTIONS` registry, `L1Layout`, `DispatchHub` |
| [Self-healing internals](../developer/self-healing-internals.md) | Failure classification, escalation wiring |
| [Node standard](../developer/node-standard.md) | Wiring a new pipeline node |

## Methods — the statistics

- [Candidate elimination](../methods/candidate-elimination.md) — Bayesian Posterior-of-Being-Best (PoBB).
- [Exploration/exploitation sample selection](../methods/exploration-exploitation.md) — Rasch + Knowledge Gradient.

## Research — benchmarks and the paper

- [Benchmarks](../research/benchmarks.md) — datasets, methodology, origins.
- [Metrics](../research/metrics.md) — beyond absolute accuracy (HC, SE, R₉₀).
- [Related work](../research/related-work.md) — competitor positioning.

---

## Iterating on prompts manually

Hand-tuning `l1_generate` (or another optimizer meta-prompt) is owned by [`/potter-l1-meta-campaign`](../../.claude/skills/potter-l1-meta-campaign/SKILL.md) — a same-command-every-tick strategist that reads cycle artifacts, applies the round-1 verdict + top-issue ranking, and writes one proposed edit per non-healthy cycle to `.promptpotter/meta_campaigns/{prompt_id}/proposed_edits/`. State persists on disk; ten ticks in a row produce ten consistent decisions.

Full M10 spec: [`../specs/m10-prompt-iteration-framework.md`](../specs/m10-prompt-iteration-framework.md).
