# Webapp Display-Source Unification

Architecture cleanup of the webapp ↔ projection seam. Collapses the
dual-write/dual-read pattern between `dashboard.json` (live, in-flight)
and `round_NNNN.json` (finalized, on-disk) into a single source per
data class. The current split forces the webapp to stitch two streams
with timing-fragile gates; every gap and "wait for the next round to
see the previous one" symptom in `webapp/lib/poll.tsx` is a direct
consequence.

Sibling cleanup to [`state-sync-cleanup.md`](../state-sync-cleanup.md)
(five-surface state drift). That spec collapses *state* surfaces;
this one collapses *display-data* surfaces.

## Problem

The same conceptual data — "round N's candidates and their scores" —
is written to disk in two formats with different freshness semantics:

1. **In-flight** — `LiveDashboardView` mirrors the round-in-progress
   into `dashboard.json::current_round.nodes.l1_score.output.candidates`.
   Updated on every `sample_scored`, wiped on `round:display` and on
   the next `L1_GENERATE:enter`.

2. **Finalized** — `AuditTrailView` writes the same round's
   candidate scores to `.runtime/cache/rounds/round_NNNN.json` at
   round close (atomic, tmp+rename).

The webapp polls dashboard at 2 s and separately fetches all
`round_NNNN.json` files, then `FitnessPanel` (and four siblings)
stitches them with a `historicalRoundsSeen.has(currentRound)` gate
that switches each round between the two sources.

### How the split bites

| Anti-pattern site | Severity | Symptom |
|---|---|---|
| **FitnessChart in-flight ↔ historical** | High (operator-reported) | Bars vanish at `round:display`, reappear at next `L1_GENERATE:enter`. The historical refetch trigger fires on `liveRound` bumps, which happen at round *start*, not at round *end* — so the just-finished `round_NNNN.json` only lands in the cache one round later. |
| **LineageTree in-flight merge** | Medium | Defensive merge of `liveL1Candidates(dash)` with historical rounds, self-documented as "without this the lineage lags the fitness panel by a whole round" (`LineageTree.tsx:72`). Symptom of the same root cause. |
| **origin_accuracy stitch** | Medium | `dash.origin_accuracy` vs `round_NNNN.json::origin_accuracy` — both populated, FitnessPanel prefers historical with a live fallback. Origin bar can blink at cycle bootstrap if dashboard hasn't written `origin_accuracy` yet. |
| **Per-sample stream re-sort** | Low | HardSamplesTable shows live sample rows during scoring, then they snap to a different order once the round file lands. |
| **Halt-after-N stale in-flight** | Low (latent) | If the operator halts after round N closes (no round N+1 starts), the in-flight block keeps showing round N's candidates forever, because the L1_GENERATE:enter wipe never fires. With the original `round:display` wipe in place, the opposite bug (empty in-flight) shows instead. There is no halt-correct state in the current design. |
| **`dashboard.json` non-atomic write** | Latent (race-window widens with each new field) | `LiveDashboardView._persist()` uses direct `path.write_text()` (`view.py:600-603`). `AuditTrailView` uses tmp+rename via `write_json()` (`store/base.py:73-97`). A polling reader can catch dashboard mid-write today; adding a `rounds[]` field grows the byte count and widens the window. |

### Why patching one site doesn't fix it

The 2026-05-23 one-line stopgap (removing the `_round.candidates = {}`
wipe at `view.py:331`) silenced the FitnessChart symptom for the
operator, but it didn't touch the root cause. Four observations:

1. **The same root cause owns four other consumers** — LineageTree,
   TopStrip, TrendChart, FreqChart all read `historyDocs` via
   `useCycleStream()`. Anything reading historical rounds via the
   shared cache has the same gap; the others just haven't been
   reported because they degrade more gracefully.
2. **The stopgap moves the bug** rather than removing it. The wipe
   at `view.py:361` (L1_GENERATE:enter) still exists; halt-after-N
   is "wrong but not catastrophic" only because no operator has held
   that state long enough to notice. There is no correct halt state
   under the current design.
3. **Every new chart component pays the tax.** A future contributor
   adding a round-aware view writes either an in-flight reader OR a
   historical reader OR (if they're careful) the stitch logic again.
   The pattern is contagious by design.
4. **The non-atomic dashboard write is a separate latent bug** that
   any field-addition (including the stopgap's effective behavior of
   keeping more data in `current_round`) widens.

## Target architecture

**One writer per data class. Webapp reads one source per consumer.**

### Two surfaces, two contracts

**`dashboard.json` is the display projection.** It carries
*everything the bar chart needs to render*: origin row, every
completed round's per-candidate summary (`RoundSummary[]`), and the
in-flight round's live candidate samples. Atomic writes. Bounded
size (one row per round; ~3 candidates per row; ~250 bytes per
candidate ⇒ ~1 KB per round; a 100-round campaign ≈ 100 KB on top
of the existing dashboard payload).

**`round_NNNN.json` is the deep-audit trail.** It carries the full
LLM input/output (template fields, response, tokens), per-sample
evaluator rows, full results arrays, scoreboard with `per_sample`.
The webapp reads it *only* on demand when the operator drills into
a specific round (FreqChart distribution, ScoringInspector per-sample,
OptimizerNodeDetail node-by-node inspection). No eager all-files
polling.

### Webapp consumer tiers

Discovered by the Phase-0 exploration (Explore agent reports
attached in the planning conversation):

| Consumer | New source | Reason |
|---|---|---|
| FitnessPanel | `dash.rounds` + in-flight `current_round` | summary only |
| TopStrip | `dash.rounds` | one scalar per round (sparkline) |
| TrendChart | `dash.rounds` | two scalars per round |
| LineageTree | `dash.rounds` | label + winner + accuracy + parent |
| FreqChart | `useRoundFile(round)` — lazy | full `results[]` |
| ScoringInspector | `useRoundFile(round)` — lazy | `per_sample[]` on selection |
| OptimizerNodeDetail | `useRoundFile(round)` — lazy | full `nodes` subtree on selection |
| HardSamplesTable | unchanged (already API-driven) | independent path |

### What `current_round` means after the change

Strict single semantic: **the round whose samples are landing right
now.** Wipe sites collapse to one (`L1_GENERATE:enter` for round N
clears the prior in-flight, in lockstep with `state["round"]`'s bump
to N). On halt, `current_round` correctly stays as the last
in-flight round. Dashboard readers know everything they need: the
just-completed rounds are in `dash.rounds`, the live one is in
`dash.current_round`, the two never overlap.

## Phases

Phases ship in order; each is a separate commit and survives in
isolation.

### Phase 0 — Atomic dashboard writes

Wrap `LiveDashboardView._persist()` in tmp+rename, or call the
existing `write_json()` helper at `store/base.py:73-97`. Precondition
for everything else — adding `rounds[]` (Phase 1) grows the byte count
and widens the partial-read race.

- **Files**: `promptpotter/infrastructure/projections/live_dashboard/view.py:600-603`.
- **Risk**: minimal — `write_json` is the standard pattern already
  used by every other store write.

### Phase 1 — Emit `dash.rounds[]` from the projection

Define `RoundSummary` (Pydantic model in
`promptpotter/domain/`). `LiveDashboardView` accumulates the
just-finished round's summary into `state["rounds"]` on
`round:display`, before the persist call. No webapp change yet —
validate the projection end-to-end by hitting `/dashboard` and
spot-checking the new field.

- **Schema** — per-round entry:
  ```
  {
    round: int,
    origin_accuracy: float | null,   # carried for C0 cross-check
    candidates: [
      {
        candidate_id: str,
        label: str,                    # "C1.0", "C2.3"
        accuracy: float,
        composite_fitness: float,
        scored_samples: int,
        expected_samples: int,
        is_winner: bool,
        hits_phase1: int | null,
        total_phase1: int | null,
        hits_phase2: int | null,
        total_phase2: int | null,
        changes_description: str | null,
        evaluators: dict[str, float] | null,    # for what-if
      },
      ...
    ],
  }
  ```
- **Files**: `view.py` (writer), `domain/results.py` or sibling
  (schema), `tests/test_invariants.py` (no change — field lives
  inside the existing `SESSION_TELEMETRY_ARTIFACTS` allowlist).
- **Resume safety**: dashboard is display-only; resume reads from
  the ledger, not from dashboard. Adding state is free.

### Phase 2 — Migrate summary-only consumers

FitnessPanel, TopStrip, TrendChart, LineageTree switch to
`dash.rounds`. Each commit migrates one consumer end-to-end:

- Update the component to read from `dash.rounds`.
- Drop its dependency on `useCycleStream().rounds` / `historyDocs`.
- Remove any in-flight-vs-historical merge logic (e.g. the
  `historicalRoundsSeen.has(currentRound)` gate in
  `FitnessPanel.tsx:273-276`, the defensive merge in
  `LineageTree.tsx:65-100`).

At the end of Phase 2, revert the Phase-0 stopgap in `view.py:331`
(the wipe is harmless once consumers stop depending on the in-flight
block carrying historical data). Half of Phase 5's work falls out
of the migration.

### Phase 3 — Shrink `poll.tsx::refreshRounds`

After Phase 2, only the deep consumers (Phase 4) still need round
files. `refreshRounds` and `historyDocs` come out of the shared
`CycleStreamContext`; a new `useRoundFile(round: number)` hook
fetches a single file on demand, keyed on the operator's selection.

- **Files**: `webapp/lib/poll.tsx`, new `webapp/lib/useRoundFile.ts`.
- The `lastRoundRef` / `roundsFetchingRef` / listing-poll dance
  disappears entirely. The dashboard-stamp guard stays.

### Phase 4 — Lazy fetch for deep consumers

FreqChart, ScoringInspector, OptimizerNodeDetail switch to
`useRoundFile(selectedRound)`. Each component already has a "selected
round" notion (the round drilled into); the hook fetches on selection
and caches per round.

- **Eager fallback**: when no round is selected, default to the most
  recent (highest-`round` entry in `dash.rounds`). One file fetch
  on mount, not all-files.

### Phase 5 — Drop the `_round` wipes

Remove the wipe at `view.py:361` (L1_GENERATE:enter). With Phase 1 in
place, the in-flight block's role narrows to "what's being scored
right now" — the L1_GENERATE:enter transition can clear via a
different mechanism (e.g. `candidate_started` with `ci == 0` clears
the prior `candidates` dict; or the round-number transition itself).

Audit the origin-completion guard at `view.py:338` for any latent
dependency on `_round.candidates` being empty between rounds. Replace
the gate with a positive condition ("origin just completed and origin
row is missing") instead of a relied-upon side effect.

`current_round` semantics tighten to: "the round whose samples are
landing right now." Halt is correct by construction.

### Phase 6 — Sweep remaining stitch sites

Walk the inventory from the Problem section and close each:

- **origin_accuracy** — pick one writer. Recommended: dashboard owns
  it (read from `dash.origin_accuracy` only); strip
  `round_NNNN.json::origin_accuracy` or document it as audit-only
  with the webapp never reading it from there.
- **HardSamplesTable per-sample re-sort blink** — drive the table
  off `dash.current_sample_id` + `dash.hard_sample_order` only;
  drop any historical merge.
- **Any new site discovered during the sweep** — log and address.

Grep checkpoint: after this phase, `webapp/` has zero call sites
that read the same conceptual data from two physical sources and
choose between them based on freshness.

### Phase 7 — PoBBStream audit

**Decision: keep** (2026-05-23). `PoBBStreamView` writes
`.runtime/streams/round_NNNN_p_best.jsonl` for trajectory plots and
post-hoc posterior debugging. The webapp doesn't read it (verified via
grep), but operator value is independent of webapp consumption — the
stream is the canonical record of paired-difference posterior evolution
per sample, used for offline analysis. No action required.

### Phase 8 — Validation

CI green across the full chain:

- `ruff check . && ruff format --check . && mypy promptpotter/ && pytest -q`
- `cd webapp && npm run lint && npx tsc --noEmit && npm run build`

Manual smoke (in order, against a fresh `python -m promptpotter new
justlogic` or similar):

1. Run 3+ rounds. Watch the FitnessChart through every
   `ROUND N SUMMARY` print — bars stay visible continuously
   through round close, L1 critique, and next L1 generate.
2. `Ctrl+C` after round 2 closes. Reload webapp. R0/R1/R2 bars
   render; `current_round` shows R2 as the last in-flight round.
3. `python -m promptpotter resume --fork-on-divergence` on a
   re-tuned cycle. Fork's dashboard isolates correctly; parent's
   dashboard untouched.
4. Click a historical round in the lineage tree. FreqChart and
   ScoringInspector populate via `useRoundFile`; no eager refetch
   on poll tick.
5. Sweep restore (multi-variant). Active pointer flips between
   variants without inflight-vs-historical mismatches.

### Phase 9 — Documentation

- `docs/architecture.md` §0 — if the Persistence I/O kind's
  examples reference `round_NNNN.json` as a webapp source, retitle
  to "deep audit trail; on-demand fetch only."
- `promptpotter/infrastructure/CLAUDE.md` — update the projection
  table to reflect the split (`LiveDashboardView` = display surface,
  `AuditTrailView` = deep audit).
- `webapp/AGENTS.md` — drop the historyDocs/render-phase-reset
  pattern note if it stops applying; add the `useRoundFile`
  pattern.
- `docs/specs/CLAUDE.md` — link this spec from the Reference
  table; remove from TODO once the arc closes.
- `.ai/CODEMAP.md` — regenerate via
  `python scripts/build_ai_index.py`.

## Acceptance criteria

- [x] Bar chart bars never vanish at a round boundary; halt-after-N
      shows N's bars indefinitely until the next operator action.
      (Structural — chart no longer reads the in-flight wipe.)
- [x] `webapp/lib/poll.tsx` has no `rounds: RoundFileDoc[]` field on
      `CycleStreamState`; `historyDocs` removed.
- [x] Eager `round_NNNN.json` polling is gone — round files fetched
      only on operator drill-in via `useRoundFile`.
- [x] `dashboard.json` writes are atomic — `_persist()` calls
      `write_json(...)` (tmp+rename).
- [x] `LiveDashboardView._round` has one wipe site (L1_GENERATE:enter).
- [x] `current_round.round` always equals "the round whose samples
      are landing now," with no override on round close.
- [x] Zero call sites in `webapp/` read the same conceptual data
      from two physical sources and choose between them based on
      freshness.
- [x] All CI gates green (ruff/format/mypy/pytest + webapp
      tsc/eslint/build).
- [ ] Manual smoke checklist (Phase 8) passes on a live campaign.
      (Operator validation — pending the next live run.)

## Non-goals

- **Backwards-compatibility with on-disk `dashboard.json` files** —
  per project rule, none. Operators resume; projection re-emits.
- **Webapp control plane (launch/stop/resume/fork)** — that's
  `m10-operator-control-loop.md`; this spec leaves write paths to
  that arc.
- **Multi-tenant / auth / SaaS hardening** — `m12-control-plane.md`.
- **Schema validation of `dashboard.json`** — useful but separable;
  scope to its own follow-up if Phase 8 surfaces drift.
- **PoBBStream consumption in the webapp** — if Phase 7 decides
  "keep", visualizing the live stream is a separate feature.

## Open questions

1. **Does `dash.rounds[]` belong on the dashboard or on a sibling
   file?** Two options:
   - **(a) On dashboard.json** (recommended). One fetch covers
     everything the chart needs; consistency by construction.
   - **(b) Separate `summary.json`** with its own atomic write +
     its own poll. Smaller dashboard payload; one more file +
     fetch. Only worth it if dashboard size becomes a problem at
     100+ rounds; size estimate says no.
2. **Should `RoundSummary.evaluators` carry full per-evaluator
   floats or just the summary stats?** What-if ablation needs the
   full dict to recompute composite client-side. Decision: carry
   the full dict; size estimate above already includes it.
3. **Origin row** — currently emitted from a mix of
   `dash.origin_accuracy`, `dash.origin_samples`, and
   `round_NNNN.json::origin_accuracy` fallback. Recommendation:
   single source on dashboard, with `dash.rounds[0]` reserved
   for round 1's results (origin sits in its own
   `dash.origin: {accuracy, samples, ...}` block, not in
   `rounds[]`).
4. **Per-round atomicity of incremental updates** — `dash.rounds[]`
   appends one entry per round close. Mid-round writes do not
   touch `rounds[]`. Confirm no race between the appendone and
   the dashboard write (none expected since both happen on the
   same `_persist()` call).
