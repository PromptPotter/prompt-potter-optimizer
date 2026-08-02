# docs/specs — Specs Index

**Read [`docs/architecture.md`](../architecture.md) first** — §0 is the shape, §0.5 the load-bearing surface.

Deliberately small: forward direction lives in one roadmap; the rest are living contracts. Superseded/done specs were removed — recover via `git log`.

| File | What |
|---|---|
| [roadmap.md](roadmap.md) | **Forward direction** — execution-ordered lanes + the folded per-milestone design notes (origin check-in, ingest/chat-web, connectors/L4, prompt-iteration framework, agent-tool parity, BYO keys, operator-steered fork, state-sync). The lane table's **Status column** is truth for what shipped. |
| [code-debt-cleanup.md](code-debt-cleanup.md) | Living debt backlog — open items only; `git log` is the history layer. |
| [verdict-resolution.md](verdict-resolution.md) | The single statistical model behind the live adaptive queue + the persisted `hard_samples.json` ranking. |
| [frontend-surface-contract.md](frontend-surface-contract.md) | Per-control webapp behavior per auth/data state + the **`I*` invariant block** (the file's own enumeration — every user-facing PR is measured against it). |
| [chat-foundation.md](chat-foundation.md) | The chat-first front door (Lane C1): thread model, `ProjectionEnvelope → ActivityItem` translator, copilot decision buttons (existing verbs), campaign-scoped persistence, reusable-template seam. |
| [fitness-comparability.md](fitness-comparability.md) | The θ/accuracy boundary collapse — **slices 1–3 SHIPPED** (gating fitness = the 1PL Rasch ability θ on one fixed ruler, subset-invariant; resubset ON; 1PL→2PL graduation per-dataset). Open: slice-4's cross-round headline surfaces + feeding graduated discrimination into selection. Prerequisite to l4-outer-loop — satisfied. |
| [schema-description-axis.md](schema-description-axis.md) | **Built, unmeasured.** `Field(description=)` is LLM-facing copy no parser reads, so it is free to mutate. Resolved at the `build_l1_response_schema` seam off the per-node override channel `layout` uses — Pydantic stays the sole default, no lift, no table. Schema-driven on **every `output_schema`-bearing target node** (an earlier build wired it one level too high, against the optimizer's own `l1_generate` schema; that seam was replaced). A negative `--sweep` closes the spec by reverting. |
| [l4-outer-loop.md](l4-outer-loop.md) | **The living finish-line plan for L4 — recursion is SHIPPED + live-validated; read § Finish line first.** In-process inner-cycle recursion (own asyncio task; inner campaigns in a **flat `<workspace>/.inner/<key>/` registry** — NOT nested under `.runtime/inner/`, which blew Windows' `MAX_PATH`), the shared `in_process` seam (the `llm_only` connector it also yielded is withdrawn — zero adopters), the specialized outer optimizer prompt set (SHIPPED `28f9c720`; it was listed as "gating" here for months after), and the enriched outer composite fitness. The live gate is now item 7 — the panel cannot yet tell an arm from a re-read of itself. |
| [mask-projection.md](mask-projection.md) | The mask framing (record / divergence / invariant-vs-divergent) + the deferred fork-from-divergence write-side (Lane C8). Read-side is shipped; `application/mask/` is the code SoT. |
| [m12-api-openapi.yaml](m12-api-openapi.yaml) · [m12-events-asyncapi.yaml](m12-events-asyncapi.yaml) | **Control-plane contracts** (verified by review + schema lint; no standing test — the structural/contract suite was cut, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)): the closed inbound command set + outbound event set. Declare schema here *before* a handler lands. |

Permanent constitutions live in [`docs/adr/`](../adr/): [0001 control-plane](../adr/0001-m12-control-plane.md) · [0002 identity](../adr/0002-identity-foundation.md) · [0003 spend/tenancy](../adr/0003-spend-and-tenancy.md) · [0004 operator-admin channels](../adr/0004-operator-admin-channels.md).

## What may live here

**`specs/` describes direction of travel — a past-tense fact about how shipped behavior works belongs in `docs/concepts/` / `developer/` / `operations/` instead.** A spec that has fully shipped is finished, not archived: delete it and recover from `git log`. The failure this prevents is the tree's most common drift — a spec that shipped months ago still reading as "gating", which costs a reader a re-plan of work that already exists.
