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

**Every response type comes from `lib/api/types.generated.ts`**, imported through `lib/api/types.ts` alone. **A closed set and its words belong on the server** (`promptpotter/shared/worded.py::Worded`), never a local `Record<…, string>`; a composed line is a served `Said` row. <!-- probe: git grep -cE "^const [A-Z_]+_(LABELS|TONE|ADVICE|NAME): Record" -- webapp/*.ts <= 1 --> **A read's URL fills its served route** (`READ_ROUTES`, generated from each `ReadParams.route`) through `api/reads.ts::readUrl`, which types its slots; no path is spelled beside it but `/health`'s, the public probe no session row serves, which the About tab reads once and nothing polls. <!-- probe: git grep -cE "\$\{API\}/health" -- webapp/lib/api/reads.ts == 1 --> <!-- probe: git grep -cE "\$\{API\}/" -- webapp/lib/api/reads.ts == 1 -->

## Viewed identity — one address (CyclePath)

**"What am I looking at?" is `viewed`, owned by `lib/workspace.tsx`; no surface keeps a second or re-derives a hop**, and a surface reads `useWorkspace()` itself, never a callback handed down. Display re-roots to the LEAF hop; ingest and Files stay on the ROOT. A command is addressed by the whole path; whether a kind acts on an inner run is the server's. **The deep link is the whole address in the HASH** (a static export has no SPA fallback), spelled in `Subject`'s hierarchy grammar (`address.ts`), and the view keys (round, sample set, observe) ride it as `;name=value`, written through `workspace.tsx::setViewKeys` alone; `ids.ts::encodeCyclePath` is also the server's `?descend=` format, byte-exact. **The one candidate pick is `workspace.tsx::pick`**, its one writer `workspace.tsx::pickCandidate`; one 95% band per channel, a bar-chart channel declared once in `components/candidates/series.ts`.

## Reads and failure

- **No read asks on a clock: the server says what moved.** The cache subscribes once to `GET /feed` (`promptpotter/application/cycle_reads.py::feed_frames`); a descriptor names the `subject` that moves it and its answer says the versions it stood on (`FEED_VERSIONS_HEADER`), a frame re-asks every mounted read whose subject it starts and whose answer stood on another version (`read-cache.ts::moved`), a view column off screen mounts none (`components/shell/Views.tsx`'s `<Activity>`), and a write names no read. The one clock left is a failed read's retry (`RECONNECT_POLL_MS`); no provider mirrors the cache. <!-- probe: git grep -cE "setInterval\(" -- webapp/lib/*.ts <= 1 --> **Two live channels, the account's feed and the viewed cycle's tail (`useCycleEvents`), and one history channel**, no SSE join. <!-- probe: git grep -cE "new EventSource" -- webapp/*.ts <= 2 -->
- **Every read rides `useRead`, named by one descriptor in `lib/api/reads.ts`**; no hand-rolled fetch. <!-- probe: git grep -cE "fetch\(" -- webapp/components/*.tsx == 0 --> **Every write rides `useCommand`**, its sentence `operatorMessage(e, kind)`.
- **A bare `catch` is the bug**: branch on `failureKind(err)` and `failurePasses(kind)`, never a status literal. **`gone` (404) is terminal and stops the retry** (I7). Failures are reported with ids only, never measurements or prompt text.
- **Never render "verified" while `BRAND.verification` says `self-declared`.**
