# webapp/lib — CLAUDE.md

## Where a module lives — by what it IS

**`api/`** the wire · **`state/`** shared state: one context provider or module store per file, kebab-case, the context `<Subject>Context` · **`prefs/`** one hook over one `STORAGE_KEYS` entry · **`derivations/`** a pure reading of served data · **`hooks/`** a read, command or browser hook · **`types/`** a TS-only type · the root is infrastructure every tier imports. A context one surface alone consumes lives in that surface.

## Display-data sources

**One source per data class.**

| Surface | Reached by | Sole source for |
|---|---|---|
| `dashboard.json` | `lib/poll.tsx` → `useCycleStream()` | in-flight `current_round` + `rounds[]`: FitnessChart and the SPEND breakdown |
| `/hierarchy` | `lib/state/registry.tsx` → `useRegistry()` / `useEntry(address)` | every surface that lists, switches, docks or heads a run; tags via `lib/derivations/hierarchy.ts` |
| `/hierarchy/{address}` | `lib/lineage.tsx` | the genealogy at any depth, indexed once by `lib/derivations/lineage-candidates.ts::indexLineage` |
| `/campaigns/{id}/cycles/{id}/rounds/{n}` | `lib/hooks/useRound.ts` | the round (`RoundView`); `round_NNNN.json` is shown only as raw text in Files |
| `…/rounds/{n}/audit` (AUDIT TWIN) | `lib/hooks/useRoundNodes.ts` | per-node LLM I/O; selects on `round === current_round.round` here alone |
| `/datasets/{name}/cells` | `lib/hooks/useCells.ts` via `lib/hard-samples.tsx` / `usePresetCells` | every list of measured cells; one answer opens off `/cells/{answer}` |
| `/campaigns/{id}/pipeline?at=` | `lib/hooks/useConnector.ts` → `ConnectorProvider` | every config row, model chip, lock glyph, provenance badge; a check-in's `draft` and `CampaignFacts.runs_with` are other transports of the same answer (`I9`) |

- **No stitch**: never merge in-flight with historical or fall back to a round file; where two are one series the SERVER merges. Take a WHOLE candidate row from one half.
- **A prop names WHICH campaign/round, never WHERE to read**; provenance is the served `source`, never a client diff.
- **One 95% band per channel**; a bar-chart channel is declared once in `components/candidates/series.ts`.
- **Join arms on `reading.arm.key`; key entries on `address`, never `facts.individual`** (`splitRetired`). `reading.election.selected: false` alone says nothing — ask `reading.election.held`.
- **The sidebar reads the served `CampaignFacts.line`** and follows no successor pointer. <!-- probe: git grep -cE "superseded_by" -- webapp/lib/state/registry.tsx == 0 -->

## A wire shape is GENERATED — never hand-declared

**Every response type comes from `lib/api/types.generated.ts`** (gate `ts-types`); re-export from `lib/api/types.ts`; a narrow alias is derived (`ResponseType["field"]`). **A closed set belongs on the server**, and so do its words: a served enum is worded by its exported table (`scripts/build_ts_types.py::_WORD_TABLES`, e.g. `STATUS_MARK_INFO`, `STOP_REASON_INFO`), never a local `Record<…, string>`; a composed line is a served `Said` row.
<!-- probe: git grep -cE "^const [A-Z_]+_(LABELS|TONE|ADVICE|NAME): Record" -- webapp/*.ts <= 2 -->

## Viewed identity — one address (CyclePath)

**"What am I looking at?" is `viewedPath`, a `CyclePath` (`lib/ids.ts`), owned by `lib/workspace.tsx`; no surface keeps a second.** The one subject beside it is an unstarted `checkin` (`#/d/<draft id>`, `#/new`), under which `viewedPath` is null; `lib/chat/thread.ts` keys one thread per subject. **`workspace.tsx::navigate` moves the subject, `open` / `close` the views**; a surface reads `useWorkspace()` itself, never a callback handed down. Display re-roots to the LEAF hop (`pathLeaf`, `useLeafIsL4`); ingest and Files stay on the ROOT. A command is addressed by the whole path (`lib/api/commands.ts::cycleAddress`); inner-run refusal is the server's (`CyclePayload.inner_refusal`).

**The deep link is the whole address in the HASH** (`lib/address.ts` syntax; `lib/workspace.tsx` sole reader/writer) — a static export has no SPA fallback. `ids.ts::encodeCyclePath` is also the server's `?descend=` format: byte-exact.

## Polling shape

**One clock** (`useRead` + `intervalMs` on `lib/read-cache.ts`'s beat; every interval a named export of `lib/cadence.ts`) **and one invalidation** (`invalidateReads(name?)`). <!-- probe: git grep -cE "setInterval\(" -- webapp/lib/*.ts <= 1 --> One tree per campaign, fetched once; the step window (`/hierarchy/{course}/@`) is the chronology, not a second tree. Steps and SSE frames arrive READ (`promptpotter/domain/activity.py::ActivityFeed`). **One live channel (`useCycleEvents`), one history channel** — no SSE join. <!-- probe: git grep -cE "new EventSource" -- webapp/*.ts <= 1 -->

## Failure handling — classify, don't bucket

**Every read rides `lib/hooks/useRead.ts`, NAMED by one `{ id, load }` descriptor in `lib/api/reads.ts`**; same `id` shares one flight. No hand-rolled `useEffect` fetch. <!-- probe: git grep -cE "fetch\(" -- webapp/components/*.tsx == 0 -->

**Every write rides `lib/hooks/useCommand.ts`** (`slot.run`). The sentence is `operatorMessage(e, kind)` (`lib/api/errors.ts`; a write's is `commandMessage`), rendered by `ui/states::FailureLine`; a hook carries the `ReadFailure` / `CommandFailure`, never a string.

**A bare `catch` is the bug.** `failureKind(err)` (`lib/api/client.ts`) classifies into `transient | auth | gone | denied | invalid | refused`; branch on that, never a status literal. **`gone` (404) is terminal and stops the poll** — only the dashboard poll reports it, after `GONE_CONFIRM_LIMIT` misses, to `workspace.tsx::reportAddressGone`. **Failures are reported with ids only** (`lib/diagnostics.ts`, the API's `error_id`) — never measurements or prompt text.

## Brand identity

`lib/brand.ts` is the single brand source (`NEXT_PUBLIC_*`-overridable). **Never render "verified" while `BRAND.verification` says `self-declared`.**
