# Roadmap

PromptPotter is in alpha. This page is the short version. The full development plan with milestones, specs, and acceptance gates lives at [`specs/roadmap.md`](specs/roadmap.md).

## Shipped

- **Prompt + pipeline optimization.** Three-layer optimizer loop (generate / score / critique → framing refinement → strategic replan) over the dataset's pipeline.
- **Statistical early-stopping.** Posterior-based candidate elimination so weak candidates exit before they finish the full sample budget.
- **Cross-run memory.** The MeasurementArchive records every per-sample measurement; future runs cache-hit on identical work and inform the next round's exploration.
- **Read-only operator dashboard.** A live view of the active campaign at `/ui`, served from a static Next.js export.
- **Connector boundary + TermNorm.** The first registered connector; the wire adapter, session lifecycle, and experiment-data extraction sit behind one `Connector` shape.

## Next

- **Multi-connector.** A second connector + competitor head-to-head on a published benchmark.
- **Control plane.** Operator-driven launch / stop / resume / fork from the webapp.
- **Composite fitness.** Multi-objective scoring (accuracy / cost / latency) instead of one scalar.

## Direction

PromptPotter is shaped like AlphaZero-MCTS over a lineage tree. The strategic-replan layer already emits an observation-only fork proposal when it judges the current subtree exhausted; backpropagating round outcomes up the tree + UCB-style ancestor selection are the two remaining halves.

Full plan: [`specs/roadmap.md`](specs/roadmap.md).
