# L4 outer loop + the shared in-process execution seam

> The real L4 recursion runs end-to-end (`new promptpotter-self` mints + runs real inner campaigns). This doc is the **living finish-line plan** for what remains — § Finish line below is the single owner of what has shipped and what has not.
>
> **Scope:** CLI / headless only — no webapp surface yet (that's a later lane). The outer loop is a normal `python -m promptpotter new promptpotter-self` invocation.
>
> **Prerequisite:** [`fitness-comparability.md`](fitness-comparability.md) — the outer fitness reads inner-campaign improvement, which must be subset-invariant (θ-based).

## Finish line — a distributable `promptpotter-self` (drives the remaining work)

**The goal is no longer "make L4 run" (done) — it is "ship a `promptpotter-self` an operator can `new` and watch the optimizer improve its OWN optimizer prompts, at bounded cost, with spend visible."** Every remaining slice is judged against that. The AI agent owns L4 end-to-end and drives it autonomously (commit small green arcs); escalate to the operator only for genuine actions — real spend approval on a multi-campaign run, a provider/account change, or a compaction handoff. Definition of done:

1. **An inner benchmark the inner loop can still climb.** A benchmark the inner model already aces gives the loop nothing to find — every outer candidate scores the identical composite. **Chosen: `justlogic-d234`** — JustLogic depths 2-4 (iid mix), a reasoning task where optimizer prompt quality plausibly moves the needle; `inner_tasks.yaml` points at it. (The operator's bet: the inner model's `Uncertain`-hedging under low effort is an ADDRESSABLE behaviour the loop can crack in the first few rounds, not a hard capability ceiling — so optimizer prompt quality has room to move the score. Which JustLogic cut is the instrument, and why a cut switch is never advice, is owned by [`../../datasets/CLAUDE.md`](../../datasets/CLAUDE.md) § L4.)

   > **There is no declared target, and no *expected* headroom — deliberately.** Declaring a target asserts up front how much room the benchmark has; that assumption reaches no decision and is epistemically backwards. **A task the inner model looks bad at is a task it has not been TUNED for yet, not a task with a low ceiling** — gpt-oss-20b can be prompted a long way up on justlogic. The default posture is *optimistic*: assume the room is large unless the evidence is unambiguous. The lift core is the raw climb on the ability ruler and divides by nothing — not by a declared target, and not by an inferred "room" either.
   >
   > What this does NOT excuse: **improvement on justlogic is real but INFREQUENT.** A seed that fails to move under one optimizer prompt is weak evidence, not proof the seed is flat — do not read a quiet panel as "no headroom", and do not retire the benchmark on one run. The panel spans a *range* of seed difficulty rather than a wide count, because duplicated difficulty buys nothing. Seeds are chosen on measurement (`python -m promptpotter seed-screen`): a bank whose constant-answer floor EXCEEDS its origin is REJECTED — it pays a candidate for collapsing to one label — and the rest are ranked on `reasoning_margin` (origin − floor), never on accuracy, which conflates an easy bank with a large majority class. **The floor is exact and the origin is not**, so the verdict needs repeated passes (default 3) and is WITHHELD while the margin sits inside its own error bar. A thin POSITIVE margin disqualifies too.
   >
   > **Two readings that outlived their runs.** A collapse verdict off ONE origin pass is not settled: the screen once condemned a seed on a margin its own second read reversed, and both passes straddled the floor. Do not cite a single-pass collapse call — that is what `repeat` and `verdict_settled` exist for. And the raw floor is the wrong axis to rank on: a seed can draw a HIGHER floor and not reward collapse at all, while its settled `reasoning_margin` is about one row in the bank. A bank that pays a single row for reasoning over hedging is not measuring reasoning, whichever side of its floor it lands on.
   >
   > **What the screen deliberately does NOT measure.** What a panel cell is really worth is its INFORMATIVE width — rows that are neither impossible nor free, since only those separate two optimizer prompts — and that cannot be bought in advance. A fresh bank shares too few rows with everything ever measured to inherit the width from history, and one extra probe with the strongest banked prompt separates too few rows for a between-seed difference to clear its own noise. The audit that *does* resolve it pools dozens of measurements per row across a whole campaign: a by-product of running the panel, not something a screen can buy. So informative width stays an after-the-fact read off the measurement archive.
2. **Outer mutations actually reach the inner optimizer prompts — done** (§3 below). The number stays because code cites these positions.
3. **Spend is visible — done.** Each inner campaign's total rides its `CycleResult.spend` and returns as that outer sample's `step_tokens`, fanning onto the outer ledger through the existing backend-cost channel. The inner cost IS the outer sample's backend cost, so "spend is the headline" holds at the outer level without a second mechanism (`runner/inner/spawn.py::run_inner_cycle`).
4. **A bounded, cheap default config.** The committed `inner_tasks.yaml` + `campaign.json` must let `new promptpotter-self` complete at a cost an evaluator will tolerate. Cost is **geometric** (one outer round = n_variants × n_inner_tasks inner campaigns, each a full inner campaign) AND each inner optimizer call is slow. So: few inner tasks, few samples, few inner rounds, low outer `max_rounds`/`n_variants` by default — and consider pinning the inner+outer optimizer to a faster provider (groq) in the shipped config. Document the cost shape for the operator. The **stall brake** is what keeps the geometry honest: `inner_lives` (+1 per improving round, −1 per stall, stop at 0 → `LIVES_EXHAUSTED`) ends a stalling inner campaign early, so a dead optimizer prompt is cheap and only a compounding one buys depth. **INVARIANT: `lives.start` must sit well below `max_inner_rounds`.** Set near it, the bank cannot drain before the calendar cap — every inner then runs full-length regardless of quality, and that also removes the geometry's only brake, since an optimizer prompt that finds nothing then burns the same rounds as one that compounds (a term with no candidate gradient earns nothing — the governing law is the type, `domain/l4/proxies.py::OuterSampleProxies`; `inner_tasks.yaml` is a typed declaration now, not a place to write prose). **The brake is only free because the measurand divides by the BUDGET** (§4): `mean_round_delta` holds the last adopted level forward over the rounds a stopped cell never ran (`held_levels`), so ending early saves the money without moving the score.
5. **A run that demonstrably improves.** The validation gate (`proxy_lift_corr ≥ 0.6` over ≥4 paired branches) PLUS at least one real `new promptpotter-self` whose outer best beats its outer origin — the proof the cheap proxy predicts real lift and the loop climbs. **Read item 7 before reading this one:** "outer best beats outer origin" is satisfied by noise whenever the winning arm's own interval spans zero, so clearing it proves nothing on its own. Ask for the interval, not the ordering.

   > **STATUS 2026-08-07 — the INNER half is measured, the OUTER half is not, and the gap is deliberate.** After five fixes (labelled-field render, `ability_delta`, the `task_context` splice, the origin rendering on its candidates' basis, and the projection absorbing the warm round-0 θ), inner cells adopt and score clearly positive for the first time — the C0 panel ran cells at `mean_round_delta` +0.2754 / +0.6871 / +0.7959, every one adopting, against a prior history of flat θ with C0 winning every round. **Nothing here says the OUTER loop separates arms.** Round 0 holds one arm, so `p_best` cannot leave its tie and no arm can go negative; that needs a round-1 election, ~14 further cells beyond the panel. It was NOT run because the spend cap lands almost exactly at PoBB's `elimination_n_min` floor with no margin, and concurrency work comes first. On the next `new promptpotter-self`: read `p_best` off a completed round-1 election, confirm at least one regressing arm goes negative, and only then read item 7's interval test. Until that run, treat every claim in this file about outer behaviour as untested.
6. **The loop owns its own information flow (§6) — mechanism built; the validating data run is open.** A distributable `promptpotter-self` must let the optimizer improve *how its optimizer prompts are built*, not only their prose. Which signals each inner node sees (the per-node injection set) is the higher-value, dataset-agnostic axis. It **is now a searched axis** with a mandatory guard-rail floor: every optimizer node owns a `NodeLayoutSpec` in `NODE_LAYOUTS` (`domain/l1_layout.py`), and the L4 layout edit is wired for the three `editor == "l4"` nodes. What remains is **validation, not construction** — a full-signal run showing outer candidates that differ by inner-node *layout* (not only prose), with the winner's layout captured. Sequenced alongside 3–5 on the same run.
7. **What the panel can claim about a leader — and what it cannot.** The finish-line goal — *tuning a very good base configuration for the L1/L2/L3 loop, driven by the outer loop* — is only meaningful once an outer difference is larger than the panel's own error. A panel that cannot separate arms still prints a leader, and reading that leader as a finding is the failure mode this phase is most exposed to.
   - **What the corpus reports, at zero spend.** Each arm's anchor-to-origin paired effect with its own interval, plus `OuterSpread` — how far apart those effects are across arms — off the same walk (`application/optimizer_prompt_ranking.py`, served by `python -m promptpotter rank-optimizer-prompts` and `GET /optimizer-prompt-ranking`). Its per-round peer `PanelPrecision` (on every `rounds[]`) reports ONE round's estimation noise beside its observed between-cell spread, off `mean_adopted_level_se` — the arm's OWN half of the paired diff, the shared origin level excluded because it cancels rather than adding twice. Two bars, never their ratio: the ratio was served once as `estimation_share`, and `min(1.0, …)` rendered a raw 5.55 — noise claiming to exceed the spread it is a component of — as a tidy "100% measurement noise". An interval that excludes zero is the evidence; the ordering alone is not.
   - **There is no within-cell noise term, by design.** Reading one (state, cell) twice is not a second measurement: the inner instrument is content-addressed end to end — the campaign key, the `shared_root` caches, the seeded bank draw, CRN, the optimizer clamp — so the second ask replays the first and its spread is zero by construction, which reads as a perfect instrument. Manufacturing one (numbering the draw so it misses every cache) measures how noisy an LLM is on an identical request, which is not a quantity the loop can act on. **Depth on a specific candidate is `verify`'s job** — it re-scores one candidate on MORE samples and records the result without touching the cycle, which tightens the estimate of the thing being compared instead of sampling the same question twice.
   - **So the levers are the operator's.** Variance reduction (more cells, more samples, or a low-variance one-step proxy in place of the high-variance full-trajectory measurement) and deliberately diverse hand-authored optimizer prompts. A loop cannot grow a signal it cannot yet distinguish, which is why this stays operator-driven until the arms separate on their own intervals.
   - **One way the instrument can still lie, and it is refused.** A cell that failed is not a cell that scored zero (`scoring/selection.py::_scoreable`, shared by `composite_ci` and the lift interval). The ELECTION grades an errored row 0.0 on purpose — the overlap guard needs that — but a published interval may not, because at L4 a floored cell does not read as "scored nothing", it reads as "drove the inner loop maximally down".

## Running & supervising a live `promptpotter-self`

**The infrastructure is done; the optimizer *application* is not.** The loop, seams, recursion, and scoring gateway all exist and are green. What remains is making the optimizer *behave well*, and that is found empirically: **run `new promptpotter-self` on `justlogic-d234`, collect the data, read what the loop actually produced, fix the bug at its ROOT, re-run.** Expect several restarts; this is the loop, not a failure. **Read a leader against its own interval, never against its rank** (item 7) — a panel that cannot separate arms still prints a leader, and reading that leader as a finding is the failure mode this whole phase is exposed to. Most roots in this phase turn out to be prompts rather than code — that is where the causes have been, not a rule that the fix must be small.

**The cadence must be SELF-FIRING, not event-driven.** A supervising agent schedules its own
wake-ups (the harness's ScheduleWakeup / self-paced loop, ~150–270 s) the moment a run starts,
and each wake IS a researcher pass over the reading list below. A passive log Monitor does NOT
count as supervision — it only fires on patterns you predicted, and every real bug so far
(estimator inconsistency, evidence starvation, proxy annihilation) was found by reading the
run's own measurement files, not by a grep hit. Monitor stays as a supplementary alarm only.
Role split: the operator is the developer/user (UX); the agent owns everything else.

**Supervise every live run actively — never fire-and-wait.** While a run (or any long optimization) is in flight, poll its output at least **every 2 minutes** looking for a newly-surfacing bug — read the fresh dashboards/measurements/run readout, not just the exit code. **The 2-minute window is for fanning out and researching — not for pausing.** Spend each interval *actively investigating*: fan out parallel searches over the fresh dashboards/measurements/run readout, chase the newest anomaly, read what the loop just produced. The cadence is **not** "check once, then wait" — it is "keep investigating and researching, up to 2 minutes, then check again." An idle wait between ticks is the wasted-run failure mode this guards against. A background run you set and forgot is a wasted run; the bugs show themselves *while it runs*, and catching one early lets you kill-fix-restart before the whole spend drains.

**Run `new promptpotter-self` ONCE, in the FOREGROUND, to completion — never as a killable background task.** A harness/OS kill orphans whatever inner `justlogic-d234` run is open; the reaper stamps it `producer_vanished` (a `FAILED` outcome — correctly excluded from scoring, so the headline is *not* corrupted, but NOT cached). The next relaunch re-opens that seed's campaign and continues it from the rounds already banked — an inner campaign is keyed on the cell it runs, the optimizer-prompt overrides it runs under, and what it is FOR — a candidate's own panel cell, or a PoBB backfill catching a prior up out of the round's shared order (`runner/inner/spawn.py::inner_campaign_id`). So a retry lands back on the same campaign instead of minting a fresh random one, and a backfill can never land on top of a panel run: same cell and same overrides, but a different experiment, so a different directory. What a kill still costs is the round it interrupted, not the whole cell. And because the outer `cycle_id` is deterministic (hash of optimizer prompts + benchmark), every re-`new` with unchanged optimizer prompts collides on ONE `cycle_id` + ONE `.inner/` sandbox — the lineage tree then renders the *same* inner seed-runs under N campaign headers, so "N campaigns, identical stats" is one measurement shown N times, not N runs. To iterate a live run, `resume` it; only `new` again after an optimizer prompt/config change (which mints a fresh `cycle_id`).

**Default the fix to the prompts** (`promptpotter/assets/optimizer/` — `pipeline.yaml::resolved_prompts` for the inner set, `sets/self_optimizing.yaml` for the outer one: wording, evidence framing, the per-node edit schema). Reach past prompts to a code fix ONLY when the data shows a structural cause — broken information flow (a signal the prompt needs never reaches it), a missing analysis (evidence the loop should compute but doesn't), or a wiring gap. Name that structural cause before touching code; do not add new infrastructure to paper over a prompt problem.

### THE PER-CHECKUP READING LIST — every 2-minute tick reads ALL of these, not just the log tail

**A checkup that only greps the run-readout tail is NOT a checkup.** Each tick, open the newest
`{cycle}/.runtime/cache/rounds/round_NNNN.json` (outer) and read every LLM tier's actual I/O:

1. **`l1_generate`** — rendered input (are the panels populated or empty? for the OUTER generator,
   is the `inner_narratives` panel present with one story per seed — the primary evidence a
   optimizer prompt edit must ground on, not the scalar per-seed delta?), raw output, parsed
   variants: `evidence_grounding.field` in the real enum (now includes `inner_narratives`)? citations
   quote text that EXISTS in the rendered input? hypotheses distinct (not one idea relocated)?
   `changes_description` actually REPORTS the override emitted beside it (not a change the variant
   never made)? Any hallucinated node/param (validation drops)?
2. **`l1_critique`** — input carries the evidence, and WHICH panel is the evidence depends on the
   level: inner reads SAMPLE TRANSCRIPTS + MODEL REASONING, outer reads INNER RUN NARRATIVES. The
   two are a matched pair, each silent where the other fires (`panels.py::_inner_narrated`), because
   transcripts are selected by a MISS and one level up a miss is a placeholder-label artifact the
   outer critique is told to ignore. Output `priority_fix`/`failure_highlights` quote CONCRETE
   evidence (a reasoning step, a premise), not recycled labels or scoring artifacts — and
   `priority_fix` must name a steer the generator is ALLOWED to make: an edit to the inner
   optimizer's own job, never one naming the benchmark's vocabulary or answer labels. It once
   prescribed the `justlogic-d234` modus-tollens idea, which is both forbidden there and already
   measured and lost, and the generator spent all three candidates rebutting it.
3. **Scoring** — per-candidate `candidate_scores` (accuracy, θ, θ_se, `composite_ci_lo`), the
   **matched-origin** comparison (NEVER the cross-subset round-0 origin — subset drift reads as
   lift), PoBB stream (`p_best` moving off 0.5?), `decisions` (cuts firing, and on the right arm?).
4. **`l2_context` / `l3_plan` when fired** — validator failures (`paraphrase_repeat`,
   `dangling_trigger`), whether the task_context delta is evidence-anchored, plan text sane and
   within its render cap.
5. **Spot-check ≥1 inner campaign per outer sample batch** — the same four reads one level down
   in `.inner/<key>/…/campaigns/justlogic-d234__*/`.

Red flags that mean STOP-AND-DIAGNOSE, not keep-watching: `raw_chars: 0` / empty candidate list;
an outer sample returning in ~0.0s (stale-cache reuse); off-enum grounding fields;
any optimizer call > 2 min; a headline Δ that disagrees with `matched_origin_*` / `improved`.

### Cross-run comparability — rules that always hold

- **Absolute outer numbers NEVER travel across runs.** Only a candidate's delta against ITS OWN
  run's origin is meaningful (same discipline as "verdicts compare lift-over-reference per model").
- Within a run, comparisons are **paired by seed** (each candidate runs the same
  `inner_dataset_seed`-pinned banks, per `datasets/promptpotter-self/inner_tasks.yaml`) — draw
  difficulty cancels; trust the paired PoBB/θ reads.
- The `inner_origin` identity fingerprint partitions runs into same-origin families; an
  origin edit = a NEW family. Never pool or compare across families.
- Residual cross-run noise = inner-process stochasticity (inner optimizer sampling, adaptive
  subset picks). Quantify it before trusting cross-run deltas (`noise-floor --k N`).

### The shipped ladder — read it off the files, not off this prose

**The config IS the source of truth** (`datasets/promptpotter-self/inner_tasks.yaml` +
`campaign.json`) — read every knob value there, never off this doc. The rules that stand:

- **No knob changes mid-run** — the JSON baselines are read per inner mint, so a mid-run edit
  splits the run into two fingerprint families.
- **`max_inner_rounds` ≥ 2** — at 1 the inner `levels` trajectory is length-1, so
  the trajectory readings would all be byte-for-byte the same
  number and the formula's two weighted delta terms silently double-count one measurement.
- **`elimination_n_min` is the panel-size floor** — keep the inner-task count at least one
  above it, or crowning starves.
- **CRN is the variance control, and the only one.** A per-cell inner LLM seed shared by the
  origin and every variant (`runner/inner/spawn.py`) cancels *common-input* noise in the paired
  diff. There is deliberately no replication knob beside it: re-running an identical cell
  against the recursive backend replays rather than re-measures, and depth on one candidate is
  `verify`'s job (item 7).

Open knobs to validate on the next supervised run:

- **Inner-optimizer determinism clamp** (`inner_benchmark_config.inner_optimizer_temperature`,
  identity-joined via `connectors/promptpotter.py::_identity_config`): confirm the
  cached-origin paired swing collapses toward the provider floor while the inner
  best-beats-origin rate holds.
- **Seed count** — widening the seed panel was deferred in favor of outer breadth; revisit if
  the noise floor says candidate deltas are real but crowning starves.

Standing invariants (verified — don't re-chase):

- **No config is ever re-measured mid-run**; an origin-identical candidate is illegal
  (`no_op_variant` → L2 heals). The noise-floor capability is the on-demand
  `python -m promptpotter noise-floor --k N` diagnostic, never wired into the loop. The
  per-round verdict is the ENGINE's, not L4's: every candidate carries its blocked lift over
  the origin and that lift's interval (`ScoredCandidate.matched_origin_lift*`, from
  `scoring/selection.py::matched_origin_lift`), stamped at the same site as the matched floor
  and read identically at both levels. When no electable arm's interval excludes 0 the round
  emits `round_not_separable` — the winner stands, but its margin is not a result.
  `panel_precision` is read off the round's winner, else the best arm the round admitted to
  its own election (`domain/results.py::is_electable` — the ONE spelling of that rule, shared
  with `winner.py`'s `electable_count`); a round that admits none reports absence.
- **Candidate-arm inner round-0 is NOT re-measured** — the tenant-global `measurements/`
  store under the cycle's `.inner/<key>/` sandbox + content-addressed reuse replays the
  origin-arm's rows into candidate arms by construction; a "share the origin across arms"
  fix is unnecessary.

## Live-run learnings — bake these in, don't re-discover

- **Inner sandbox is a FLAT registry, not physical nesting.** Inner campaigns live at `<workspace>/.inner/<key>/` (sibling of `projects/`), where the key identifies the owning `(tenant, campaign, cycle)` — NOT nested under the deep outer cycle dir — physical nesting blows Windows' 260-char `MAX_PATH` at depth 1; a flat registry named-by-but-not-under the spawning cycle stays shallow at every depth, so the re-entrancy invariant holds. (`runner/inner/spawn.py::InnerSpawnContext`.)
- **`in_process` connectors must NOT fetch a remote pipeline schema.** `init_services` skips the backend `GET /pipeline` for `in_process` execution and uses the local `pipeline.yaml` alone — otherwise it merges an unrelated backend's nodes under the dataset overlay.
- **L4 datasets carry their "samples" on disk, not as a CSV.** The outer "samples" ARE the inner tasks; `Connector.experiment_file` (`"inner_tasks.yaml"`) loads them through `extract_experiment` at init.
- **An inner failure must degrade, not propagate.** `run_inner_cycle` catches any inner exception and returns a zero-improvement proxy, so a bad outer candidate scores poorly instead of killing the outer cycle. Keep the fallback — but a *clean* origin-gate halt should return real proxies, not the exception sentinel; verify on the justlogic run.
- **Inner mints must NOT clobber the outer's active-pointer.** Every mint site threads `projects_root=session.store.projects_root` so a sandboxed inner mint stamps its own workspace, never the real tenant `active_session.json`. Same latent class in `sweep_runner.py` + `fork_siblings.py` (not hit by the inner loop; left).
- **The outer prompts must be TIGHT — a verbose optimizer prompt can come back with empty content** (`raw_chars: 0`, `l1_provider_empty_response`). Keep `promptpotter/assets/optimizer/sets/self_optimizing.yaml` terse; never fix this by touching the global `assets/optimizer/pipeline.yaml` node config (shared with every inner cycle).
- **The θ election needs graded outcomes, a scored origin, and ≥6 inner tasks.** `Observation.response` carries the graded per-sample fitness (the logistic MAP is valid ∀ y∈[0,1] — bit-identical for binary datasets); the origin is scored in every live run (no shape guess — the round-0 origin gate catches a genuinely-unscoreable origin LOUD); and offline replay shows the LCB election needs **≥6 inner tasks** to crown (at 2, θ_se exceeds the point-lift and it correctly refuses to crown on noise) — matching the composite-fitness panel goal (§4).
- **A zero-candidate round heals immediately.** `l1_zero_candidates` is folded into the existing `l1_generate_unusable` structural-breach rule (one widened predicate, no new rule) and fires L2 straight away instead of burning `l1_patience` dead rounds; the empty call is itemized in spend. Still measure on the supervised run how often the empty-content path fires and whether the L2 reframe recovers it.
- **The outer cycle heartbeats its OWN ledger while awaiting each inner run** ("inner rX/Y · best Z%") — `dispatch/llm_call/heartbeat.py` + `runner/inner/spawn.py`; the chat maps the heartbeat to one upserted progress chip. Without it a healthy outer round looks dead for the whole multi-minute inner campaign.
- **An L4 outer sample must stamp `terminated_at` = the LAST outer node (`l3_plan`), never a mid-chain one.** `terminated_at` is the archive's reuse contract; an inner campaign consumes the ENTIRE outer config at once, so a mid-chain stamp lets prefix-trust replay serve the ORIGIN's rows to any candidate editing a later node (fake 0.0s replays).
- **A NO-OP probe's save REPLACES the origin's archive slot — by design, don't re-diagnose.** `MeasurementArchive.append_run` dedups on `content_hash` (newest wins); reuse stays correct, forensic origin rows live on in `round_0000.json` + the ledger, and index entries whose detail log was replaced dangle harmlessly.
- **Hang triage order: ledger tail → `dashboard.json::run_phase` → `.runtime/` flags → process table by command line → only then mtimes.** Control flags (`pause.flag`) are consumed at the next per-SAMPLE checkpoint — a mid-candidate pause stops within seconds and looks like a freeze to an mtime-watcher. The optimizer-call path already has a hard wall-clock (`_chat_under_deadline` → `OPTIMIZER_TIMEOUT`); overnight deaths with no terminal record are machine-sleep/session-end class, not code.
- **`token_budget` stays `null` for L4** (`datasets/promptpotter-self/campaign.yaml`) — the inner-spend rollup lands each inner campaign's tokens on the outer ledger as backend cost, so the normal-campaign default trips after a couple of inner campaigns while the USD budget sits nearly untouched. For L4 `spend_budget_usd` is the meaningful cap. (Root is L4's scale, not the rollup — the rollup correctly reports real tokens; don't uncount them.)
- **When the inner loop "stalls", suspect the SCHEMA before the prompt.** A schema that mandates the narration and marks the payload optional will get exactly that — a variant that names itself, cites a panel, describes a change and mutates nothing, cascading into a diversity/cleanliness drop, a lives drain and a multi-round stall that all read as a bad optimizer prompt. `L1Variant` generates the payload BEFORE the prose reporting it, and ≥1 override is enforced at parse.
- **Never hand an LLM a character budget — it cannot count characters.** Told its cap explicitly, *with the consequence spelled out*, a node overruns it on essentially every round and loses its tail to the truncation rail — silently, from the frame every downstream prompt reads. A char cap is a **runaway rail, not a budget knob**: express the budget in a unit the writer can honour (bullets, sentences) and let the cap catch only a genuine runaway.
- **An injection reaches a prompt through TWO channels, so "the prompt never says X" needs both checked.** A signal renders either from the node's `NODE_LAYOUTS` floor into an addressable slot, or from a `{{token}}` left in prose — and `fill` resolves both, so a signal wired to both renders twice. `rebase_capability`/`terminate_capability` ride the layout channel alone; it is `mandatory` on both `l2_context` and `l3_plan`, so no prose rewrite or L4 layout edit can drop them.
- **The L3 rung has only ever fired FORCE-TRIGGERED, never on an L2 stall.** Its one banked appearance was `l1_layout` guard breaches force-triggering it at inner round 1 — L2 could not emit a valid layout, because the vocabulary lived in prose while the field's schema stated nothing. So the rung is still unexercised on its designed trigger, and the plan it wrote was reasoning about a defect. Re-tuning patience to chase this changes what the engine decides and voids banked origins through `_identity_config`.
- **A stale prompt file is a live parse failure, not just documentation drift.** `L1Variant` is `extra="forbid"`, so a field deleted from the model but still declared REQUIRED by a prompt set (`assets/optimizer/sets/self_optimizing.yaml`) fails every outer variant at validation. **The Pydantic model, both `answer_format`s, and `resolved_schemas` move in ONE commit or the loop stops parsing.**

## 1-2. The execution seam and its recursion isolation

**Owned by [`../../promptpotter/connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § Execution mode** — the `in_process` arm, the `llm_only`-is-a-node rule, the per-task ContextVar copies and the flat `<workspace>/.inner/<key>/` sandbox. What this spec adds is the **depth-agnostic** invariant that makes L5+ free: the sandbox is named by *this* cycle, and the fresh-task spawn happens at *every* level — never assume depth 1. The recursion ceiling is economic and statistical, not architectural (geometric cost; `proxy_lift_corr` decays with depth), which is the right place for the limit to live. Known forward item: the process-global rate limiter is shared, so inner spend competes with outer for TPM/RPM.

## 3. Specialized outer optimizer prompt set

The outer optimizer mutates whole optimizer prompt templates, judged by outer evidence
(mode-collapse, parse-fail rate, candidate stratification, proxy-lift), so it gets its own
prompt set: **`promptpotter/assets/optimizer/sets/self_optimizing.yaml`** — prompt *fields* only. There is
deliberately **NO per-set `pipeline.yaml`** and `OPTIMIZER_PIPELINE_PATH` stays a
module constant: per-campaign pipeline *resolution* was not built because it would fork the
~600-line schema blob. The set is selected per-cycle by `OptimizationConfig.optimizer_set`
and applied through the **existing** per-node override channel
(`load_optimizer_set_overrides` → `set_optimizer_prompt_overrides` →
`resolve_node_override`) — the same channel the inner runner uses for its mutations, so
outer=self_optimizing / inner=default isolate by task with zero new ContextVar. The outer L1's per-node
edits ride the existing `L1Variant.pipeline_params_override` slot (no new
`OPTIMIZER_RESPONSE_MODELS` entry), and the outer evidence panels are the existing
round-trace signals surfaced as outer injections, not re-derived.

The inner optimizer's **model** is NOT part of the mutation surface: `model`/`provider` are
operator-owned axes the optimizer never searches. The inner model is pinned by the inner
dataset's `pipeline.yaml` (or `inner_tasks.yaml::inner_model`); an operator may override it on
a fork via the seed overlay, but that is a cap-gated babysit edit, not a searchpoint.

**The mutation surface is `pipeline_params` and NOTHING ELSE — structurally, not by request.**
Three things used to be offered and then forbidden in prose, which is the shape that produced a
generator obeying the prose and a validator failing it for obeying:

- `prompt_fields_override` / `task_context_override` write the OUTER searchpoint, whose render
  reaches the wire only through `prompt_node_names()[0]`. `datasets/promptpotter-self/pipeline.yaml`
  declares no `prompt_info` on any node — none of them takes a rendered target prompt — so there
  is no such node and `build_l1_response_schema` omits both slots. The LLM cannot emit a key the
  schema never declares, the same lock `model`/`provider` ride.
- `problem_description` / `answer_format` carry the injection slots and the output contract, so
  they are absent from every node's `optimizer.param_keys`. Same lock, one layer up.

What remains is four fields × four inner nodes, plus `layout` (information flow) and
`output_schema_field_names`. Their CURRENT text reaches the generator through `rendered_prompt`
(§ dispatch-hub.md), which is what makes "write a COMPLETE replacement" an informed instruction
rather than a blind one.

## 4. Outer composite fitness — cross-sample terms open

**The proxy vector and its law are `domain/l4/proxies.py` — the type IS the law, and this spec
does not restate it.** What each term means, what it is bounded by and why, and the
floor / exclude / measure trichotomy all live there.

What lives HERE is the composition, because that is `campaign.yaml`'s fact, not the type's — and
as of the first complete 39-cell panel there is barely a composition left. The formula in
`datasets/promptpotter-self/campaign.yaml::scoring` re-anchors **one** term:
`max(0.0, min(1.0, (mean_round_delta + 1.0) / 3.0))`.

**Why THIS term and not a basket** — the mean-vs-endpoint signal-to-noise decision, the four multipliers the first complete panel removed, and the bill-vs-incurred argument that keeps any cost term out — is owned by [`../concepts/optimizer-of-the-optimizer.md`](../concepts/optimizer-of-the-optimizer.md). What stays here is the composition, the open terms, and the live defect.

**The mean is taken over the round BUDGET**, holding the last adopted level forward across rounds the cell never ran (`domain/l4/proxies.py::held_levels`) — dividing by the rounds that happened would make the denominator a per-cell quantity, and since `inner_lives` stops a STALLING cycle, it would pay a cell for quitting once it had lifted. Near-inert on today's corpus (almost every banked cell used its full cap) and it binds the moment the brake starts firing, which is what a better optimizer prompt causes.

One thing this does NOT fix, and it is the open one:

- **The arms still differ less than their own intervals.** Read each arm's interval off `rank-optimizer-prompts` rather than re-deriving a ratio in prose — it is served, and a served number cannot drift from the disk it came from. `se ∝ 1/√n`, so the cell count needed to separate two arms is far below what a linear intuition suggests and buying cells is cheaper than it looks. Until they separate, the panel is a behavioural diagnostic, not a selector, and crowning a winner between near-identical arms is reading noise. Closing it needs more cells or **candidates that differ more than they currently do** — the cheaper lever, and the untried one.

**One estimator per subtraction.** Reading the two ends of one difference through different estimators — a joint `fit_rasch` anchor against `fit_theta_given_delta` round levels at the locked ruler — makes the shrinkage on the anchor move with the arm. That is a bias, which unlike noise does not average out over a panel. `_calibrate_delta_ruler` reads θ_C0 through the same conditional estimator, one expression for the cold and warm branches alike. The anchor *wander* on top of it was measured and is **not** worth buying out: a ruler shift moves θ_C0 and the round levels together, within-seed r = **+0.75**, leaving ~2% of the delta's variance for a ~3% spend increase.

**Read the shape, not only the scalar.** Every cell records a per-round `improved` verdict — a
within-round paired comparison against the matched origin on the same samples, so it touches
neither the θ anchor nor the re-drawn subset. A 6-cell panel carries ~24 of those against 6
scalars, which is why a *shape* defect is legible where a 0.077-logit contrast is not. Rendered
per cell by `runner/inner/spawn.py::_lift_shape`. The target shape is: most cells lift in round
1, about half again in round 2, the stragglers land in round 3, thinning as they saturate.
Measured on the current run's six cells: **r1 3/6 · r2 1/5 · r3 2/3 · r4 1/3** — rounds 3 and 4
behave; round 1 lifts only half the time when it should be the easiest round on the board, and
round 2 nearly flatlines. That is the live L4 defect, and it is an `l1_generate` problem.

**The re-anchoring window is `(x+1.0)/3.0`.** It clips nothing in the banked range and is linear, so the paired estimator's effect × 3.0 IS the mean logit lift. `improvement_threshold` moved with it — it lives on the fitness scale.

**Governing law: every term carries a candidate gradient** — terms without one stay out, which is why a declared target, a per-seed cost multiplier and a `normalized_gain` divisor all fail the bar. Nothing is held "emitted-but-out-of-formula" any more: the seven unused proxies are no longer emitted at all, because an emitted-and-unread field is a field that drifts from its `observation_mappings` declaration in silence.

**A term must also be blind to our measurement history** — owned by [`../concepts/optimizer-of-the-optimizer.md`](../concepts/optimizer-of-the-optimizer.md) § The bill is not the cost. What this layer must get right: no fitness term divides by either cost today, and any proposal to reintroduce one answers that section first.

Still to layer — the cross-sample terms (the P3 post-aggregate formula, above the
per-sample primitives):

- **Area-under-lift-vs-budget** — rewards climb-fast-then-plateau over crawl. Per-round
  spend is not materialized; reconstruct cumulative-spend-by-round from
  `TokenUsageRecord.round` on the cycle ledger (a dashboard per-round spend rollup is the
  durable follow-up, not required here).
- **Panel aggregation** `mean lift − λ·std` across the inner-task panel. **Critical:** the
  `std` is cross-seed **outcome dispersion**, NOT the θ estimation SE — penalizing `theta_se`
  resurrects the "wide posterior discards good candidates" pathology. Route through the P3
  post-aggregate formula, never the election rank key.
- **PoBB-decisive promotion** — at the outer level, keep topping up inner campaigns until
  the optimizer prompt config *ranking* is statistically decisive, built on
  `metrics.py::elimination_p_best` over inner-campaign arms.

The proxies read the inner loop's **θ-based**, grade-A improvement
([`fitness-comparability.md`](fitness-comparability.md)) — subset-invariant and
clean-measurement by inheritance, which is why comparability is the prerequisite.

## 5. Non-goals + validation

**Non-goals (this spec):** any webapp surface (CLI/headless only); competitor/publication
numbers; mutating the inner `checkin` (off the operator surface — deferred).

**Validation gate** (C3 exit, `roadmap.md`): `proxy_lift_corr ≥ 0.6` over ≥4 paired
branches — the empirical proof the cheap proxy predicts real lift. **Still open, and it is a
measurement to run, not a module to write.**

## Slice order

**§ Finish line is the single owner of what has shipped and what "done" means; this section holds only the ordering and the two facts it does not carry.** 3 gates real signal; 4 + 5 make the result trustworthy + shippable; 6 (the information-flow axis) is required for the distributable claim and sequences after 3.

**Slice 5 is distributable config + cost realism** — tune the committed `inner_tasks.yaml` + `campaign.json` (and the shipped optimizer provider) so `new promptpotter-self` completes at evaluator-tolerable cost, defaulting small. The cost shape (geometric; wall-clock dominated by optimizer-call tails) is documented operator-facing in `dataset.md` § Cost shape — keep it there, not here.

> **`spend_budget_usd` in `campaign.json` is the only honest cost bound — a *cap*, not an estimate.** This spec quotes no cost anchor; re-measure before quoting a price to anyone.

## 6. Per-node prompt layout — the searched information-flow axis

**`NODE_LAYOUTS` (`domain/l1_layout.py`) is the SoT — read the search space off the code,
not off this doc.** Which signals reach each inner optimizer prompt is a
searched axis, not a hand-tuned constant: every optimizer node owns a `NodeLayoutSpec`
(`editor` / `possible` / `mandatory` — the guard rail, never excised / `floor` — the good
default every normal campaign runs on), and one `DispatchHub.fill` serves every node. The L4
layout edit rides the existing `L1Variant.pipeline_params_override` slot for the three
`editor == "l4"` nodes (`l1_critique` / `l2_context` / `l3_plan`);
`resolve_node_layout` (`dispatch/llm_call/prompts.py`) partial-merges the edit onto the floor
and **rolls back to the floor on any hard failure** — creative within guard rails by
construction, and a rolled-back edit simply scores as no-improvement. Convergence is
self-correcting: a layout that starves a node loses on the same proxy as everything else, so
`mandatory` stays deliberately minimal.

Deliberately deferred: `l1_generate`'s L4 floor-edit (its layout is L2-edited in-campaign via
`opt_sp.memory.l1_layout`; an L4 edit needs the seed-the-inner-origin seam, and a fill-time
apply would clobber the inner L2's live edits — excluded from the schema graft so nothing is
silently dropped). Watch item: `prompt_block_catalogue` is `char_cap=None`. Under `restrict`
that is deliberate — the panel *is* the value space and a truncated one would reject blocks it
never offered — but it makes `restrict` the one uncapped blowup vector, and it grows with the
library.

The operator-facing layout matrix (generate it from `NODE_LAYOUTS` into one researcher doc —
don't fork the prose) and any webapp surface stay deferred with the L4 UI lane.
