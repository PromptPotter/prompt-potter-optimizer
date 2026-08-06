# docs/specs — Specs Index

**Read [`docs/architecture.md`](../architecture.md) first** — §0 is the shape, §0.5 the load-bearing surface.

Deliberately small: forward direction lives in one roadmap; the rest are living contracts. Superseded/done specs were removed — recover via `git log`.

| File | What |
|---|---|
| [roadmap.md](roadmap.md) | **Forward direction** — execution-ordered lanes + the folded per-milestone design notes (origin check-in, ingest/chat-web, connectors/L4, prompt-iteration framework, agent-tool parity, BYO keys, operator-steered fork, state-sync). The lane table's **Status column** is truth for what shipped. |
| [code-debt-cleanup.md](code-debt-cleanup.md) | Living debt backlog — open items only; `git log` is the history layer. |
| [frontend-surface-contract.md](frontend-surface-contract.md) | Per-control webapp behavior per auth/data state + the **`I*` invariant block** (the file's own enumeration — every user-facing PR is measured against it). |
| [chat-foundation.md](chat-foundation.md) | The chat-first front door (Lane C1): thread model, `ProjectionEnvelope → ActivityItem` translator, copilot decision buttons (existing verbs), campaign-scoped persistence, reusable-template seam. |
| [fitness-comparability.md](fitness-comparability.md) | The θ/accuracy boundary collapse — gating fitness is the 1PL Rasch ability θ on one fixed ruler, subset-invariant; resubset ON; 1PL→2PL graduation per-dataset. Open: the cross-round headline surfaces + feeding graduated discrimination into selection. |
| [schema-description-axis.md](schema-description-axis.md) | **Built, unmeasured.** `Field(description=)` is LLM-facing copy no parser reads, so it is free to mutate. Resolved at the `build_l1_response_schema` seam off the per-node override channel `layout` uses — Pydantic stays the sole default, no lift, no table. Schema-driven on **every `output_schema`-bearing target node**, never against the optimizer's own `l1_generate` schema. A negative `--sweep` closes the spec by reverting. |
| [l4-outer-loop.md](l4-outer-loop.md) | **The living finish-line plan for L4 — read § Finish line first; it is the single owner of L4 status**, including what the outer panel may claim about a leader. Also the specialized outer optimizer prompt set and the enriched outer composite fitness. |
| [m12-api-openapi.yaml](m12-api-openapi.yaml) · [m12-events-asyncapi.yaml](m12-events-asyncapi.yaml) | **Control-plane contracts** (verified by review + schema lint; no standing test — the structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)): the closed inbound command set + outbound event set. Declare schema here *before* a handler lands. |

Permanent constitutions live in [`docs/adr/`](../adr/): [0001 control-plane](../adr/0001-m12-control-plane.md) · [0002 identity](../adr/0002-identity-foundation.md) · [0003 spend/tenancy](../adr/0003-spend-and-tenancy.md) · [0004 operator-admin channels](../adr/0004-operator-admin-channels.md).

## What may live here

**`specs/` describes direction of travel — a past-tense fact about how shipped behavior works belongs in `docs/concepts/` / `developer/` / `operations/` instead.** A spec that has fully shipped is finished, not archived: delete it and recover from `git log`. The failure this prevents is the tree's most common drift — a spec that shipped months ago still reading as "gating", which costs a reader a re-plan of work that already exists.
