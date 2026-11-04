# Host assistant: the account's one conversation, and the surface every host drives

> **Only the open half lives here.** The boundary is [`../decisions.md`](../decisions.md) § 0012: the host
> is whoever the user talks to, the origin gate (`application/datasets/origin_readiness.py::origin_readiness`)
> stays the contract, and the account's own assistant acts as the user. The built thread's rules are
> [`../../webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § The chat thread; the reusable chat core is
> [`../../webapp/components/chat/README.md`](../../webapp/components/chat/README.md).

## The surface: MCP's three primitives

**One registry, every host, no adapter adds a tool.**

- **Tools.** Writes are the `domain/command_kinds.py::CommandKind` rows, already capability-gated. Reads
  are the `domain/command_kinds.py::ReadKind` rows, each naming the `application/` callable that answers
  it; every session route is bound to one row (`presentation/api/catalogue.py::bind_surface`), so the
  tool list is the whole surface.
- **Resources.** The account's data, addressed by the hierarchy address grammar
  ([`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § The hierarchy address).
- **Prompts.** Templates. The check-in is one — "reach the origin gate", seeded from the `checkin/2`
  prompt text in `assets/checkin/pipeline.yaml`; later templates are more prompts, never more code paths.

**Two transports**: in-process for the webapp's host; MCP stdio for a local host now, streamable HTTP on
R1's credential later ([`roadmap.md`](roadmap.md) § Agent-tool parity). The MCP SDK is an extra (§ 0006).
A long-running call returns a task handle per MCP's Tasks extension
([`../research/external-constraints.md`](../research/external-constraints.md) § TOOL and interop).

## The webapp's host

- **A server-side loop of model, tools and messages**: `infrastructure/llm/request.py::ChatRequest` gains
  `tools` and multi-turn messages; the provider adapters parse tool calls; the call goes through
  `bench/llm_call.py::llm_call`, so it is bound to a ledger and billed like any send.
- **It acts as the user**: every tool call dispatches under the session's `IdentityContext`, no new credential.
- **A write fires on the user's click**: a write tool call renders as ONE proposal shape whose click fires
  the command, replacing `origin_readiness.py::RaisedCommand` and `domain/activity.py::ActivityDecision`.
  Reads run unasked.
- **Every tool call is the generic `step`** (icon, label, status, duration, cost), riding the SSE ledger tail.
- **The composer belongs to the account**, open on every screen; a dropped file calls the ingest tool, and
  opening a view is a tool too. The pipeline hero mounts over a run only; over a check-in it draws the
  draft's pipeline. The empty thread is a generic chat: wordmark, composer, starter chips.

## Deleted when it lands

- **The `checkin` node as the conversation surface, and `CheckinOutput`.** The resolver's findings become tool calls (edit the draft, confirm a column). `RaisedCommand` goes.
- **The composer's keyword parser** (`webapp/components/shell/families.tsx::familiesNamed`).
- **The "run the resolver now" rule, spelled three times**: webapp `useIngestFlow`, CLI `new.py::_hold_checkin`, `DraftCampaign.resolution_pending`. The host reads the served gaps.
- **The client-only thread book's per-subject keys** (`webapp/lib/chat/thread.ts`), once threads persist.

## Build order

1. `ChatRequest` gains `tools` and multi-turn messages; each provider adapter parses tool calls.
2. The registry over `CommandKind` and `ReadKind`, served in-process and over MCP stdio.
3. The host endpoint, declared before its handler; a turn's steps are records on the account's workspace ledger.
4. The webapp: the composer on every screen, steps, the one proposal shape.
5. The check-in as the "reach the origin gate" prompt, and the delete list above.
6. MCP over streamable HTTP, on R1.

**Closes when** a new account drops a CSV whose headers are its own and the webapp's host takes it through
the origin gate and starts the campaign; the same steps from Claude Code over MCP stdio reach the same draft.

## Open (operator)

- **Where account threads persist** — what the account authored is a SURVIVOR (§ 0011).
- **Which writes, if any, run without a click** — default none.
- **The host loop: in-house (recommended; small, rides our provider plumbing) or an adopted agent SDK.**
- **The host's model** — the `checkin` node's pin or a model the account picks.
- **The anonymous preview (roadmap A3) rides this**: it converses and reads; every write is a sign-in prompt.
