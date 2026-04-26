# Operations

Running, integrating, and operating PromptPotter. If you're driving the system — not studying its internals — this is your folder.

| Page | What it covers |
|------|----------------|
| [CLI reference](cli-reference.md) | Every subcommand, flag, and worked example |
| [Environment](environment.md) | Env variables, optional extras bundles, Docker |
| [Backend integration](backend-integration.md) | Contract a backend must implement (`/matches`, `/pipeline`, `/status`) and REST API endpoints |
| [Persistence and state](persistence-and-state.md) | The `.promptpotter/` tree, active session pointer, cycle directory schema |
| [Rewind and fork](rewind-and-fork.md) | `optimize --from N` and `optimize --fork-on-divergence` — recovering from bad trajectories or scorer changes |
| [Observability](observability.md) | Langfuse integration and what gets traced |

New to PromptPotter? Start in [`../manual/`](../manual/README.md), not here.
