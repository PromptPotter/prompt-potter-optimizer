# Survey round — the optimizer finds the band before it descends

> Read [`../architecture.md`](../architecture.md) § Bench and optimizer first; a survey is a round
> kind inside its *A round is a neutral envelope*, which already holds the round's own row
> addresses (`RoundCells`). **Nothing on this page is built** — status is
> [`roadmap.md`](roadmap.md)'s; this page is the contract the build is measured against.

**Whatever configuration a run starts from is assumed far from the region where the search can
descend quickly.** So the optimizer's own first act is to survey: propose several starting points,
measure them cheaply against the given one, read where the task sits between its floor and its
ceiling, and descend from the best. On one dataset that is a choice of start; under
`promptpotter-self` the same round also takes the dataset as an axis. It is not transfer — nothing
measured by another campaign is injected — and not a screen beside the run: it IS a round of the run.

## Settled

- **One mechanism, three ways in.** At run init (round 1 is a survey); mid-run, elected by L2 or L3
  when the layer judges there is no better way forward; and as a setting, on or off or tuned, so it
  can be ablated. Each way in reaches the same round kind and nothing else.
- **Off is always expressible and nothing hard-wires it.** A manifest with no `survey` pipeline has
  no survey; every knob is a node knob the campaign overlays. Once the optimizer's own prompts start
  well, the survey may stop paying, and that is a reading, not a refactor.
- **It varies starts, never the instrument.** Rule 0.1 holds whole: the inner solver's
  `reasoning_effort`, `max_tokens` and `temperature` never move, and no outer arm moves a model.
- **Nothing is paid twice.** Every survey cell is an ordinary walk into the content-addressed
  archive.
- **The verdict gates; it never ranks.** Headroom does not predict lift (arXiv 2605.26655, as reported by
  the 2026-10-14 literature pass; unverified at the abstract), so the band reading admits and holds, and the manifest's own selector elects.

Everything below is the current PROPOSAL for the mechanism; the stage that builds a part re-derives
the shape with the fewest new concepts first and rewrites its section here when it differs.

## Vocabulary

| Term | Means | Reuses / collides |
|---|---|---|
| **survey round** | a round whose arms are new starting points rather than edits of the parent | new. Not "calibration": that names the δ-ruler fit (`domain/ruler.py`), LEVI's round, and `promptpotter-self/panel` |
| **`survey` pipeline** | the manifest walk a survey round takes | **renames** `initial_population` (`optimizers/nodes.py::INITIAL_POPULATION`) — "initial" stops describing it once the escalation can call it mid-run |
| **standing reference** | the arm every start is read against: the origin at init, the current parent mid-run | the existing `reference_id` |
| **band verdict** | `below_floor` · `collapsed` · `saturated` · `flat` · `in_band` | new; replaces three hand-applied band definitions (§ What it absorbs) |
| **environment** | a (dataset, solver) pair an L4 cell measures | existing: an L4 pool seat (`promptpotter-self/pool/dataset.yaml::panel`) |

## The round

A survey round:

1. Has the manifest's own proposer write **K starting candidates** — for potter that is `l1_generate`,
   the node that writes every round's candidates, asked for K starts instead of K edits; no new proposer.
   `capo_init` and `levi_seed` become survey members unchanged.
2. Adds the **standing reference** as one arm. At init the given origin is always measured.
3. Measures every arm on **one shared order** drawn by the manifest's sampler.
4. **Races** them with the manifest's own eliminator (PoBB, or CAPO's `paired_t`), first test at
   `elimination_n_min` — successive halving expressed in members the bench already runs (APE, BOHB;
   § Precedent).
5. Lets the manifest's selector elect the next parent, as any round does.

**What it varies:**

- **Prompt starts, always.** No banked winner of another campaign is ever an arm.
- **Params**, only inside the node's own `optimizer.param_keys` / `param_allowed_values` — the space
  round 1 already searches.
- **Model, honestly bounded.** On one dataset it is a candidate dimension only where the dataset opens
  `model` in `param_keys` — then an ordinary priced axis, the operator's deployment choice; otherwise
  the survey reports the band for the pinned model and moves nothing. Under L4 a model appears only as
  part of an environment, admitted per seat, never as a lever an arm earns, and never moves inside an
  inner cell (`runner/inner/tasks.py::inner_instrument_config`). Effort stays at the model's floor
  (`application/optimizers/potter/CLAUDE.md` § Origin = conservative floor).
- **Environments (L4 only).** The outer panel lists candidate environments; a `promptpotter-self`
  survey measures the outer origin plus K outer starting arms (optimizer-prompt variants, prompt
  families, the block catalogue on or off) over a census of them — one round, both axes.

## The band reading

Computed by the backend and served; no surface recomputes it.

- **Per arm:** level with a Wilson interval; the constant-answer floor; the margin over it and whether
  it is settled; the answer modal share; $/row and s/row. The floor and margin logic moves out of
  `application/diagnostics/seed_screen.py` (`class_floor`, `SeedReading`) into `domain/`, beside
  `domain/scoring.py::modal_answer_share` and `is_answer_collapsed`.
- **Per round:** **separability** — the spread of arm levels against the pooled SE, i.e. whether
  prompts can move this score at all; **learnability** — the mean over rows of p̂(1−p̂); **position** —
  the level against the floor, and against `accuracy_ceiling` only where it is declared. The best
  observed start is served as the observed best, never as a ceiling (`../../promptpotter/assets/datasets/CLAUDE.md`
  § A ceiling is DECLARED).
- **The verdict** is a typed enum in `domain/`, its thresholds knobs on the `survey` node, so the
  focus band and the L4 admission band are two settings of one predicate, not two documents.
- **L4:** each environment's origin cell carries the floor and modal share beside
  `domain/l4/inner_facts.py::InnerCellFacts.origin_headroom`; the admitted set is banked as a decision
  the way `PRICED_DRAW` is (`optimizers/potter/members.py::AdaptiveQueue.draw`), and later draws read
  it. **A held-out seat is never admitted or rejected by a survey** — it is the bench, so the lift the
  pool reports stays transfer.

## Three ways in

### At run init

With `at_init` on, **round 1 is a survey round**: `optimizer_manifest.py::RoundPlan.walk` returns the
`survey` walk where no population stands — today's seeding branch, generalized. Round 0 stays the
origin, so **C0 is still the given configuration** and every lift is read against it.

The top-level manifest key `initial_population_walks` (`optimizer_manifest.py::PipelineManifest`) goes; its
`alone` / `before_default` split becomes the survey node's `races_offspring` knob (LEVI `false`, CAPO
`true`), so the setting sits on the member that reads it.

The origin gate reads the band verdict beside health: `runner/termination.py::origin_gate_tripped`
takes it, so a `strict` gate holds on `below_floor`, `collapsed` or `saturated` before any proposer
spend.

Every launch enters the runner, so this reaches all five entry points with no new seam.

### Elected by the escalation

**A third layer-control field, `survey_proposal`** (`{reason}`), beside `fork_proposal` and
`terminate_proposal` on `L2ContextOutput` and `L3PlanOutput`
(`optimizers/potter/dispatch/schemas.py`), gated by **`survey_capability`** on `EscalationKnobs`
(`optimizers/potter/knobs.py`, beside `rebase_capability` / `terminate_capability`) whose injection
renders empty when off — the property the other two bits have.

It is handled at the post-apply seam in `optimizers/potter/escalation/firing.py`, after terminate and
before fork (terminate outranks both). Accepted — non-blank reason, capability on, survey share left —
it writes a **pending survey** on the `l3_memory` slice, restated on the ledger as every fire already
is, so a resume or fork keeps it. Refused, the reason lands on the warnings channel. It raises no
`StopLoop` and mints no cycle. The next round reads it — `RoundPlan.walk(population, survey_pending)`
returns the survey walk with the current parent as standing reference — and consumes it.

**An LLM proposal, not a rule in `escalation/rules.py`**: the rules route and never diagnose, and "no
better way forward" is the recoverability judgment that lives in the LLM tier
(`optimizers/potter/CLAUDE.md` § L2). A `flat` or `saturated` verdict from the last survey reaches L2
as a decision-frame fact, never as a fire. `escalation_ladder: l1` suppresses it, since no L2 or L3
call is made. The vocabulary in `optimizers/potter/CLAUDE.md` § The layer-control channel becomes
three verbs, and that sentence moves with the build.

### The setting

Every knob is on the `survey` node's `config` in the manifest — `at_init`, `candidates` (K),
`races_offspring`, `share`, the verdict thresholds — and a campaign overlays it through
`optimization.nodes.survey.config`, the one overlay (`application/optimizer_manifest.py::select_optimizer`).
The escalation half is `escalation.config.survey_capability`. Each declares
`Knob(Scope.POLICY, Estimand.SELECTION)`.

**The ablations are one knob each**, read `controlled` by `evidence`'s head-to-head: `at_init` on vs
off answers whether a surveyed start helps; `survey_capability` on vs off whether a mid-run survey does.

## Replay and budget

- **Replay by content.** A later round re-reading the elected start on rows the survey drew replays
  (`infrastructure/store/measurement_archive.py::ReplayFeed`); round 0's origin rows replay into the
  survey's reference arm; a mid-run survey re-proposing a measured start replays; a resume replays all
  of it.
- **No repeats, no `force_fresh`.** Width over depth: the SE is binomial across rows. That is what
  keeps the survey replayable, and why retest noise stays `noise-floor`'s question.
- **L4 cells run shallow and deepen in place.** A seat's identity excludes depth
  (`domain/cells.py::DEPTH_FIELDS`; depth is continued on a cell, never forked), so a survey runs each
  candidate environment at one inner round, and an admitted seat's later round continues the same inner
  campaign.
- **`share` bounds every survey in a cycle together, on both axes** (`<one-budget>`): at most
  `share × budget.usd` and the same fraction of `budget.wall_clock_s`. Priced before the first send from
  the archive's cell price (`initialization/loop_start.py::measured_cell_usd`, `cell_bound`) × rows ×
  (K + 1), and refused before it when the bound exceeds what is left — the shape
  `application/preflight.py::refuse_arm_below_round` already has. Racing makes the realised cost
  roughly the origin's × (1 + the survivors' fraction).

## What it absorbs

| Today | Under this spec |
|---|---|
| `seed-screen` verb | Its verdict moves to `domain/`; `draw_bank` / `draw_bench` move beside `runner/inner/spawn.py`, their other reader. Whether the verb goes is open decision 3. Its sidecars stay on disk as data. |
| `initial_population` pipeline + `initial_population_walks` | Renamed `survey`; the key becomes `races_offspring`. LEVI's calibration round is a survey with `races_offspring: false`. |
| [`roadmap.md`](roadmap.md) § Members the presets need, *LEVI's calibration, ours too* | Lands here, as a round kind rather than a check-in step, because the escalation needs a round. |
| Hand-curated pool admission (`promptpotter-self/pool/dataset.yaml::panel` comments) | The panel lists candidates; admission is the served verdict, banked per run. |
| The band in [`../operations/dataset-selection-rationale.md`](../operations/dataset-selection-rationale.md) § Selection criteria and [`../research/benchmarks.md`](../research/benchmarks.md) § Every dataset we measured | One served verdict; the docs keep the rule and name the knob. |
| Origin gate on health alone | Health plus the band verdict. |

**Not absorbed:** `noise-floor` (retest noise of one configuration), `probe-reasoning` (a model's
capability card), `accuracy_ceiling` (a declared claim).

**§0 bucket:** § Bench and optimizer → *A round is a neutral envelope*, with § Origin, parent, and
check-in for the reference. **No new I/O kind** — the band record is Persistence, its served reading
Display, `survey_proposal` Control-local on the existing layer-control channel. An operator command
would be Control-remote and is open decision 5.

## Precedent

Read 2026-10-16; abstract-only reads are marked.

- **Starts are cheap and many, judged small, then raced.** MIPROv2 bootstraps demo sets and proposes
  instructions before its search ([2406.11695](https://arxiv.org/html/2406.11695) §3.1); APE filters
  50 candidates on growing subsets ([2211.01910](https://arxiv.org/html/2211.01910) §3.2); CAPO races
  on blocks ([2504.16005](https://arxiv.org/html/2504.16005)); BOHB's successive halving
  ([1807.01774](https://arxiv.org/html/1807.01774v1)).
- **Whether the start matters depends on the model.** OPRO's start mattered and persisted under
  PaLM 2-L and hardly under text-bison ([2309.03409](https://arxiv.org/html/2309.03409) §5.3);
  EvoPrompt's init ablation stayed within ~2 points ([2309.08532](https://arxiv.org/html/2309.08532)
  Table 6). That is the case for keeping the survey ablatable.
- **Signal lives near p ≈ 0.5.** SFL's learnability p(1−p)
  ([2408.15099](https://arxiv.org/html/2408.15099v3) §4.1); DAPO keeps rows with accuracy strictly
  between 0 and 1 ([2503.14476](https://arxiv.org/html/2503.14476)).
- **No source measures a mid-accuracy band for prompt optimization.** Ours comes from the curriculum
  literature's statistic and our own admission screens.

## Open decisions

Each is the operator's; the recommendation is not a decision.

1. **DECIDED 2026-10-16, as recommended — the default.** Off in every manifest, potter's included; the
   `promptpotter-self` family's `campaign` overlay turns `at_init` and `survey_capability` on for the L4
   learning work. One line turns it back.
2. **DECIDED 2026-10-16 — no new machinery for the starts.** `l1_generate` writes them and the round
   elects as every round does; C0 stays the given origin, so the surveyed gain counts in the lift from
   origin, and the held-out bench absorbs the best-of-K selection.
3. **DECIDED 2026-10-16, as recommended — the `seed-screen` verb.** Delete it; the survey's band record replaces its
   sidecar and an environment screen becomes a one-round run. The cost: a screen then mints a campaign.
4. **DECIDED 2026-10-16 — the `Decision` cell kind is deleted** with the bank; a seat is the one cell
   kind.
5. **DECIDED 2026-10-16, as recommended — an operator twin for the escalation's way in:** a `survey-next-round`
   command kind with a CLI verb, declared in [`api-openapi.yaml`](api-openapi.yaml) first, writing the
   same pending survey the layer writes, so the operator and L2/L3 share one channel
   (`<entry-point-parity>`).
6. **DECIDED 2026-10-16, as recommended — signal as a quantity that moves with the stage of the run.** The operator's framing: each
   combination (dataset × model × prompt × params) yields a different amount of signal, and which ones
   yield most changes early, mid and late in a run, so selection maximizes signal and is redone as the
   run moves. This page's settled line "the verdict gates; it never ranks" and its two triggers (init,
   elected when stuck) do not yet say that. *Recommend:* one served quantity, the expected information
   of a combination at the CURRENT parent — the mean of p̂(1−p̂) under the parent's θ, the statistic
   `methods/verdict-resolution.md` already ranks samples by, lifted from rows to environments. It is
   recomputed every round from what the archive already holds, and the sampler/pool draw ranks by it
   (so an easy dataset early and a hard one late fall out of θ rising, not out of a schedule). The survey
   stays the round that BUYS fresh estimates where the archive holds none. Ranking on information is not
   ranking on headroom, so the cited result does not bear on it.

## Build order

Each stage closes on a verification.

1. **The verdict in `domain/`.** *Closes when* it reproduces every seat and rejection the kept
   seed-screen sidecars record. Their cells were wiped with the workspace, so the recompute buys
   its own rows.
2. **The rename and `races_offspring`.** *Closes when* CAPO's and LEVI's offline legs
   ([`../developer/offline-run.md`](../developer/offline-run.md)) decide and send exactly what they did.
3. **`l1_generate` writing starts, `at_init`, and the gate reading the verdict.** *Closes when* potter's offline leg
   at `at_init: off` renders round 1 unchanged.
4. **`survey_proposal` + `survey_capability`.** *Closes when* L2/L3 prompts are byte-identical with the
   capability off, and with it on a scripted proposal makes the next round a survey a resume restores.
5. **L4 admission and shallow cells continued in place.** *Closes when* the one-cell
   self-optimization leg shows a survey cell continued, not re-minted.
6. **The ablation (PAID).** `at_init` on vs off on `promptpotter-self/pool`, matched on incurred USD and
   cells, read `controlled`, quoted from `rounds_to_separable` on held-out seats.
