# webapp/lib — CLAUDE.md

A module lives by what it IS: `api/` the wire, `state/` one provider or store per file, `prefs/` one hook per `STORAGE_KEYS` entry, `derivations/` a pure reading of served data, `hooks/`, `types/`. A context one surface alone consumes lives in that surface.

## Display-data sources

**One source per data class.**

| Surface | Reached by | Sole source for |
|---|---|---|
| `dashboard.json` | `lib/poll.tsx` → `useViewedDashboard()` | in-flight `current_round` + `rounds[]`: FitnessChart and the SPEND breakdown |
| `/hierarchy` | `lib/hooks/useRegistry.ts` | every surface that lists, switches, docks or heads a run |
| `/hierarchy/{address}` | `lib/lineage.tsx` | the genealogy at any depth |
| `/campaigns/{id}/cycles/{id}/rounds/{n}` | `lib/hooks/useRound.ts` | the round; `round_NNNN.json` only as raw text in Files |
| `…/rounds/{n}/audit` | `lib/hooks/useRoundNodes.ts` | per-node LLM I/O |
| `/datasets/{name}/cells` | `lib/hooks/useCells.ts::useCells` | every list of measured cells |
| `/campaigns/{id}/pipeline?at=` | `lib/hooks/useConnector.ts::useConnector` | every config row, model chip, lock glyph, provenance badge (I9) |

- **No stitch**: never merge in-flight with historical or fall back to a round file; where two are one series the SERVER merges.
- **A prop names WHICH campaign/round, never WHERE to read**; provenance is the served `source`, never a client diff.
- **Join arms on `reading.arm.key`; key entries on `address`, never `facts.individual`.** `reading.election.selected: false` alone says nothing: ask `reading.election.held`. The sidebar follows no successor pointer. <!-- probe: git grep -cE "superseded_by" -- webapp/lib/hooks/useRegistry.ts == 0 -->

## A wire shape is GENERATED — never hand-declared

**Every response type comes from `lib/api/types.generated.ts`**, imported through `lib/api/types.ts` alone. **A closed set and its words belong on the server** (`promptpotter/shared/worded.py::Worded`), never a local `Record<…, string>`; a composed line is a served `Said` row. <!-- probe: git grep -cE "^const [A-Z_]+_(LABELS|TONE|ADVICE|NAME): Record" -- webapp/*.ts <= 2 -->

## Viewed identity — one address (CyclePath)

**"What am I looking at?" is `viewed`, owned by `lib/workspace.tsx`; no surface keeps a second or re-derives a hop**, and a surface reads `useWorkspace()` itself, never a callback handed down. Display re-roots to the LEAF hop; ingest and Files stay on the ROOT. A command is addressed by the whole path; whether a kind acts on an inner run is the server's. **The deep link is the whole address in the HASH** (a static export has no SPA fallback); `ids.ts::encodeCyclePath` is also the server's `?descend=` format, byte-exact.

## Polling and failure

- **One clock** (every interval a named export of `lib/cadence.ts`) **and one invalidation** (`invalidateReads`); no provider mirrors the cache. <!-- probe: git grep -cE "setInterval\(" -- webapp/lib/*.ts <= 1 --> **One live channel, one history channel**, no SSE join. <!-- probe: git grep -cE "new EventSource" -- webapp/*.ts <= 1 -->
- **Every read rides `useRead`, named by one descriptor in `lib/api/reads.ts`**; no hand-rolled fetch. <!-- probe: git grep -cE "fetch\(" -- webapp/components/*.tsx == 0 --> **Every write rides `useCommand`**, its sentence `operatorMessage(e, kind)`.
- **A bare `catch` is the bug**: branch on `failureKind(err)` and `failurePasses(kind)`, never a status literal. **`gone` (404) is terminal and stops the poll** (I7). Failures are reported with ids only, never measurements or prompt text.
- **Never render "verified" while `BRAND.verification` says `self-declared`.**
