# Chat foundation — the first-class front door

> **Forward-looking living contract.** This is the canonical design for the chat tab as
> PromptPotter's front door + activity stream. Roadmap **C1** ("Chat write-path") and the
> roadmap's *Ingest + chat-first web* note defer their detail here. Status lines below are
> truth for what ships; full prose history is in `git log`.
>
> Read [`../architecture.md`](../architecture.md) §0 (five I/O kinds) first.

## 0. What this is — and the positioning

The first tab **is a chat**, and it is — deliberately and cleanly — an LLM wrapper. We own
that rather than dress it up: the chat is the operator's front door to the Potter. It is a
**human-in-the-loop operator copilot**. Three things converge in **one ordered thread**:

1. **Conversation** — the operator talks to an assistant that answers from campaign context.
2. **Activity** — the Potter posts what it's doing into the same thread, Perplexity-style
   ("scoring sample 14/40 · HIT", "optimizer call · gpt-oss-120b · 1,240 tok · 3.1s",
   "round 3 complete"). This is exactly what the CLI already streams to the terminal.
3. **Decisions** — when a choice is warranted (the round-1 halt-and-decide gate, the origin
   gate, a budget call), the copilot raises **inline buttons**. The copilot proposes; the
   operator's click acts.

**The copilot cannot mutate state directly in v1.** A button click *is* the action, and each
button is a thin trigger for a control-plane command that already exists. The copilot's job
is to converse, explain, and surface the right button at the right moment.

This codebase is **chat-experience-first, and meant to be reused.** The chat core (thread
model + activity translator + transport) is structured so another team can keep it and delete
the PromptPotter-specific panes (§6).

**Status:** spec-only. The `chat` tab is the webapp's default landing tab today
(`webapp/components/shell/AppShell.tsx`, `useState<Tab>("chat")`), but `ChatPane`
(`webapp/components/chat/ChatPane.tsx`) is **inert** — a job-bar + pipeline hero + the
dataset-ingest wizard rendered as a thread, plus a hardcoded illustration and "Soon"
toggles. This spec replaces the inert shell with a real thread.

## 1. The unified thread model (the imprint)

One thread is an ordered list of typed **items**. Extend — do not duplicate — the existing
durable message model: `ChatMsg` in `webapp/lib/hooks/useIngestFlow.ts`
(`user-file | user | ai | error`, with the standing comment "the conversation renders from a
list of these"). The thread carries three item families:

| Family | Items | Source |
|---|---|---|
| **Message** | `user`, `user-file`, `ai`, `error` | operator input + assistant replies (existing `ChatMsg`) |
| **Activity** | running / done / progress / sample-result / round-summary / warning | projected from the cycle event stream (§2) |
| **Decision** | a labelled button group with a pending/acted state | raised by the copilot; fires an existing command (§4) |

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

| `ProjectionEnvelope.kind` | `LiveDisplay` handler | Activity item |
|---|---|---|
| `llm_call_start` | `_handle_llm_call_start` (`↻ optimizer call: …`) | **running** — "{node} · {model}" (oversize → warn) |
| `llm_call_progress` | `_handle_llm_call_progress` (`· still waiting`) | **progress** — "{node} still running · {N}s" |
| `llm_call` | `_handle_llm_call` (`✓ … · Ns · tok`) | **done** — "{node} · {N}s · {tok} tok · $" (cached tag) |
| `snapshot` (`event=sample_scored`) | `_handle_snapshot` → `on_sample_scored` | **sample** — HIT/MISS, "{i}/{n}" |
| `snapshot` (`candidate_*`, `p_best_update`) | `on_candidate_*` / `on_p_best_update` | **candidate** — scoreboard / PoBB line |
| `phase` (`phase=round`, `event=display`) | `_handle_phase` → `on_round_complete` | **round-summary** card — round N, leader, fitness, spend |
| `round_warning` | `_handle_round_warning` (`⚠`/`✗`) | **warning** — inline alert |
| `token_usage` | (folded into spend) | feeds the running spend chip, not its own item |
| `error` | (runner failure) | **error** message item |
| `decision`, `command`, `command_ack` | — | drive decision-item / button state (§4), not free items |
| `stream_snapshot` | (subscribe-time) | initial backfill of the thread's activity (§3) |

State pairs an icon **and** a label (HIT/MISS, running/done) — never color alone — per the
frontend accessibility invariant.

## 3. Transport — the first SSE consumer in the webapp

Today the webapp has **no SSE/EventSource client**; all liveness is the 2s `dashboard.json`
poll (`webapp/lib/poll.tsx`). The chat is the **first SSE consumer**, and the first step of
the planned *SSE client cutover* (roadmap *Plus-backlog*: "backend `events:subscribe`
shipped, client still 2s poll").

- **Stream:** subscribe to the shipped SSE channel
  `GET /campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe`
  (`promptpotter/presentation/api/routers/campaigns/events.py::stream_cycle_events`; contract
  in [`m12-events-asyncapi.yaml`](m12-events-asyncapi.yaml)). Honor its contract:
  leading `stream_snapshot` frame → live tail on strictly-increasing `sequence` → 15s
  heartbeats → on a sequence gap, re-subscribe.
- **Session resolution:** the chat resolves the active `(campaign_id, cycle_id)` via the
  shipped `GET /api/v1/sessions/active/live-state` (roadmap C1's stated dependency, P3) — the
  hard-ordering rule routes new chat state-queries through `live-state`, not `dashboard.json`.
- **No torn surfaces:** the activity backfill comes from the `stream_snapshot` frame (which
  mirrors `dashboard.json`), so the thread shows correct history on (re)subscribe.

## 4. The conversational endpoint + button-gated agency

**Two halves are net-new; flag them precisely against the §0 gate.**

### 4a. Conversational endpoint — **new, openapi-first**
The user ↔ assistant round-trip does not exist (chat input is disabled outside ingest). v1
adds a chat endpoint that wraps an LLM and answers from campaign context. It uses the
**per-campaign provider** already in `campaign.json` — no separate model surface. In v1 the
assistant uses **no new tools** (it answers from context + the live stream); genuine
web-search / MCP assistant tools are deferred (§7).

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
- Genuine assistant tool-use (web search / MCP / code-exec — the "Soon" toggles in
  `ChatPane.tsx`). A richer per-tool activity item (beyond what the current records carry)
  is a **new `ProjectionKind`** → `m12-events-asyncapi.yaml` first.
- Backend (TermNorm) tool activity surfaces in v1 only at the granularity the existing
  `snapshot` / `token_usage` records carry; a dedicated "web search" item is future work.

**Drift this spec records (reconcile, don't silently fix):**
- The roadmap calls this work **C1**; `code-debt-cleanup.md` labels the same inert controls
  **"M13+"**. Unified to **C1** pointing here.
- `mask-projection.md` requires the `/lineage?lens=` read endpoint declared in
  `m12-api-openapi.yaml`, but the openapi declares no read endpoints though mask M1 is marked
  shipped — a contract gap to resolve when the chat read-surface is declared.
- `Expected-Version` is optional in v0 of `m12-api-openapi.yaml` pending the client
  consuming SSE sequence numbers. Once the chat consumes the SSE tail (§3), it threads
  `sequence` into command `Expected-Version` — the condition to flip it back to required.

## 8. Build order + acceptance

1. **Openapi-first** (separate PR): declare the conversation endpoint in `m12-api-openapi.yaml`.
2. **Translator** (`ProjectionEnvelope → ActivityItem`), unit-mapped 1:1 against
   `LiveDisplay` handlers — no orphan `kind`.
3. **SSE client** in `webapp/`, snapshot-then-tail, gap→re-subscribe; first consumer.
4. **Thread model** extending `ChatMsg`; replace the inert `ChatPane`.
5. **Conversation endpoint** handler (per-campaign provider; no new tools).
6. **Decision buttons** → existing commands, starting with origin-gate + round-1 gate.
7. **Persistence** extending the check-in thread, campaign-scoped.

**Acceptance:** every `ProjectionKind` maps to a rendered item or an explicit non-item;
buttons fire only existing commands; the conversation endpoint is in the openapi before its
handler; the thread persists with the campaign and survives reload; the chat core has a
documented delete-list; copy passes VOICE (anti-jargon, "the Potter", "node"/"origin").
