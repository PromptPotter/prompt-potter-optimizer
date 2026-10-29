# Survey round — the optimizer finds the band before it descends

> Read [`../architecture.md`](../architecture.md) § Bench and optimizer first; a survey is a round kind
> inside its *A round is a neutral envelope*. **Nothing here is built** — status is [`roadmap.md`](roadmap.md)'s.

**Whatever configuration a run starts from is assumed far from where the search descends quickly.** So the
optimizer's first act is to survey: propose several starts, measure them cheaply against the given one, read
where the task sits between floor and ceiling, and descend from the best. Under `promptpotter-self` the same
round takes the dataset as an axis. It is not transfer — nothing another campaign measured is injected — and
not a screen beside the run: it IS a round of the run.

## Settled

- **One mechanism, three ways in**: at run init, elected by L2/L3 when there is no better way forward, and as
  a setting. Each reaches the same round kind.
- **Off is always expressible**: a manifest with no `survey` pipeline has none; every knob is a node knob.
- **It varies starts, never the instrument**: the inner solver's `reasoning_effort`, `max_tokens` and
  `temperature` never move, and no outer arm moves a model.
- **Nothing is paid twice**: every survey cell is an ordinary walk into the content-addressed archive.
- **The verdict gates; it never ranks** — headroom does not predict lift
  ([`../research/external-constraints.md`](../research/external-constraints.md) owns the source), so the band
  admits and holds and the manifest's own selector elects.
- **Default off in every manifest**; the `promptpotter-self` family's `campaign` overlay turns `at_init` and
  `survey_capability` on.
- **No new machinery for starts**: `l1_generate` writes them; C0 stays the given origin, so the surveyed gain
  counts in the lift from origin and the held-out bench absorbs best-of-K selection.
- **The `seed-screen` verb is deleted** when this lands; an environment screen becomes a one-round run (which
  mints a campaign). The `Decision` cell kind goes with the bank; a seat is the one cell kind.
- **An operator twin**: a `survey-next-round` command kind with a CLI verb, payload declared first, writing the
  same pending survey the layer writes (`<entry-point-parity>`).
- **Signal moves with the stage of the run.** One served quantity ranks combinations at the CURRENT parent: the
  mean of p̂(1−p̂) under the parent's θ ([`../methods/verdict-resolution.md`](../methods/verdict-resolution.md)'s
  row statistic, lifted to environments), recomputed each round from the archive; the sampler/pool draw ranks
  by it. The survey is the round that BUYS estimates where the archive holds none. Ranking on information is
  not ranking on headroom.

Below is the current PROPOSAL; the stage that builds a part re-derives it with the fewest new concepts.

## Vocabulary

| Term | Means | Reuses / collides |
|---|---|---|
| **survey round** | a round whose arms are new starts, not edits of the parent | new; not "calibration" (the δ-ruler fit, LEVI's round, `promptpotter-self/panel`) |
| **`survey` pipeline** | the manifest walk a survey takes | **renames** `application/optimizers/nodes.py::INITIAL_POPULATION` |
| **standing reference** | the arm every start is read against: origin at init, current parent mid-run | `reference_id` |
| **band verdict** | `below_floor` · `collapsed` · `saturated` · `flat` · `in_band` | new |
| **environment** | a (dataset, solver) pair an L4 cell measures | an L4 pool seat |

## The round

1. The manifest's own proposer writes **K starts** — potter's `l1_generate` asked for starts instead of edits;
   `capo_init` and `levi_seed` become survey members unchanged.
2. The **standing reference** is one arm; at init the origin is always measured.
3. Every arm is measured on **one shared order** from the manifest's sampler.
4. The manifest's eliminator **races** them (PoBB or CAPO's `paired_t`) from `elimination_n_min` — successive
   halving in members the bench already runs.
5. The manifest's selector elects the next parent.

**It varies** prompt starts always (never another campaign's winner); params only inside the node's
`optimizer.param_keys` / `param_allowed_values`; a model only where the dataset opens `model` (otherwise the band
is reported for the pinned model), and under L4 only as part of an environment admitted per seat, never inside an
inner cell (`application/runner/inner/tasks.py::inner_instrument_config`); effort stays at the model's floor. Under
L4 a survey measures the outer origin plus K outer starts over a census of candidate environments — one round,
both axes.

## The band reading

Served; no surface recomputes it.

- **Per arm:** level with a Wilson interval, the constant-answer floor, the margin over it and whether it is
  settled, the modal answer share, $/row and s/row. The floor logic moves from
  `application/diagnostics/seed_screen.py` (`class_floor`, `SeedReading`) into `domain/`, beside
  `domain/scoring.py::modal_answer_share`.
- **Per round:** **separability** (arm-level spread against pooled SE), **learnability** (mean p̂(1−p̂)),
  **position** (against the floor, and `accuracy_ceiling` only where declared). The best start is served as the
  observed best, never a ceiling.
- **The verdict** is a typed enum in `domain/`, its thresholds knobs on the `survey` node, so the focus band and
  the L4 admission band are two settings of one predicate.
- **L4:** each environment's origin cell carries floor and modal share beside
  `domain/l4/inner_facts.py::InnerCellFacts.origin_headroom`; the admitted set is a banked decision
  (`Replay.decide`). **A held-out seat is never admitted or rejected by a survey** — it is the bench.

## Three ways in

**At run init.** With `at_init` on, round 1 is a survey: `application/optimizer_manifest.py::RoundPlan.walk`
returns the `survey` walk where no population stands. Round 0 stays the origin, so C0 is the given configuration.
`PipelineManifest.initial_population_walks` goes; its `alone` / `before_default` split becomes the survey node's
`races_offspring` knob (LEVI `false`, CAPO `true`). `application/runner/termination.py::origin_gate_tripped` takes
the verdict, so a `strict` gate holds on `below_floor`, `collapsed` or `saturated` before proposer spend.

**Elected by the escalation.** A third layer-control field, `survey_proposal` (`{reason}`), beside
`fork_proposal` and `terminate_proposal` on `L2ContextOutput` / `L3PlanOutput`
(`optimizers/potter/dispatch/schemas.py`), gated by `survey_capability` on `EscalationKnobs`
(`optimizers/potter/knobs.py`), whose injection renders empty when off. Handled in
`optimizers/potter/escalation/firing.py` after terminate and before fork; accepted, it writes a **pending survey**
on the `l3_memory` slice (restated on the ledger, so resume and fork keep it), raises no `StopLoop`, mints no
cycle; the next round's `RoundPlan.walk(stands=…)` takes it as a second input and consumes it. It is an
LLM proposal, not a rule in `escalation/rules.py` — rules route, never diagnose. A `flat` or `saturated` verdict reaches L2 as a
decision-frame fact; `escalation_ladder: l1` suppresses it.

**The setting.** `at_init`, `candidates` (K), `races_offspring`, `share` and the thresholds sit on the `survey`
node's `config`, overlaid through `optimization.nodes.survey.config`; the escalation half is
`escalation.config.survey_capability`; each is `Knob(Scope.POLICY, Estimand.SELECTION)`. **Each ablation is one
knob**, read `controlled` by `evidence`'s head-to-head.

## Replay and budget

- **Replay by content** (`infrastructure/store/measurement_archive.py::ReplayFeed`): origin rows replay into the
  reference arm, a re-proposed start replays, a resume replays all of it.
- **No repeats, no `force_fresh`** — width over depth; retest noise stays `noise-floor`'s question.
- **L4 cells run shallow and deepen in place**: depth is outside a seat's identity (`domain/cells.py::DEPTH_FIELDS`),
  so an admitted seat's later round continues the same inner campaign.
- **`share` bounds every survey in a cycle on both axes** (`<one-budget>`): `share × budget.usd` and the same
  fraction of `budget.wall_clock_s`, priced before the first send from
  `application/scoring/sample_measurement.py::measured_cell_usd` × rows × (K + 1), and refused when it exceeds what
  is left (the shape of `application/preflight.py::refuse_arm_below_round`).

## What it absorbs

- `seed-screen`: its verdict moves to `domain/`; `draw_bank` / `draw_bench` move beside `runner/inner/spawn.py`.
- `initial_population` + `initial_population_walks`: renamed `survey` + `races_offspring`; LEVI's calibration
  round is a survey with `races_offspring: false` ([`roadmap.md`](roadmap.md) § Members the presets need).
- Hand-curated pool admission (`promptpotter-self/pool/dataset.yaml` `panel` comments): the served verdict, banked per run.
- The band in [`../operations/datasets.md`](../operations/datasets.md) and [`../research/benchmarks.md`](../research/benchmarks.md):
  one served verdict; the docs keep the rule and name the knob.
- The origin gate: health plus the band verdict.

**Not absorbed:** `noise-floor`, `probe-reasoning`, `accuracy_ceiling`. **§0 bucket:** § Bench and optimizer →
*A round is a neutral envelope*. **No new I/O kind** — the band record is Persistence, its reading Display,
`survey_proposal` Control-local, `survey-next-round` Control-remote.

**Precedent:** [`../research/external-constraints.md`](../research/external-constraints.md) § Survey round.

## Build order

Each stage closes on a verification.

1. **The verdict in `domain/`.** *Closes when* it reproduces every seat and rejection the kept seed-screen
   sidecars record (buying its own rows).
2. **The rename and `races_offspring`.** *Closes when* CAPO's and LEVI's offline legs
   ([`../developer/offline-run.md`](../developer/offline-run.md)) decide and send exactly what they did.
3. **`l1_generate` writing starts, `at_init`, the gate reading the verdict.** *Closes when* potter's offline leg at
   `at_init: off` renders round 1 unchanged.
4. **`survey_proposal` + `survey_capability`.** *Closes when* L2/L3 prompts are byte-identical with it off, and on,
   a scripted proposal makes the next round a survey a resume restores.
5. **L4 admission and shallow cells continued in place.** *Closes when* the one-cell self-optimization leg shows a
   survey cell continued, not re-minted.
6. **The ablation (PAID).** `at_init` on vs off on `promptpotter-self/pool`, matched on USD and cells, read
   `controlled`, quoted from `rounds_to_separable` on held-out seats.
