# Chat foundation — the first-class front door

> **Living contract.** The canonical design for the chat tab as PromptPotter's front door +
> agent-activity stream. **Arc 1 (curated activity + loop control) ships today**; the forward
> part is the *imprint* — a generic agent-activity taxonomy (§1) that future tool-use
> populates without reshaping. Roadmap **C1** ("Chat write-path") and the roadmap's *Ingest +
> chat-first web* note defer their detail here. Status lines below are truth for what ships;
> full prose history is in `git log`.
>
> Read [`../architecture.md`](../architecture.md) §0 (five I/O kinds) first.

## 0. What this is — and the positioning

The first tab **is a chat**, and it is — deliberately and cleanly — an LLM wrapper. We own
that rather than dress it up: the chat is the operator's front door to the Potter, and it is a
**canonical agent-chat template** that happens to drive an optimizer. It is a
**human-in-the-loop operator copilot**. Three things converge in **one ordered thread**:

1. **Conversation** — the operator talks to an assistant that answers from campaign context.
2. **Activity** — the Potter posts what it's doing into the same thread, Perplexity-style:
   tool calls, web searches, backend matches, and each round as it lands ("optimizer call ·
   gpt-oss-120b · 1,240 tok · 3.1s", "scoring sample 14/40 · HIT", "round 3 complete"). This is
   exactly what the CLI already streams to the terminal.
3. **Decisions** — when a choice is warranted (the round-1 halt-and-decide gate, the origin
   gate, a budget call), the copilot raises **inline buttons**. The copilot proposes; the
   operator's click acts.

**The copilot cannot mutate state directly in v1.** A button click *is* the action, and each
button is a thin trigger for a control-plane command that already exists. The copilot's job
is to converse, explain, and surface the right button at the right moment.

This codebase is **chat-experience-first, and meant to be reused.** The chat core (thread
model + the generic activity translator + transport) is structured so another team can keep it
and delete the PromptPotter-specific panes (§6) — what's left is a generic agent chat.

**Status:** **Arc 1 shipped — curated activity + loop control (frontend-only).** The
`chat` tab (`webapp/components/chat/ChatPane.tsx`) now renders one ordered thread: the
ingest/check-in segment, then a `LiveSegment` that projects a *curated* slice of the live
cycle event stream (the webapp's first SSE consumer) and raises inline decision buttons
that fire existing `/commands/{kind}` verbs. The origin gate moved into this thread (the
global `OriginGateModal` was deleted), and the standalone job-bar strip folded into the
pipeline hero (one anchor). **Deferred — Arc 2:** the conversational endpoint (§4a) + the
genuine assistant tool-use behind the "Soon" toggles (§7). The §0 schema-first gate is
untouched by Arc 1 (no new command, no new event, no new endpoint).

## 1. The unified thread model (the imprint)

One thread is an ordered list of typed **items**. Extend — do not duplicate — the existing
durable message model: `ChatMsg` in `webapp/lib/hooks/useIngestFlow.ts`
(`user-file | user | ai | warning | error`, with the standing comment "the conversation renders from a
list of these"). The thread carries three item families:

| Family | Items | Source |
|---|---|---|
| **Message** | `user`, `user-file`, `ai`, `warning`, `error` | operator input + assistant replies (existing `ChatMsg`) |
| **Activity** | the **generic** agent vocabulary — `step` (running → done), `progress`, `warning`, `error`, `merge` — plus the **optimizer specialization** `candidate` / `round` | projected from the cycle event stream (§2) |
| **Decision** | a labelled button group with a pending/acted state | raised by the copilot; fires an existing command (§4) |

**The imprint is the generic `step`.** Everything the Potter *does* — an optimizer LLM call, a
web search, a code execution, an MCP call, a backend match — is one `step`: an icon + label +
status (running → done, today the `running`/`done` pair) + optional duration / cost, rendered
the same Perplexity way. Today the only populated `step`s are the optimizer's LLM calls and
sample scoring; when backend tool-use lands (TermNorm's web-search strategy axis, MCP,
code-exec — §7), **each emits a new `ProjectionKind` that maps into the same `step` family — no
new item kind, no translator reshape.** That is what "imprint" means here: the taxonomy is
fixed now and backends populate it over time. `candidate` (the scoreboard) and `round` (the
round summary) are the **optimizer-specific** items layered on top — exactly what a reusing
team deletes (§6).

Activity and decision items are **rendered, never authored by the client** — the client
projects them from the stream and from copilot turns. Message items are the only ones the
operator and assistant write. This is the structural line that keeps the thread honest.

## 2. Activity rendering — the `ProjectionEnvelope → ActivityItem` translator

The backend activity highway **already exists** and needs no change for v1. Every frame on
the cycle SSE stream is a `ProjectionEnvelope` (`promptpotter/domain/projection_envelope.py`),
whose closed `kind` is one of 11 ledger `record_type`s + `stream_snapshot`. The CLI's
`LiveDisplay` (`promptpotter/presentation/views/live/display.py`) is the **proven, canonical
rendering** of exactly the Perplexity-style taxonomy we want — to a terminal. The chat
activity feed is a **browser port of `LiveDisplay`'s handler dispatch**: a client-side
translator from envelope `kind` → `ActivityItem`. Keep it 1:1 with `LiveDisplay` so the two
surfaces never drift.

**Generic mappings (the reusable core — kept on template extraction):**

| `ProjectionEnvelope.kind` | `LiveDisplay` handler | Activity item |
|---|---|---|
| `llm_call_start` | `_handle_llm_call_start` (`↻ optimizer call: …`) | **step (running)** — "{node} · {model}" (oversize → warn) |
| `llm_call_progress` | `_handle_llm_call_progress` (`· still waiting`) | **progress** — "{node} still running · {N}s" |
| `llm_call` | `_handle_llm_call` (`✓ … · Ns · tok`) | **step (done)** — "{node} · {N}s · {tok} tok · $" (cached tag) |
| `round_warning` | `_handle_round_warning` (`⚠`/`✗`) | **warning** — inline alert |
| `token_usage` | (folded into spend) | feeds the running spend chip, not its own item |
| `error` | (runner failure) | **error** message item |
| `command_ack` | — | a **merge** "control applied" item (rejected → warning); §4 |
| `command`, `decision`, `stream_snapshot` | (subscribe-time / §4) | non-items: backfill (§3) + button state (§4), never free items |

**Optimizer-specific mappings (deleted to de-PromptPotter, §6):**

| `ProjectionEnvelope.kind` | `LiveDisplay` handler | Activity item |
|---|---|---|
| `snapshot` (`event=sample_scored`) | `_handle_snapshot` → `on_sample_scored` | **progress** chip — HIT/MISS, "{i}/{n}" |
| `snapshot` (`candidate_*`, `p_best_update`) | `on_candidate_*` / `on_p_best_update` | **candidate** — scoreboard / PoBB line |
| `phase` (`phase=round`, `event=display`) | `_handle_phase` → `on_round_complete` | **round** card — round N, leader, fitness, spend |

**Tool-use lands as a `step`, not a new item kind.** When backend web-search / MCP / code-exec
ship (§7), each new `ProjectionKind` maps to a generic **step** via this same translator — the
imprint already has the slot. Only a *richer per-tool field set* (beyond what `llm_call` /
`snapshot` carry) needs a new asyncapi `ProjectionKind` declared first (§0 gate).

State pairs an icon **and** a label (HIT/MISS, running/done) — never color alone — per the
frontend accessibility invariant.

**As shipped (Arc 1), the feed is curated, not the firehose** (`webapp/lib/chat/activity.ts`):
the high-signal kinds above become items; the per-sample `sample_scored` torrent collapses
into a single replaced **progress chip**; `sample_order_preview` / `pobb_backfill` /
`llm_call_progress` heartbeats / `token_usage` map to `null`; `command_ack` surfaces as a small
"control applied" **merge** item (the visible resolution of the parallel chat ↔ trace streams);
`command` / `decision` / `stream_snapshot` are non-items. Every `ProjectionKind` maps to an
item or an explicit `null` — no orphan.

## 3. Transport — the first SSE consumer, over a cross-process ledger tail

The chat is the webapp's **first SSE consumer** (all other liveness is the 2s `dashboard.json`
poll, `webapp/lib/poll.tsx`). Critically, the stream is **cross-process**: the endpoint tails
the cycle's on-disk ledger (`.runtime/ledger.jsonl`) rather than an in-memory fan-out, so the
chat sees a campaign no matter which process runs it (the API server, the CLI, a spawned
runner). This was the migration that made the chat work at all — an in-memory stream only
existed in the runner's process, so a webapp chat against a CLI-launched run was always blank.
Codepath: `event_stream/tail.py::CycleLedgerTail` → `events.py::stream_cycle_events`.

- **Stream:** subscribe to `GET /campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe`
  (contract in [`m12-events-asyncapi.yaml`](m12-events-asyncapi.yaml) + the certified
  [`event-stream.md`](../developer/event-stream.md)): a leading `stream_snapshot` frame →
  live tail on increasing `sequence` (the ledger line index) → 15s heartbeats. The route 404s
  **only** for an unknown cycle; a running, paused, or finished cycle all subscribe.
- **Two-pass paint (efficient, no firehose replay):** the `stream_snapshot` frame (the cycle's
  `dashboard.json`, read **as-is** — not extended) paints the state-so-far
  (`snapshotToActivity`); the tail then begins at `snapshot_at_offset` and each live record
  upserts by stable id (`projectionToActivity`). The ledger is never re-scanned from the top.
- **Session resolution:** the chat reuses the viewed `(campaign_id, cycle_id)` pair already
  owned by `useWorkspace()` (`webapp/lib/workspace.tsx`) — no new `live-state` call.

## 4. The conversational endpoint + button-gated agency

**Two halves are net-new; flag them precisely against the §0 gate.**

### 4a. Conversational endpoint — **new, openapi-first (deferred to Arc 2)**
The user ↔ assistant round-trip does not exist (chat input is disabled outside ingest). It
adds a chat endpoint that wraps an LLM and answers from campaign context — a new optimizer
node mirroring the `checkin` node, so its **provider/model lives per-node in
`datasets/_optimizer/pipeline.json`** (resolved inside `run_optimizer_node`/`llm_call`), NOT
in `campaign.json` (which carries no `optimizer_llm.provider`). The assistant uses **no new
tools** (it answers from context + the live stream); genuine web-search / MCP assistant
tools are deferred (§7).

This is a **new HTTP surface** and **must be declared in
[`m12-api-openapi.yaml`](m12-api-openapi.yaml) first**, in its own PR, before the handler
lands (the §0 schema-first gate; same discipline as `POST /datasets/ingest`). It is **not** a
`/commands/{kind}` verb — it does not mutate cycle state; it is a conversation surface
alongside the read API. The assistant reply is delivered on the endpoint's **own** response
(optionally a token stream) — this is independent of the cycle-activity SSE and touches no
asyncapi event kind.

### 4b. Button-gated agency — **no new commands**
When a decision is warranted, the copilot emits a **decision item** (a labelled button
group). Each button is a thin trigger for a command **already in the closed
`/commands/{kind}` set** (`promptpotter/presentation/api/routers/commands.py`;
[`m12-api-openapi.yaml`](m12-api-openapi.yaml)) — e.g. `origin-gate-decision`,
`pause-cycle`/`resume-cycle`, `start-run`, `change-spend-budget`, `endorse-candidate`. **No
command is added.** The first wirings are the surfaces that already pause the loop for an
operator call: the **origin gate** and the **round-1 halt-and-decide** verdict.

### 4c. Five-I/O mapping
- Assistant reply + raised buttons + activity items = **Display** (outbound projection / read).
- A button click = **Control-remote** inbound command (existing closed set → `CommandRecord`
  → inline apply → `CommandAckRecord`).
- The new conversation endpoint = a sanctioned non-command read/conversation surface
  (openapi-declared), peer to `POST /datasets/ingest`. **No new I/O kind.**

## 5. Persistence — extend the check-in thread, campaign-scoped

The thread is **not a new concept beside the campaign** — it *is* the check-in, continued.
The origin-resolution check-in conversation that bootstraps a campaign (the ingest Q&A:
`useIngestFlow.ts` + backend `application/datasets/origin_resolve.py`) is the **first
segment** of one campaign-scoped thread. That same thread then carries free-form messages,
activity items, and decision buttons.

- **One durable thread per campaign**, persisted with the campaign (where the origin /
  check-in artifacts already live, under `projects/{tenant}/datasets/{slug}/` +
  `campaign.json`). Exact filename is the implementer's call; keep it human-readable and
  on-disk per the folder-UI contract — an operator can open it.
- **Loop-relevant vs conversational coexist.** Check-in answers that shape the origin are
  loop-relevant; most chat is not. The optimizer reads only what it needs (the origin /
  command records) and **ignores the rest** — the thread does not gate the loop.
- **No new store.** Extend the existing message model; reuse the ingest draft-sync channel
  (the roadmap's planned `DraftUpdatedRecord`, declared in asyncapi before its handler) as
  the first-segment sync, rather than inventing a parallel one.

## 6. Template seam — keep it simple

Goal is a **chat-experience-first codebase for everyone**, but **cleaner appearance wins over
premature abstraction** — do **not** extract a standalone chat-core package now. Aim for
clean internal structure plus a documented **delete-list**:

- **Keep (the reusable core):** the chat shell + thread model (§1) + the
  `ProjectionEnvelope → ActivityItem` translator (§2) + the SSE client (§3) + the
  conversation endpoint (§4a).
- **Delete to de-PromptPotter:** the optimizer-specific panes (`webapp/components/dashboard/`,
  `verify/`, `tree/`) and the optimizer-specific activity mappings (round-summary, PoBB,
  candidate scoreboard) — leaving a generic chat + tool-activity app.

State in the spec that this core **can be lifted into its own module later**; the seam is
reversible by design. Don't pay the extraction cost until a second consumer earns it
(the ≥3-call-site / removes-a-concept bar).

## 7. v1 boundary + drift notes (honoring the gate)

**v1 touches no event contract.** Activity rendering rides existing `ProjectionKind`s;
buttons fire existing commands. The **only** new contract surface is the conversation
endpoint (§4a), declared in `m12-api-openapi.yaml` first.

**Deferred (each needs the YAML edited first):**
- Genuine assistant tool-use. The **"Soon" toggles in `ChatPane.tsx` are Extended thinking ·
  Web search · Code execution** (MCP is a planned fourth tool, not yet a toggle). Each tool,
  when wired, emits a new `ProjectionKind` that renders as a generic **step** (§1, §2) — the
  imprint already has the slot. A *richer per-tool field set* (beyond what the current records
  carry) needs that `ProjectionKind` declared in `m12-events-asyncapi.yaml` first.
- Backend (TermNorm) tool activity surfaces in v1 only at the granularity the existing
  `snapshot` / `token_usage` records carry; a dedicated "web search" step is future work.

**Drift this spec records (reconcile, don't silently fix):**
- The roadmap calls this work **C1**; `code-debt-cleanup.md`'s "intentional UI placeholders"
  table already points its chat rows at **C1** (the stale "M13+" tag was dropped). The
  `adr/0001` historical "M13 chat-first user web" naming stays as constitutional record.
- `mask-projection.md` requires the `/lineage?lens=` read endpoint declared in
  `m12-api-openapi.yaml`, but the openapi declares no read endpoints though mask M1 is marked
  shipped — a contract gap to resolve when the chat read-surface is declared.
- `Expected-Version` is optional in v0 of `m12-api-openapi.yaml` pending the client
  consuming SSE sequence numbers. Once the chat consumes the SSE tail (§3), it threads
  `sequence` into command `Expected-Version` — the condition to flip it back to required.

## 8. Build order + acceptance

**Arc 1 — curated activity + loop control (shipped, frontend-only):**
1. **Translator** (`ProjectionEnvelope → ActivityItem`, `lib/chat/activity.ts`), curated 1:1
   against `LiveDisplay` handlers — every `kind` maps to an item or an explicit `null`.
2. **SSE client** (`lib/chat/useCycleEvents.ts`), snapshot-then-tail, reconnect; first consumer.
3. **One thread** — `LiveSegment` appended into `IngestConversation`'s thread; welcome stub
   collapses, never renders over live activity. Chrome: job-bar folds into the hero (one anchor).
4. **Decision buttons** → existing commands, the **origin gate** first (`origin-gate-decision`),
   folded in from the deleted `OriginGateModal`. No new command. Pause/resume/stop stay on the
   cross-tab RemoteBar pill.

**Arc 2 — conversation (deferred, YAML-first):**
5. **Openapi-first**: declare the conversation endpoint in `m12-api-openapi.yaml`.
6. **Conversation endpoint** — a `chat` optimizer node mirroring `checkin` (provider per-node in
   `datasets/_optimizer/pipeline.json`); no new tools.
7. **Persistence** extending the check-in thread, campaign-scoped (Arc 1 persists nothing new —
   activity re-derives from the stream, decisions ride the command ledger).

**Acceptance (Arc 1):** every `ProjectionKind` maps to a rendered item or an explicit non-item;
buttons fire only existing commands; no new command / event / endpoint; the chat core has a
documented delete-list; copy passes VOICE (anti-jargon, "the Potter", "node"/"origin").
