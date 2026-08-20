# Chat foundation — the first-class front door

> **Living contract.** Arc 1 (curated activity + loop control) **shipped** — read it off
> `webapp/components/chat/` + `webapp/lib/chat/activity.ts`, not off this file. What stays here is the
> positioning, the decisions that would be re-broken by a plausible edit, and **Arc 2, which is open**.
>
> Read [`../architecture.md`](../architecture.md) §0 (five I/O kinds) first.

## 0. What this is — and the positioning

The first tab **is a chat**, and it is — deliberately and cleanly — an LLM wrapper. We own that rather than
dress it up: the chat is the operator's front door to the Potter, and a **canonical agent-chat template**
that happens to drive an optimizer. It is a **human-in-the-loop operator copilot**. Three things converge in
**one ordered thread**:

1. **Conversation** — the operator talks to an assistant that answers from campaign context.
2. **Activity** — the Potter posts what it's doing into the same thread, Perplexity-style: tool calls, web
   searches, backend matches, each round as it lands. Exactly what the CLI already streams to the terminal.
3. **Decisions** — when a choice is warranted (the round-1 halt-and-decide gate, the origin gate, a budget
   call), the copilot raises **inline buttons**. The copilot proposes; the operator's click acts.

**The copilot cannot mutate state directly.** A button click *is* the action, and each button is a thin
trigger for a control-plane command that already exists.

## 1. The decisions Arc 1 stands on

Four rules, each one a thing a plausible edit would undo:

- **The imprint is the generic `step`.** Everything the Potter *does* — an optimizer LLM call, a web search,
  a code execution, an MCP call, a backend match — is one `step`: icon + label + status + optional
  duration/cost. When backend tool-use lands (§4), each emits a new `ProjectionKind` that maps into that same
  family — **no new item kind, no translator reshape**. `candidate` and `round` are the optimizer
  specialization layered on top, and they are what a reusing team deletes (§3).
- **Activity and decision items are rendered, never authored by the client.** Message items are the only ones
  the operator and assistant write. That line is what keeps the thread honest — with **one deliberate
  crossing**: a run that has ENDED is neither, so the client freezes a snapshot of *served values* into the
  list as a `run` item. It holds values rather than a pointer because `resume` re-animates `dashboard.json`
  and `resume --from N` rewrites the round files beneath it — anything holding a pointer would silently
  restate itself as the next run.
- **Derive "what's active" once, at the writer, and never re-infer it per surface.** The Step level is one
  declared field, `current_round.active_node` (`live_dashboard/view.py::_active_node`), that every surface
  reads verbatim; the in-flight LLM call's node wins, else the phase does, through a map that is TOTAL over
  `DashboardState` and raises at import if a member is missing. **Totality is the fix, not the relocation** —
  the client version covered three states and everything else resolved to "nothing running".
- **The feed is curated, not the firehose, and every `ProjectionKind` maps to an item or an explicit
  `null`** — no orphan. The `sample_scored` torrent collapses into one replaced progress chip; an in-flight
  heartbeat carrying `detail` upserts one stable-id chip so the thread never reads as silent — the L4
  inner-campaign tick names its round, an optimizer tick names the PROVIDER it is waiting on, which is the
  one fact that separates a slow model from a dead loop (`elapsed_s` rides the same record, so the chip's
  clock is formatted client-side and no duration formatter is duplicated into the engine); `command` surfaces as a "control applied" merge item and its `command_ack` is a non-item *unless
  rejected*, because acking an applied command prints the same fact twice.

**A non-item is not the same as discarded.** `sample_order_preview` yields no item — nothing *happened*, it
is the order the scorer is about to walk — but the stream is its **only** channel: `LiveDashboardView` never
persists it. It is read as STATE beside the feed and drives the run card's "next in line". A *declared*
order, never a promise: PoBB can stop a candidate before its tail is reached, so no surface may word it as
"will".

**Transport** is the webapp's first SSE consumer, and it tails the cycle's on-disk ledger rather than an
in-memory fan-out — which is why the chat sees a campaign no matter which process runs it (API server, CLI,
spawned runner). The paint is two-pass: a leading `stream_snapshot` frame paints state-so-far, the tail
begins at `snapshot_at_offset` and upserts by stable id. The ledger is never re-scanned from the top.
Contract: [`m12-events-asyncapi.yaml`](m12-events-asyncapi.yaml).

## 2. Arc 2 — the conversational endpoint, open

The user ↔ assistant round-trip does not exist (chat input is disabled outside ingest).

**It is served by the `checkin` optimizer node** — the check-in copilot IS the conversation surface; there is
no separate `chat` node — so its provider/model lives per-node in `promptpotter/assets/optimizer/pipeline.yaml`,
NOT in `campaign.json`, which carries no `optimizer_llm.provider`. The assistant uses **no new tools**; it
answers from context and the live stream.

**It is a new HTTP surface, so it is declared in [`m12-api-openapi.yaml`](m12-api-openapi.yaml) first, in its
own PR, before the handler lands** (the §0 schema-first gate). It is **not** a `/commands/{kind}` verb — it
does not mutate cycle state. The reply is delivered on the endpoint's own response, independent of the
cycle-activity SSE, touching no asyncapi event kind.

**Buttons add no command.** Each is a trigger for a kind already in the closed set. **"Declared" ≠ "wired"**:
the openapi declares commands ahead of their handlers by charter, and only `commands.py::_WIRED_KINDS` plus
the four typed routes resolve — anything else 404s `command_kind_unknown`. Check that set before promising a
button; an earlier draft of this spec advertised `endorse-candidate` as live on the strength of the yaml
alone, and it has no handler.

**Persistence — extend the check-in thread, campaign-scoped.** The thread is not a new concept beside the
campaign; it *is* the check-in, continued. One durable thread per campaign, stored where the check-in
artifacts already live, human-readable on disk per the folder-UI contract. **No new store** — extend the
existing message model and reuse the ingest draft-sync channel. Check-in answers that shape the origin are
loop-relevant; most chat is not, and the optimizer reads only what it needs. The thread never gates the loop.

**Five-I/O mapping:** assistant reply + raised buttons + activity items = **Display**; a button click =
**Control-remote** inbound; the conversation endpoint = a sanctioned non-command read/conversation surface,
peer to `POST /datasets/ingest`. **No new I/O kind.**

**Build order:** (1) declare the endpoint in the openapi; (2) the endpoint on the `checkin` node; (3)
persistence extending the check-in thread. Arc 1 persists nothing new — activity re-derives from the stream,
decisions ride the command ledger.

## 3. Template seam — keep it simple

**Cleaner appearance wins over premature abstraction** — do not extract a standalone chat-core package now.
Clean internal structure plus a documented delete-list:

- **Keep (the reusable core):** the chat shell + thread model + the `ProjectionEnvelope → ActivityItem`
  translator + the SSE client + the conversation endpoint.
- **Delete to de-PromptPotter:** the optimizer panes (`components/dashboard/`, `verify/`, `tree/`), the
  optimizer-specific activity mappings (round summary, PoBB, candidate scoreboard), and the **run card + its
  `run` record**. The *pattern* the card instantiates — one always-current pane pinned to the thread tail
  while a task runs, frozen into the log when it ends — is worth keeping; its contents are not.

The core can be lifted into its own module later; the seam is reversible by design. Don't pay the extraction
cost until a second consumer earns it.

## 4. Deferred, each needing the YAML edited first

Genuine assistant tool-use — the "Soon" toggles are Extended thinking · Web search · Code execution, with MCP
a planned fourth. Each renders as a generic **step**; only a *richer per-tool field set* needs a new
`ProjectionKind` declared in `m12-events-asyncapi.yaml` first. Backend (TermNorm) tool activity surfaces today
only at the granularity the existing `snapshot` / `token_usage` records carry.
