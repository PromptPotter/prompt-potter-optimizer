# docs/specs — Specs Index

**Read [`docs/architecture.md`](../architecture.md) first** — §0 is the shape, §0.5 the load-bearing surface.

Deliberately small: forward direction lives in one roadmap; the rest are living contracts. Superseded/done specs were removed — recover via `git log`.

| File | What |
|---|---|
| [roadmap.md](roadmap.md) | **Forward direction** — execution-ordered lanes + the folded per-milestone design notes (origin check-in, ingest/chat-web, connectors/L4, prompt-iteration framework, BYO keys, operator-steered fork, state-sync). `Status:` lines are truth for what shipped. |
| [code-debt-cleanup.md](code-debt-cleanup.md) | Living debt backlog — open items only; `git log` is the history layer. |
| [verdict-resolution.md](verdict-resolution.md) | The single statistical model behind the live adaptive queue + the persisted `hard_samples_*.json` ranking. |
| [frontend-surface-contract.md](frontend-surface-contract.md) | Per-control webapp behavior per auth/data state + 5 invariants; every user-facing PR is measured against it. |
| [chat-foundation.md](chat-foundation.md) | The chat-first front door (Lane C1): thread model, `ProjectionEnvelope → ActivityItem` translator, copilot decision buttons (existing verbs), campaign-scoped persistence, reusable-template seam. |
| [fitness-comparability.md](fitness-comparability.md) | Collapse the θ/accuracy boundary: gating fitness becomes the existing 1PL Rasch ability θ (subset-invariant) so per-round resubset is safe to turn on; 1PL→2PL graduation gated per-dataset; max-information selection + θ-CI stopping. **Prerequisite to l4-outer-loop.** |
| [schema-description-axis.md](schema-description-axis.md) | **Built, unmeasured.** `Field(description=)` is LLM-facing copy no parser reads, so it is free to mutate. Resolved at the `build_l1_response_schema` seam off the per-node override channel `layout` uses — Pydantic stays the sole default, no lift, no table. Live on `promptpotter-self`; a negative `--sweep` closes the spec by reverting. |
| [l4-outer-loop.md](l4-outer-loop.md) | The L4 outer loop (Lane C3): in-process inner-cycle recursion (own asyncio task + `.runtime/inner/` sandbox), the shared `in_process` seam that also yields an in-process `llm_only` connector, the specialized outer meta-prompt set, and the enriched outer composite fitness. |
| [storage-architecture.md](storage-architecture.md) | **Persistence target** — store-once invariant, `archive/` as recycle bin + `measurements/` relocation, lean ledger writers (dataset ref not embed), destructive delete + keep-results, MECE storage partition. |
| [mask-projection.md](mask-projection.md) | The mask framing (record / divergence / invariant-vs-divergent) + the deferred fork-from-divergence write-side (Lane C8). Read-side is shipped; `application/mask/` is the code SoT. |
| [m12-api-openapi.yaml](m12-api-openapi.yaml) · [m12-events-asyncapi.yaml](m12-events-asyncapi.yaml) | **Control-plane contracts** (verified by review + schema lint; no standing test — the structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)): the closed inbound command set + outbound event set. Declare schema here *before* a handler lands. |

Permanent constitutions live in [`docs/adr/`](../adr/): [0001 control-plane](../adr/0001-m12-control-plane.md) · [0002 identity](../adr/0002-identity-foundation.md) · [0003 spend/tenancy](../adr/0003-spend-and-tenancy.md) · [0004 operator-admin channels](../adr/0004-operator-admin-channels.md).

Past-tense facts about how shipped behavior works belong in `docs/concepts/` / `developer/` / `operations/`, not here.
