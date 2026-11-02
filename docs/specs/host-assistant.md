# Host assistant: the account's one conversation, and the surface every host drives

> **Only the open half lives here.** Why the boundary moved is [`../decisions.md`](../decisions.md) § 0012. The built thread's rules are [`../../webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § The chat thread — rendered, never authored. The reusable chat core and its delete-list are [`../../webapp/components/chat/README.md`](../../webapp/components/chat/README.md). The outbound transport is [`events-asyncapi.generated.json`](events-asyncapi.generated.json).

## The boundary

**The host is whoever the user talks to.** PromptPotter serves one surface. The webapp's assistant is one host of it. Claude Code driving `/potter-run`, HuggingChat, or any MCP client is another. The check-in is not a gate in front of the optimizer. It is the first job any host does.

**The origin gate stays.** `application/datasets/origin_readiness.py::origin_readiness` is the contract, deterministic and server-side. A host reaches it; it never talks past it.
- **Goal one: get the origin through the gate.** The served gaps are the checklist: the input and target columns, the task framing, and each LLM node's model. The host asks, proposes and confirms until `complete`.
- **Past the gate, be smart.** The host reads the account and analyses it: its datasets, campaigns, rounds, measured cells and spend. It answers in the account's own numbers and steers runs through the commands that already exist.
- **One host, one goal at a time, read off served readiness.** The client keeps no mode.

## The surface: MCP's three primitives

**One registry, every host, no adapter adds a tool.**

- **Tools.**
  - Writes are the `domain/command_kinds.py::CommandKind` rows, already capability-gated (§ 0005).
  - Reads get a catalogue declared the same way: each row has a name, a response model, a capability and the `application/` function that answers it. Reads have no catalogue today; they are scattered routers known only to OpenAPI.
  - The writes outside the dispatcher become kinds first (`POST /datasets/ingest`, the draft opens), so the tool list is the whole surface. Code-debt names them under "Two doors into account state".
- **Resources.** The account's data, addressed by the hierarchy address grammar ([`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § The hierarchy address): datasets, origins, campaigns, a cycle's readout, measured cells.
- **Prompts.** Templates.
  - **The check-in is one: "reach the origin gate."** It is seeded from the `checkin/2` prompt text in `assets/checkin/pipeline.yaml`.
  - Later templates (reading a round, explaining why a leader won) are more prompts, never more code paths.

**Two transports.** One is in-process, for the webapp's host. The other is MCP:
- stdio for a local host now;
- streamable HTTP on R1's credential ([`roadmap.md`](roadmap.md) § Agent-tool parity).

The MCP SDK is an extra (§ 0006). A long-running call returns a task handle, per MCP's Tasks extension ([`../research/external-constraints.md`](../research/external-constraints.md) § TOOL and interop — pick one, never invent a third).

## The webapp's host

- **A server-side loop of model, tools and messages.**
  - `infrastructure/llm/request.py::ChatRequest` gains `tools` and multi-turn messages. The provider adapters (`openai_compat.py`, `anthropic.py`) parse tool calls.
  - The call goes through `bench/llm_call.py::llm_call`, so it is bound to a ledger like every paid send.
- **It acts as the user** (§ 0005: the user's own assistant acts as the user). Every tool call dispatches under the session's `IdentityContext`, with no new credential and no R1.
- **It is billed like any send.** The account's workspace ledger records it, the account is charged at the same send, and the spend book admits it first (§ 0003).
- **A write fires on the user's click.**
  - A write tool call renders as ONE proposal shape whose click fires the command. That shape replaces `origin_readiness.py::RaisedCommand` and `domain/activity.py::ActivityDecision`.
  - Reads run unasked. The copilot never mutates state directly.
- **Every tool call is the generic `step`** (icon, label, status, duration and cost). It is no new item kind, and the steps ride the SSE ledger tail like every outbound frame (§ 0001).
- **The composer belongs to the account, not to a subject.** It is open on every screen. A dropped file calls the ingest tool, and opening a view is a tool too.
- **The pipeline hero (`ChatPane.tsx` `.wf-hero` → `PipelineStack`) mounts over a run only.** Over a check-in, its steps draw the draft's pipeline.
- **The empty thread is a generic chat.** It shows a wordmark, the composer, and a row of starter chips (HuggingChat's new-chat screen is the reference). The composer's tool rows are placeholders until each lands: extended thinking, web search, code execution.

## Deleted when it lands

- **The `checkin` node as the conversation surface, and `CheckinOutput`.** The resolver's findings become tool calls (edit the draft, confirm a column). `RaisedCommand` goes, and so does `ResolveOriginPayload.message`, which has no caller.
- **The composer's keyword parser** (`webapp/components/shell/families.tsx::familiesNamed`).
- **The "run the resolver now" rule, spelled three times**: webapp `useIngestFlow`, CLI `new.py::_hold_checkin`, `DraftCampaign.resolution_pending`. The host reads the served gaps.
- **The client-only thread book's per-subject keys** (`webapp/lib/chat/thread.ts`), once threads persist.

## Build order

1. `ChatRequest` gains `tools` and multi-turn messages; each provider adapter parses tool calls.
2. The read catalogue beside `CommandKind`, and the out-of-dispatcher writes as kinds.
3. The registry, served in-process and over MCP stdio.
4. The host endpoint, declared before its handler. A turn's steps are records on the account's workspace ledger, tailed over SSE.
5. The webapp: the composer on every screen, steps, the one proposal shape.
6. The check-in as the "reach the origin gate" prompt, and the delete list above.
7. MCP over streamable HTTP, on R1.

**Closes when** a new account drops a CSV whose headers are its own, and the webapp's host takes it through the origin gate and starts the campaign. The same steps driven from Claude Code over MCP stdio reach the same draft, through the same calls.

## Open (operator)

- **Where account threads persist.** What the account authored is a SURVIVOR (§ 0011); run data is wiped on a reshape.
- **Which writes, if any, run without a click.** The default is none.
- **The host loop: in-house or an adopted agent SDK.** In-house is recommended: the loop is small, it rides the provider plumbing we already own, and it stays provider-agnostic.
- **The host's model.** Either the `checkin` node's optimizer-locked pin or a model the account picks.
- **The anonymous preview (roadmap A3) rides this.** It converses and reads; every write is a sign-in prompt.
