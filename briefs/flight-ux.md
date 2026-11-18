# flight-ux: the parallel-calls control says what it does, and the CLI can press it (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `flight-ux`, arc
`arc/flight-ux`. Also read `webapp/CLAUDE.md`, `webapp/components/CLAUDE.md`,
`promptpotter/presentation/CLAUDE.md` and `.claude/skills/design/SKILL.md` (mobile first).

- **Base:** `origin/main` at `7d5d12f7b`. Your `<base sha>`.
- **The operator (2026-11-18):** "make helpful additions to the parallelism meter and the every round
  toggle … we should also be able to trigger those things through the TUI/CLI (parity for us)".

## Decisions (build them, do not reopen them)
- **The sample look-ahead stops being browser-only** (the operator's word reverses the documented
  boundary). `set-sample-lookahead` (`domain/command_kinds.py`, today `BROWSER`) is pressable from
  the CLI on the existing command channel, so CLI, REST, `/potter-run`, `embedded_run.py` and the
  webapp reach it alike. Its capability rung `campaign.lookahead` (decisions § 0005: it spends the
  shared rate bucket) STAYS: who may press it is unchanged, only where. Delete the boundary
  everywhere it is written: `presentation/CLAUDE.md` § Sample look-ahead, root `CLAUDE.md`'s
  carve-out ("and browser-only sample look-ahead"), `docs/operations/access-model.md`'s
  "Browser-only —", the `command_kinds.py` comment; `gate.py --only doc-claims` must stay green.
- **A launch takes its depth**: `new` and `resume` accept the depth and its every-round mode, filed
  as the same `set-sample-lookahead` command before the walk's first cell (an origin's cells are the
  ones a slow run waits on), never a second mechanism or config key.
- **The CLI reads what the panel reads**: `dashboard` (and the verb's reply) print the served
  `SampleLookahead` rows: depth in force and its scope, calls out now of the most allowed, discards
  so far, and why a deeper press is not admitted. One wording table (`served_dashboard.py::
  LOOKAHEAD_WORDS`), never spelled per surface.
- **The panel** (`RemotePanel.tsx::SamplesInFlight`; first move it into its own file
  `webapp/components/shell/remote/SamplesInFlight.tsx`, a pure move commit-internal):
  - the control is named in words ("Parallel calls"), the `⇉` glyph alone names nothing;
  - under the segments, one quiet served line: calls out now · depth in force · discards so far;
  - a depth that is not pickable says why where it is drawn (the backend's ceiling, the cycle
    settled, money: `affordable`), never only in an aria label (invariant I3);
  - the every-round switch shows its scope in words (`holds_this_round` / `holds_every_round`),
    not only on hover. No second switch in this chain.
- **The toggle that would help next is NOT yours**: "inner runs too" (each L4 inner run's own sample
  walk at the outer's depth; today `SetSampleLookaheadPayload` says the outer's depth is never
  inherited). It lives in `runner/inner/**`, which the l4-outer chain owns: file it under "Rows to
  file" with its `file:line` and the one-line mechanism, do not build it.

## Three ways to run, all kept (the operator, 2026-11-18 ~11:10)
"we wanna have this new upgrade as well as being able to have the old style going, as well as the
fastest option that exists. the controls might become more in toggles or so, that's okay."
- **One at a time** (the old style): depth 1, the δ-order walk an early cut reads cheapest, zero
  discards. It stays the default and a named first segment ("One at a time"), never only "1".
- **The upgrade**: a chosen depth, this round or every round (the switch above).
- **Fastest**: a `Max` segment that tracks the ceiling the backend admits (`most`), never a frozen
  number, so it stays the fastest that exists as the ceiling moves; CLI `--lookahead max`. With every
  round on, it is the fastest an outer can run until "inner runs too" (filed, not yours) adds the
  inner walk.
- Toggles are fine where they read better than segments (every round is one); one control per axis.
If this section reaches you after your links are pushed, the orchestrator builds it in the landing's
fix pass; do not reopen a pushed link for it.

## Beside you
- **limits-ux** (cloud) owns `RunLimitsControl.tsx`, `lib/run-limits.ts`,
  `application/commands/limits_and_queue.py`, and may add a disabled-reason line inside
  `SamplesInFlight`: yours wins that hunk at landing. **l4-runs** (cloud) and limits-ux both touch
  `served_dashboard.py` / `live_dashboard.py`: name the file in your commit body, touch only your
  hunk (`SampleLookahead` and its words). **l4-outer** (local) owns `application/runner/inner/**`,
  `application/optimizers/potter/**`, `domain/l4/**`.
- You own `SamplesInFlight`, `useRemoteModel.ts`'s look-ahead hunk, the CLI verb and its readout,
  `loop_commands.py::set_sample_lookahead`, the docs above, and their tests.

## Proof (beyond common.md)
- On the offline world: `new … --lookahead 3 --every-round` (or the flags you name) runs its origin
  with 3 calls in flight (the served `depth` and `in_flight`), and the CLI verb on a live run moves the
  served depth; the readout says it.
- Phone-width (390 px) and desktop screenshots of the panel before and after, through the browser
  walk's harness (`webapp/e2e/`), on your `out/flight-ux-report` branch beside `report.md`.
