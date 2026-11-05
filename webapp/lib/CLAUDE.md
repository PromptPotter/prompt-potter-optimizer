# webapp/lib — CLAUDE.md

## Where a module lives — by what it IS

**`api/`** the wire · **`state/`** shared state: one context provider or module store per file, kebab-case, the context `<Subject>Context` · **`prefs/`** one hook over one `STORAGE_KEYS` entry · **`derivations/`** a pure reading of served data · **`hooks/`** a read, command or browser hook · **`types/`** a TS-only type · the root is infrastructure every tier imports. A context one surface alone consumes lives in that surface.

## Display-data sources

**One source per data class.**

| Surface | Reached by | Sole source for |
|---|---|---|
| `dashboard.json` | `lib/poll.tsx` → `useViewedDashboard()` / `useDashboard(path, intervalMs)` | in-flight `current_round` + `rounds[]`: FitnessChart and the SPEND breakdown |
| `/hierarchy` | `lib/hooks/useRegistry.ts` → `useRegistry()` / `useEntry(address)` | every surface that lists, switches, docks or heads a run; tags via `lib/derivations/hierarchy.ts` |
| `/hierarchy/{address}` | `lib/lineage.tsx` | the genealogy at any depth, indexed once per body by `lib/lineage.tsx::lineageIndexOf` |
| `/campaigns/{id}/cycles/{id}/rounds/{n}` | `lib/hooks/useRound.ts` | the round (`RoundView`); `round_NNNN.json` is shown only as raw text in Files |
| `…/rounds/{n}/audit` (AUDIT TWIN) | `lib/hooks/useRoundNodes.ts` | per-node LLM I/O; selects on `round === current_round.round` here alone |
| `/datasets/{name}/cells` | `lib/hooks/useCells.ts::useCells` | every list of measured cells; one answer opens off `/cells/{answer}` |
| `/campaigns/{id}/pipeline?at=` | `lib/hooks/useConnector.ts::useConnector` | every config row, model chip, lock glyph, provenance badge; a check-in's `draft` and `CampaignFacts.runs_with` are other transports of the same answer (`I9`) |

- **No stitch**: never merge in-flight with historical or fall back to a round file; where two are one series the SERVER merges. Take a WHOLE candidate row from one half.
- **A prop names WHICH campaign/round, never WHERE to read**; provenance is the served `source`, never a client diff.
- **One 95% band per channel**; a bar-chart channel is declared once in `components/candidates/series.ts`.
- **Join arms on `reading.arm.key`; key entries on `address`, never `facts.individual`** (`splitRetired`). `reading.election.selected: false` alone says nothing — ask `reading.election.held`.
- **The sidebar reads the served `CampaignFacts.line`** and follows no successor pointer. <!-- probe: git grep -cE "superseded_by" -- webapp/lib/hooks/useRegistry.ts == 0 -->

## A wire shape is GENERATED — never hand-declared

**Every response type comes from `lib/api/types.generated.ts`** (gate `ts-types`), imported through `lib/api/types.ts` alone, the one type door; a narrow alias is derived (`ResponseType["field"]`). **A closed set belongs on the server**, and so do its words: a served enum is worded by its `promptpotter/shared/worded.py::Worded` table, which registers itself when built and is exported under the binding that declares it (e.g. `STATUS_MARK_INFO`, `STOP_REASON_INFO`), never a local `Record<…, string>`; iterate one through `lib/api/words.ts`; a composed line is a served `Said` row.
<!-- probe: git grep -cE "^const [A-Z_]+_(LABELS|TONE|ADVICE|NAME): Record" -- webapp/*.ts <= 2 -->

## Viewed identity — one address (CyclePath)

**"What am I looking at?" is `viewedPath`, a `CyclePath` (`lib/ids.ts`), owned by `lib/workspace.tsx`; no surface keeps a second.** The one subject beside it is an unstarted `checkin` (`#/d/<draft id>`, `#/new`), under which `viewedPath` is null; `lib/chat/thread.ts` keys one thread per subject. **`workspace.tsx::navigate` moves the subject, `open` / `close` the views**; a surface reads `useWorkspace()` itself, never a callback handed down. Display re-roots to the LEAF hop (`pathLeaf`, `useLeafIsL4`); ingest and Files stay on the ROOT. A command is addressed by the whole path (`lib/api/commands.ts::cycleAddress`), an inner run's included; whether a kind acts on one is the server's, its `inner_refusal` (`domain/command_kinds.py::CyclePayload`): a text where no inner address is admitted (the 422 `inner_cycle_unaddressed`), `None` = acts on an inner run.

**The deep link is the whole address in the HASH** (`lib/address.ts` syntax; `lib/workspace.tsx` sole reader/writer) — a static export has no SPA fallback. `ids.ts::encodeCyclePath` is also the server's `?descend=` format: byte-exact.

## Polling shape

**One clock** (a descriptor's `cadence` on `lib/read-cache.ts`'s beat, the most eager subscriber's per id; every interval a named export of `lib/cadence.ts`) **and one invalidation** (`invalidateReads(name?)`; a write's answer re-asks the reads its `useCommand` `revalidate` names, every read where it names none). **No provider mirrors the cache**: each read is one `useRead` subscription (`read-cache.ts::subscribe`), and what a read does once per id — keep across re-keys, accept, report gone, react to a change — its descriptor declares. A cadence is a constant (the listing, the active pointer, a connector probe, machine status), the read's own state (`read-cache.ts::CadenceState`: the draft's served stage, a write in flight on it), or the interval its caller names (`intervalMs`) where it is a fact of another read: the viewed dashboard 2 s, a replayed moment `RECONNECT_POLL_MS` (`poll.tsx::viewedIntervalMs`); its steps the same, or slow while it is lost, unless the run is settled; a campaign tree unless the listing has every course settled; the viewed cycle's cells while it appends; evidence while a measured course is attached; another cycle's dashboard once, or while a verify pass is awaited. A failed read's cadence is the cache's (`intervalOf`: a failure that passes (`client.ts::failurePasses`), and a 404 until its gone verdict, re-asks no faster than `RECONNECT_POLL_MS` nor than its own cadence; a refusal the server would repeat rests). A change re-asks from the descriptor whose body shows it (`onChange`): the viewed cycle's phase change every mounted read (each `useDashboard` caller of the viewed cycle carries `reads.ts::onViewedPhaseChange`), the listing's moved bill the coupon; `useEvidence`'s pulse re-asks evidence when a measured course moves. No read rests on a subject's version: that is the change feed's (`docs/specs/roadmap.md` Q2). The viewed cycle's scoring mask is seeded once, where its dashboard lands (`poll.tsx::useViewedDashboard`); every mask reader reads the store alone. A key not known yet is `held` on its keep body, never idle. A derived index memoizes on the body it read. State that names no view is a module store (`lib/state/module-store.ts`). <!-- probe: git grep -cE "setInterval\(" -- webapp/lib/*.ts <= 1 --> One tree per campaign, fetched once; the step window (`/hierarchy/{course}/@`) is the chronology, not a second tree. Steps and SSE frames arrive READ (`promptpotter/domain/activity.py::ActivityFeed`). **One live channel (`useCycleEvents`), one history channel** — no SSE join. <!-- probe: git grep -cE "new EventSource" -- webapp/*.ts <= 1 -->

## Failure handling — classify, don't bucket

**Every read rides `lib/hooks/useRead.ts`, NAMED by one descriptor in `lib/api/reads.ts`**; same `id` shares one flight and body. No hand-rolled `useEffect` fetch. A hook hands back the `ReadResult` with the served field names; it never re-encodes it into its own loading or failure record. <!-- probe: git grep -cE "fetch\(" -- webapp/components/*.tsx == 0 -->

**Every write rides `lib/hooks/useCommand.ts`** (`slot.run`). The sentence is `operatorMessage(e, kind)` (`lib/api/errors.ts`; a write's is `commandMessage`), rendered by `ui/states::FailureLine`; a hook carries the `ReadFailure` / `CommandFailure`, never a string.

**A bare `catch` is the bug.** `failureKind(err)` (`lib/api/client.ts`) classifies into `transient | throttled | auth | gone | denied | invalid | refused`; branch on that, never a status literal, and on `failurePasses(kind)` beside it for whether a failure passes. **`gone` (404) is terminal and stops the poll** — only the dashboard read reports it (`onGone`, once per id), after `GONE_CONFIRM_LIMIT` misses, to `workspace.tsx::reportAddressGone`, and only the viewed cycle's says its campaign is gone, a pinned one's alone returning to the active run (`poll.tsx::connectionSaid`). A read that `expects` a 404 lands it as a null body: answered (`ready`), never a failure. **Failures are reported with ids only** (`lib/diagnostics.ts`, the API's `error_id`) — never measurements or prompt text.

## Brand identity

`lib/brand.ts::BRAND` is the single brand source: `vite.config.ts` resolves `brand-of.ts::brandOf` once from the `VITE_*` overrides and defines the result, which the page head, the manifest and `BRAND` all read (`brand-of.ts` holds the defaults; the names are spelled there and in `deploy-linux/brand-env.sh` only). **Never render "verified" while `BRAND.verification` says `self-declared`.**
