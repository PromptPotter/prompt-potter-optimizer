# webapp/lib — CLAUDE.md

## Where a module lives — by what it IS

**`api/`** the wire · **`state/`** shared state: every context provider and module store, one per
file, kebab-case and named for its subject, the context `<Subject>Context` · **`prefs/`** a display
preference: one hook over one `STORAGE_KEYS` entry · **`derivations/`** a pure reading of served
data · **`hooks/`** a read, command or browser hook · **`types/`** a
TS-only type · the root is infrastructure every tier imports (format, ids, address, clock, brand,
chart theme, vocabulary). A context one surface alone consumes lives in that surface.

## Display-data sources

**One source per data class.**

| Surface | Reached by | Sole source for |
|---|---|---|
| `dashboard.json` | `lib/poll.tsx` → `useCycleStream()` | in-flight `current_round` + `rounds[]`: FitnessChart, TrendChart, the masthead's ROUNDS and SPEND (its STATE, BEST, BENCH are `lib/lineage.tsx::useViewedCourse`'s) |
| `/hierarchy` | `lib/state/registry.tsx` → `useRegistry()` / `useEntry(address)` | every surface that lists, switches, docks or heads a run; tags via `lib/derivations/hierarchy.ts::hasTag` / `tagValue` |
| `/hierarchy/{address}` | `lib/lineage.tsx` | the genealogy at any depth, indexed once by `lib/derivations/lineage-candidates.ts::indexLineage` |
| `/campaigns/{id}/cycles/{id}/rounds/{n}` | `lib/hooks/useRound.ts` | the round (`RoundView`); `round_NNNN.json` is shown only as raw text in Files |
| `…/rounds/{n}/audit` (AUDIT TWIN) | `lib/hooks/useRoundNodes.ts` | per-node LLM I/O |
| `/datasets/{name}/cells` | `lib/hooks/useCells.ts` via `lib/hard-samples.tsx` / `usePresetCells` | every list of measured cells; one answer opens off `/cells/{answer}` |
| `/campaigns/{id}/pipeline?at=` | `lib/hooks/useConnector.ts` → `ConnectorProvider` | every config row, model chip, lock glyph, reach ring, provenance badge; `StaticConnectorProvider` and `CampaignFacts.runs_with` are other transports of the same answer, never a second source (`I9`) |

- **No stitch**: never merge in-flight with historical or fall back to a round file; where two are one series the SERVER merges.
- **Take a WHOLE candidate row from one half**, chosen once, never per field.
- **A prop names WHICH campaign/round, never WHERE to read**; provenance is the served `source`, never a client diff.
- **One 95% band per channel, no scale field**; a bar-chart channel is declared once in `components/candidates/series.ts`.
- **Join arms on `reading.arm.key`; key entries on `address`, never `facts.individual`** — split with `splitRetired`.
- **`reading.election.selected: false` alone says nothing** — ask `reading.election.held`; `pickWinner` has no fallback.
- **The sidebar reads the served `CampaignFacts.line`** and follows no successor pointer. <!-- probe: git grep -cE "superseded_by" -- webapp/lib/state/registry.tsx == 0 -->
- **NAVIGATION** (`viewedPath` + `viewedCandidateId`) is written only by the sidebar; **INSPECTION** (`SelectionContext.candidate`) by a bar click, minted by `selectedCandidateOf`. A bar click never navigates.
- **The audit twin selects on `round === current_round.round`**, resolved in `useRoundNodes.ts` alone.

## A wire shape is GENERATED — never hand-declared

**Every response type comes from `lib/api/types.generated.ts`** (gate `ts-types`); re-export from `lib/api/types.ts`. A route with no `response_model` stays hand-written and says so; a narrow alias is derived (`ResponseType["field"]`), never re-declared.

**A closed set belongs on the server** — a union declared only in TypeScript is the bug.

## Viewed identity — one address (CyclePath)

**"What am I looking at?" is `viewedPath`, a `CyclePath` (`lib/ids.ts`), and no surface keeps a second.** `lib/workspace.tsx` owns it; `drillInto` / `backToOuter` move it. **An unstarted campaign is the one address beside it: `checkin` (`#/d/<draft id>`, `#/new` before one is minted), under which `viewedPath` is null** — `openCheckin` moves there, `navigate` back. **The chat is that subject's**: `lib/chat/thread.ts` keys one thread per subject, and the check-in flow (`lib/ingest-flow.tsx`) holds the viewed check-in's draft alone.

**Navigation policy has one entry, `workspace.tsx::navigate(path, opts)`**, and `openView` is the one view switch; a surface reads `useWorkspace()` itself, never a callback handed down.

**Display re-roots to the LEAF hop** (`pathLeaf`, `useLeafIsL4`); ingest and Files stay on the ROOT. **A command is addressed by the whole path** (`lib/api/commands.ts::cycleAddress`); inner-run refusal is the server's (`CyclePayload.inner_refusal`).

**The deep link is the whole address in the HASH** (`lib/address.ts` syntax, `lib/workspace.tsx` sole reader/writer) — a static export has no SPA fallback. Path, candidate and view are all required; the moment `at` is stamped with its path and read via `useMomentAt`. Malformed leaves the view alone. `ids.ts::encodeCyclePath` is also the server's `?descend=` format: byte-exact.

## Polling shape

**One clock** (`useRead` + `intervalMs` on `lib/read-cache.ts`'s beat; every interval is a named export of `lib/cadence.ts`) **and one invalidation** (`invalidateReads(name?)`). <!-- probe: git grep -cE "setInterval\(" -- webapp/lib/*.ts <= 1 -->

- **One tree per campaign, fetched once and addressed into** — never a second genealogy read.
- **The step window (`/hierarchy/{course}/@`) is the chronology**, not a second tree.
- **Steps and SSE frames arrive READ** (`promptpotter/domain/activity.py::ActivityFeed`); the browser translates no record and reads no `payload`.
- **One live channel (`useCycleEvents`), one history channel (the step window)** — no SSE join, no `since=`. <!-- probe: git grep -cE "new EventSource" -- webapp/*.ts <= 1 -->

## Failure handling — classify, don't bucket

**Every read rides `lib/hooks/useRead.ts`, NAMED by one `{ id, load }` descriptor in `lib/api/reads.ts`**; same `id` shares one flight, body and clock. No hand-rolled `useEffect` fetch. <!-- probe: git grep -cE "fetch\(" -- webapp/components/*.tsx == 0 -->

**Every write rides `lib/hooks/useCommand.ts`** (`slot.run`) — never throws, retries or reports gone. The sentence is `operatorMessage(e, kind)` (`lib/api/errors.ts`), rendered by `components/ui/states::FailureLine`; a hook carries the `ReadFailure` / `CommandFailure`, never a string, and a per-surface sentence table violates I2.

**A bare `catch` is the bug.** `failureKind(err)` (`lib/api/client.ts`) classifies into `transient | auth | gone | denied | invalid | refused`; branch on that, never a status literal. Every answered 4xx is a verdict; `transient` (5xx, network, parse) is the safe default.

**A pending claim and its fetch are guarded by one value**, and the mark is released in the effect's own cleanup.

**`survive` needs something to survive on** — render `invalid` as the operator's input only where `kept !== null`.

**`gone` (404) is terminal and stops the poll** — only the dashboard poll reports it, after `GONE_CONFIRM_LIMIT` misses, to `workspace.tsx::reportAddressGone`; list membership is not existence (I7). `useCycleEvents` subscribes only while the address is live.

**Failures are reported with ids only** (`lib/diagnostics.ts`, the API's `error_id`) — never measurements or prompt text.

## Brand identity / "About this unit"

`lib/brand.ts` is the single brand source (`NEXT_PUBLIC_*`-overridable). **Never render "verified" while `BRAND.verification` says `self-declared`.**
