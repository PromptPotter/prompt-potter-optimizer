# Nodes and Pipelines

A pipeline is a sequence of steps the backend runs for each query. Each step is a node. Nodes compose freely — the same node can appear in multiple pipelines, and different nodes can do very different things.

The optimizer loop itself is built from nodes. So is every backend pipeline. The unit of composition is uniform across both sides.

---

## What a node can do

Capabilities are opt-in. A deterministic lookup node declares none of them. An LLM node participating in the optimizer loop may use all of them.

### Everything a node declares

- **Exit-point declaration** — a node that produces candidates declares where its output lives. PromptPotter reads this to find the last active exit point, enabling cache reuse across configurations that share a prefix.
- **Escalation signals** — a node signals the orchestrator to eliminate a candidate or abort a round, rather than failing silently.

### Extra capabilities for LLM nodes

- **Prompt exposure.** An LLM node exposes its prompt so PromptPotter can read, display, and optimize it. The prompt is broken into named fields — see [prompts-and-individuals.md](prompts-and-individuals.md) for the decomposition.
- **Optimizer-discoverable parameters.** The node declares which parameters it accepts and their valid values. PromptPotter picks these up automatically as optimization axes — no hardcoding required on either side. This is what makes a node tunable without any PromptPotter code knowing the node's internals.
- **Rail 1 self-healing.** If the optimizer proposes a parameter value the node doesn't accept, the proposal is rejected before any run. The optimizer learns and won't propose it again. See [self-healing.md](self-healing.md).
- **Rail 2 self-healing.** If a candidate's configuration produces degraded results consistently, the failure is pinned to that configuration and the optimizer adjusts strategy. See [self-healing.md](self-healing.md).
- **Warnings as optimizer context.** Per-query warnings from the node accumulate and feed the optimizer as context, even when no hard failure has fired.
- **Warnings as escalation signals.** Sustained degradation increments a patience counter. When patience runs out, the orchestrator escalates to a higher layer or halts the round.
- **Warnings attached to search points.** Failures are pinned to the exact configuration that caused them, not to the round. Future proposals that resemble the failing configuration are penalized.
- **Skip and abort.** A candidate producing too many degraded or empty results is eliminated mid-run; the remaining candidates continue. A candidate can also signal that the round should stop entirely.
- **Fatal fast-path.** Certain failure codes eliminate a candidate on the very first query, with no rate threshold.

---

## Why nodes instead of a monolithic pipeline

The same three reasons that make prompt decomposition valuable apply to pipeline decomposition.

**Measurable axes.** Each node's parameters are a separate axis. The optimizer can track which node's tuning actually moves scores and which node's parameters might as well be constants.

**Independent mutation.** A candidate can change one node's parameters without touching the others. The cache reuses per-query results up to the node that changed — everything before that node replays from storage.

**Extensibility without coupling.** Anyone can write a node. A JSON declaration registers it. PromptPotter doesn't need to know the node's internals — only its declared capabilities.

---

## The optimizer loop is a pipeline too

L1 Generate is a node. The critique step is a node. L2 Refine and L3 Plan are nodes. They share the same JSON declaration format as backend pipeline steps, live in the same registry, and emit the same kind of diagnostic warnings.

This is what lets the optimizer self-inspect. Search memory tracks warnings from both the backend pipeline and the optimizer loop. Self-healing applies to both. The patience counters that drive escalation are the same shape whether they're watching a backend node degrade or an optimizer node stall.

For the JSON declaration format and how to wire a new node, see [../developer/node-standard.md](../developer/node-standard.md).
