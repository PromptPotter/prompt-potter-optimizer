# chat/ — the reusable chat core + the delete-list

PromptPotter's chat front door, built **chat-experience-first so another team can
keep the core and delete the optimizer-specific panes** (`docs/specs/chat-foundation.md`
§6). The seam is kept simple on purpose — clean internal structure + this
delete-list, not a prematurely-extracted package. It can be lifted into its own
module later; reversible by design.

This is **Arc 1: curated activity + loop control** — the chat renders a curated
layer over the live cycle event stream and surfaces in-thread decision buttons
that fire the existing `/commands/{kind}` verbs. The free-form "talk to an
assistant" endpoint is a deferred Arc 2 (see chat-foundation.md §4a).

## Keep — the reusable core

- **Chat shell + one ordered thread** — `components/chat/ChatPane.tsx` hosts the
  single thread in `components/ingest/IngestConversation.tsx`: the ingest /
  check-in segment, then the appended `LiveSegment` (curated activity feed +
  inline decision buttons), over the durable `ChatMsg` model
  (`lib/hooks/useIngestFlow.ts`).
- **The activity translator** — `lib/chat/activity.ts`
  (`ProjectionEnvelope → ActivityItem`, 1:1 with the CLI's `LiveDisplay`; curated,
  with the per-sample firehose mapped to `null` / a single progress chip).
- **The SSE client** — `lib/chat/useCycleEvents.ts` (snapshot → tail →
  heartbeat → reconnect), the webapp's first EventSource consumer.
- **The decision surface** — `lib/chat/decision.ts` + `components/chat/LiveSegment.tsx`
  (button-gated agency over the existing `/commands/{kind}` set; the origin gate
  was folded in here from the removed global modal).

## Delete to de-PromptPotter

To strip this down to a generic chat + tool-activity app, remove:

- **The optimizer panes:** `components/dashboard/`, `components/verify/`,
  `components/tree/` (Files), and the ingest setup flow
  (`components/ingest/`, `lib/hooks/useIngestFlow.ts`'s `IngestPhase` machine).
- **The optimizer-specific activity mappings** in `lib/chat/activity.ts` — the
  `snapshot` candidate / round / PoBB branches and the `phase` round-summary
  (`candidate`, `round` `ActivityKind`s). Keep the generic
  `running`/`done`/`progress`/`warning`/`error`/`merge` mappings; rewire them to
  your own tool's event records.
- **The optimizer-specific decision** in `lib/chat/decision.ts` (the origin-gate
  group); keep the `DecisionItem` shape + `LiveSegment`'s button rendering and
  point them at your own gated commands.
- The job-bar + pipeline hero + settings card inside `ChatPane.tsx` (the campaign
  telemetry chrome) — leave the `.chat-panel` thread + `LiveSegment`.

What remains is the chat shell, the one-thread model, the SSE transport +
translator seam, and the button-gated control surface — a generic copilot you
point at your own activity stream and commands.
