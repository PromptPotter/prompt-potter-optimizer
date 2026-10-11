# chat/ — the reusable chat core + the delete-list

PromptPotter's chat front door, built **chat-experience-first so another team can
keep the core and delete the optimizer-specific panes**. This file owns that
delete-list. The seam is kept simple on purpose — clean internal structure + this
delete-list, not a prematurely-extracted package. It can be lifted into its own
module later; reversible by design.

This is **Arc 1: curated activity + loop control** — the chat renders a curated
layer over the live cycle event stream and surfaces in-thread decision buttons
that fire the existing `/commands/{kind}` verbs. The free-form "talk to an
assistant" endpoint is a deferred Arc 2 —
`docs/specs/chat-foundation.md` § Arc 2 — the conversational endpoint, open.

## Keep — the reusable core

The core imports nothing from an optimizer module; each file's import list is the proof.

- **The thread model** — `lib/chat/thread.ts`: one ordered item list behind `ThreadProvider` /
  `useThread`. `message` and `file` are what the operator and the assistant write, `run` is the
  frozen values of a task that ended (generic in its payload), and a `block` is an
  always-current region a host mounts into the list and the store never keeps.
- **The renderer** — `components/chat/Thread.tsx` draws that list and follows its tail;
  `components/chat/Composer.tsx` is attach + one field + send. Neither knows what a `block` or
  a `run` holds: the host hands `Thread` a `renderRun` and `Composer` its `tools`.
- **The served current state** — `promptpotter/domain/activity.py::ActivityFeed`, on the
  server. The thread shows what the task is doing NOW, never its log: a record reads as at most
  one `ActivityItem`, every `ActivityKind` declares a lifetime in `LIFETIME`
  ([`event-stream.md`](../../../docs/developer/event-stream.md) § Current state), and every SSE frame
  carries the fold whole as `activity: {status, notices}`. A new kind is one row in that table,
  and any client — this one, an MCP tool, another agent's UI — renders the newest frame.
- **The SSE client** — `lib/chat/useCycleEvents.ts` (snapshot → tail →
  heartbeat → reconnect), transport only: it keeps the newest `activity` and reads nothing
  else off a frame.
- **The decision surface** — `components/chat/LiveSegment.tsx`: the live status line, its
  notices and the button-gated agency over the existing `/commands/{kind}` set, mounted as one
  `block` at the thread's tail.
- **The live-then-frozen shape** — an always-current `block` at the tail while the task runs,
  snapshotted into the list as a `run` item when it ends (`useThread().appendRun`, idempotent
  per key). The *shape* is reusable for any long task; what fills it here is not.

## Delete to de-PromptPotter

To strip this down to a generic chat + tool-activity app, remove:

- **The optimizer panes:** `components/dashboard/`, `components/files/`, and the ingest setup
  flow — `components/ingest/` whole, `lib/hooks/useIngestFlow.ts` and `lib/ingest-flow.tsx`.
  Ingest is a contributor: it appends messages and mounts two blocks
  (`IngestConversation.tsx::useIngestItems`), so removing it leaves the thread intact:
  `ThreadProvider` is mounted by the shell (`shell/AppShell.tsx`), above `IngestFlowProvider`.
- **The host** — `components/chat/ChatPane.tsx` is PromptPotter's composition: the pipeline
  hero, ingest's items, the live tail and the run card. Replace it with a host that hands
  `Thread` your own items.
- **The optimizer-specific readings** in `domain/activity.py` — the `candidate_scored` /
  `sample_scored` cases, the `phase` round headline and the held-out pass (`candidate`, `round`
  `ActivityKind`s). Keep the generic `running`/`done`/`progress`/`warning`/`error`/`merge`
  readings; rewire them to your own tool's event records.
- **The optimizer-specific decision** — the served `ActivityDecision` (the origin-gate group) and the
  origin-gate verb `LiveSegment` fires; keep `LiveSegment`'s button rendering and point it at
  your own gated commands.
- **The run card** — `components/chat/RunCard.tsx` (the Round row's
  `<TrendChart density="glyph" />` and the dashboard link live there) plus the derivation it
  reads, `lib/derivations/run-summary.ts`. Keep the `run` item kind and hand
  `Thread` a `renderRun` for your own task summary.
- **The optimize row** of `ingest/ComposerTools.tsx` and the `useRunControl` behind it — move
  the Tools popover and its coming-soon rows, which are the generic composer's, beside
  `Composer.tsx`, and re-point that one row at your own long-running task's pause/start.

What remains is the thread model and its renderer, the composer, the SSE transport and its
served state, and the button-gated control surface — a generic copilot you
point at your own activity stream and commands.
