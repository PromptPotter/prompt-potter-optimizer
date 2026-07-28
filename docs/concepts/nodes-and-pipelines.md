# Nodes and Pipelines

A pipeline is a sequence of steps the backend runs for each query. Each step is a node. **The optimizer loop itself is built from nodes — same JSON declaration format as backend pipeline steps, same registry.** This is what lets the optimizer self-inspect: search memory tracks warnings from both sides; self-healing applies to both; patience counters watching a backend degrade and an optimizer stall are the same shape.

Capabilities are opt-in (a deterministic lookup node declares none). An LLM node in the optimizer loop may declare:

- **Prompt exposure** — broken into named fields ([`state-record.md`](state-record.md)).
- **Optimizer-discoverable parameters** — node declares accepted parameters + valid values; PromptPotter picks them up automatically as optimization axes, no hardcoding either side.
- **Validation-failure healing (Wound 1)** + **runtime-failure healing (Wound 2)** + **warnings as escalation signals** — see [`../developer/self-healing-internals.md`](../developer/self-healing-internals.md).
- **Exit-point declaration** — where the node's output lives, so cache reuse works across configs that share a prefix.
- **Skip and abort** — degraded candidates eliminated mid-run; remaining candidates continue.

Three reasons nodes-not-monoliths (mirrors prompt decomposition): measurable axes per node · independent mutation (cache reuses up to the changed node) · extensibility without coupling (anyone can write a node — JSON registers it).

## Pointers

- JSON declaration format + how to wire a new node: [`../developer/node-standard.md`](../developer/node-standard.md)
- Pipeline JSON contract: [`../developer/pipeline-contract.md`](../developer/pipeline-contract.md)
- Backend integration recipe: [`../operations/backend-integration.md`](../operations/backend-integration.md)
