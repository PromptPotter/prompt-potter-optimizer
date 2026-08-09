# Chat foundation — the first-class front door

> **Living contract.** The canonical design for the chat tab as PromptPotter's front door +
> agent-activity stream. The forward part is the *imprint* — a generic agent-activity taxonomy
> (§1) that future tool-use populates without reshaping. Roadmap **C1** ("Chat write-path")
> and the roadmap's *Ingest + chat-first web* note carry the milestone framing.
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

The `chat` tab (`webapp/components/chat/ChatPane.tsx`) renders one ordered thread: the
ingest/check-in segment, then a `LiveSegment` that projects a *curated* slice of the live
cycle event stream (the webapp's first SSE consumer) and raises inline decision buttons
that fire existing `/commands/{kind}` verbs. The origin gate lives in this thread (no separate
modal); the job-bar folds into the pipeline hero (one anchor). The still-open half is the
conversational endpoint (§4a) + the genuine assistant tool-use behind the "Soon" toggles (§7);
the §0 schema-first gate is untouched by it (no new command, no new event, no new endpoint).

## 1. The unified thread model (the imprint)

One thread is an ordered list of typed **items**. Extend — do not duplicate — the existing
durable message model: `ChatMsg` in `webapp/lib/hooks/useIngestFlow.ts`
(`user-file | user | ai | warning | error | run`, with the standing comment "the conversation renders from a
list of these"). The thread carries four item families:

| Family | Items | Source |
|---|---|---|
| **Message** | `user`, `user-file`, `ai`, `warning`, `error` | operator input + assistant replies (existing `ChatMsg`) |
| **Activity** | the **generic** agent vocabulary — `step` (running → done), `progress`, `warning`, `error`, `merge` — plus the **optimizer specialization** `candidate` / `round` | projected from the cycle event stream (§2) |
| **Decision** | a labelled button group with a pending/acted state | raised by the copilot; fires an existing command (§4) |
| **Record** | `run` — a finished run, frozen (`RunSummary`) | committed by the client on the live→stopped edge |

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

**The `run` record is the one deliberate crossing of that line, and the reason is that a
task which has ENDED is neither.** Its facts stop changing, and the thread has to keep
saying what it ended at while the surface above moves on to the next run. So the live view
— the **run card**: what the run cost and changed, over a three-row window on the scoring
walk, pinned to the thread tail while the cycle runs — is an ordinary projection like any other;
the moment the run stops, the client freezes a **snapshot of served values** into the
message list as a `run` item. It holds values rather than a pointer *because* a `resume`
re-animates `dashboard.json`, and `resume --from N` can rewrite the round files beneath it
— anything holding a pointer would silently restate itself as the next run. One item per
cycle, and the guard lives with the list it protects.

The snapshot is in-memory for the session and does **not** survive a reload until thread
persistence lands (§5). A reload of a stopped campaign shows the run card in its final
state and no frozen item — correct, since there is no *earlier* run to log.

## 1a. Liveness — the Focus chain (the *now*, complementing the stream)

The activity stream (§1) is the **history**: an append-only log of what the run *did*. A
running task also needs the orthogonal answer — *what is it doing right **now***? That is the
**Focus chain**: at any instant the run has a path from coarse to fine, and a good "something
is running" surface shows every level at once. The names are deliberately generic so the
template carries to any project, no pre-knowledge required:

| Level | Answers | PromptPotter meaning | Backing field |
|---|---|---|---|
| **Live** | Is it alive? | the heartbeat / pulse | `run_phase` + poll freshness (`isLive`) |
| **Stage** | What part of the work? | Check-in / Generating / Scoring / Refining / Replanning | `dashboard.json::state` |
| **Step** | Which operation right now? | the active optimizer node | `dashboard.json::current_round.active_node` |
| **Item** | Which unit right now? | the candidate being scored (`C3.2`) | `dashboard.json::candidate` |
| **Progress** | How far through the item? | samples `24/40`, rate | `dashboard.json::query` |

**One canonical "active" signal, and the SERVER resolves it.** The Step level is a declared
field — `current_round.active_node` (`live_dashboard/view.py::_active_node`) — that every surface
reads verbatim (the optimizer-canvas pulse, the RemoteBar, the TopStrip, the node detail). The
**in-flight LLM call's node** wins when there is one (`in_flight.node` names it directly for
`l1_generate` / `l1_critique` / `l2_context` / `l3_plan` / `checkin`); otherwise the phase does,
through a map that is TOTAL over `DashboardState` and raises at import if a member is missing.

The rule: **derive "what's active" once, at the writer, and never re-infer it per surface.**
Totality is the fix, not the relocation — the client version covered three states and every
other one resolved to "nothing running", which a partial map on the server would reproduce
exactly.

**Item / Step are the optimizer specialization of the generic levels.** Exactly as §1 frames
`candidate` / `round` as the optimizer-specific layer on the generic activity vocabulary, the
Focus chain's `Step` (= optimizer node) and `Item` (= candidate `C{round}.{idx}`) are what a
reusing team re-points at their own units of work; `Live` / `Stage` / `Progress` are generic
and kept. The accessibility invariant (§2) holds here too — the active Step pairs the green
pulse with a **text label** (the canvas toolbar's `live · {node}` in an `aria-live` region), so
"which node is live" reads without relying on color alone.

## 2. Activity rendering — the `ProjectionEnvelope → ActivityItem` translator

The backend activity highway **already exists** and needs no change for v1. Every frame on
the cycle SSE stream is a `ProjectionEnvelope` (`promptpotter/domain/projection_envelope.py`),
whose closed `kind` is one of 11 ledger `record_type`s + `stream_snapshot`. The CLI's
`LiveDisplay` (`promptpotter/presentation/views/live/display.py`) is the **proven, canonical
rendering** of exactly the Perplexity-style taxonomy we want — to a terminal. The chat
activity feed is a **browser port of `LiveDisplay`'s handler dispatch**: a client-side
translator from envelope `kind` → `ActivityItem`. Keep it 1:1 with `LiveDisplay` so the two
surfaces never drift.

The kind-by-kind mapping lives in the translator itself (`webapp/lib/chat/activity.ts`,
1:1 with `LiveDisplay`'s handlers — read the mapping there, this doc doesn't mirror it).
The split that survives template extraction: the **generic** mappings (llm-call steps,
warnings, errors, the `command` merge item + rejected-only `command_ack`) are the reusable
core; the **optimizer-specific** ones (sample/candidate snapshots, round cards) are the ones
§6 deletes to de-PromptPotter.

**Tool-use lands as a `step`, not a new item kind.** When backend web-search / MCP / code-exec
ship (§7), each new `ProjectionKind` maps to a generic **step** via this same translator — the
imprint already has the slot. Only a *richer per-tool field set* (beyond what `llm_call` /
`snapshot` carry) needs a new asyncapi `ProjectionKind` declared first (§0 gate).

State pairs an icon **and** a label (HIT/MISS, running/done) — never color alone — per the
frontend accessibility invariant.

**The feed is curated, not the firehose** (`webapp/lib/chat/activity.ts`):
high-signal kinds become items; the per-sample `sample_scored` torrent collapses
into a single replaced **progress chip**; `pobb_backfill` /
detail-less `llm_call_progress` heartbeats / `token_usage` map to `null` — with the one
special case that the **L4 inner-campaign** heartbeat *does* carry `detail`
("inner rX/Y · best Z%") and upserts one stable-id **progress** chip, so the outer chat
never reads as silent; **`command`** surfaces as the small "control applied" **merge** item
(the visible resolution of the parallel chat ↔ trace streams), and its **`command_ack`** is
a non-item *unless rejected* — acking an applied command would print the same fact twice.
`decision` / `stream_snapshot` are non-items.
Every `ProjectionKind` maps to an item or an explicit `null` — no orphan.

**A non-item is not the same as discarded.** `sample_order_preview` yields no item
(nothing *happened* — it is the order the scorer is about to walk), but the stream is
its **only** channel: `LiveDashboardView` never persists it, so `dashboard.json` carries
the sample being scored *now* and, on closed rounds, the order after the fact
(`RoundSummary.selection`). The forward view exists nowhere else. It is therefore read
as STATE beside the feed (`sampleOrderFrom` → `useCycleEvents().sampleOrder`) and drives
the run card's "next in line". A *declared* order, never a promise — PoBB can stop a
candidate before its tail is reached, so no surface may word it as "will".

## 3. Transport — the first SSE consumer, over a cross-process ledger tail

The chat is the webapp's **first SSE consumer** (all other liveness is the 2s `dashboard.json`
poll, `webapp/lib/poll.tsx`). Critically, the stream is **cross-process**: the endpoint tails
the cycle's on-disk ledger (`.runtime/ledger.jsonl`) rather than an in-memory fan-out, so the
chat sees a campaign no matter which process runs it (the API server, the CLI, a spawned
runner). Codepath: `event_stream.py::CycleLedgerTail` → `events.py::stream_cycle_events`.

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
  owned by `useWorkspace()` (`webapp/lib/workspace.tsx`) — it adds no second identity call.

## 4. The conversational endpoint + button-gated agency

**Two halves are net-new; flag them precisely against the §0 gate.**

### 4a. Conversational endpoint — **new, openapi-first (deferred to Arc 2)**
The user ↔ assistant round-trip does not exist (chat input is disabled outside ingest). It
adds a chat endpoint that wraps an LLM and answers from campaign context — a new optimizer
node mirroring the `checkin` node, so its **provider/model lives per-node in
`promptpotter/assets/optimizer/pipeline.yaml`** (resolved inside `run_optimizer_node`/`llm_call`), NOT
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
`pause-cycle`, `start-run`, `change-spend-budget`. **No command is added.**

> **"Declared" ≠ "wired".** The openapi declares commands ahead of their handlers
> by charter; only the kinds in `commands.py::_WIRED_KINDS` (plus the four typed
> routes) actually resolve — anything else 404s `command_kind_unknown`. Check
> that set before promising a button. An earlier draft of this spec named
> `endorse-candidate` here as if it were live; it has no handler. Unwired
> operations carry `x-status: declared-not-wired` in the yaml. The first wirings are the surfaces that already pause the loop for an
operator call: the **origin gate** and the **round-1 halt-and-decide** verdict.

### 4c. Five-I/O mapping
- Assistant reply + raised buttons + activity items = **Display** (outbound projection / read).
- A button click = **Control-remote** inbound command (existing closed set → `CommandRecord`
  → inline apply → `CommandAckRecord`).
- The new conversation endpoint = a sanctioned non-command read/conversation surface
  (openapi-declared), peer to `POST /datasets/ingest`. **No new I/O kind.**

## 5. Persistence — extend the check-in thread, campaign-scoped

The thread is **not a new concept beside the campaign** — it *is* the check-in, continued.
The origin-resolution check-in conversation that starts a campaign (the ingest Q&A:
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
  `verify/`, `tree/`), the optimizer-specific activity mappings (round-summary, PoBB,
  candidate scoreboard), and the **run card + its `run` record**
  (`components/chat/RunCard.tsx`, `lib/derivations/{run-summary,flipped-samples,sample-walk}.ts`)
  — leaving a generic chat + tool-activity app.
  The *pattern* the card is an instance of — one always-current pane pinned to the
  thread tail while a task runs, frozen into the log when it ends — is worth keeping;
  its contents are not.

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

## 8. Build order

**Arc 1 — curated activity + loop control: SHIPPED** (translator, SSE client, one thread,
decision buttons on existing commands only).

**Arc 2 — conversation (deferred, YAML-first):**
1. **Openapi-first**: declare the conversation endpoint in `m12-api-openapi.yaml`.
2. **Conversation endpoint** — served by the `checkin` optimizer node (the check-in copilot IS
   the conversation surface; no separate `chat` node); no new tools.
3. **Persistence** extending the check-in thread, campaign-scoped (Arc 1 persists nothing new —
   activity re-derives from the stream, decisions ride the command ledger).
