# L4 outer loop + the shared in-process execution seam

> The real L4 recursion runs end-to-end (`new promptpotter-self` mints + runs real inner campaigns). Slices 3 + 4 + the distributable config remain; this doc is the **living finish-line plan** for them (see § Finish line below).
>
> ## ⚠️ EVERY MEASUREMENT BELOW IS VOID — DO NOT CITE ONE
>
> On **2026-07-10** all 169 dev campaigns and the measurement cache were deleted on purpose.
> Every origin score, noise band, CI, per-inner cost, wall-clock and self-optimizing campaign winner in
> this document predates that reset and is **void**. Re-measure before acting on any of them.
>
> The `justlogic` numbers are void **twice over**: `_step_llm_only` dropped `seed`, so justlogic
> was **never seed-deterministic** — its ≈0.44 origin and the noise band around it were never
> reproducible measurements at all. (Fixed since; proven at temp 1.0.)
>
> The *reasoning* below is kept because it is why the knobs are what they are. The *numbers* are
> kept only so a re-measurement has something to compare against. They are marked ~~struck~~ or
> **[VOID]** where they appear.
>
> **Scope:** CLI / headless only — no webapp surface yet (that's a later lane). The outer loop is a normal `python -m promptpotter new promptpotter-self` invocation.
>
> **Prerequisite:** [`fitness-comparability.md`](fitness-comparability.md) — the outer fitness reads inner-campaign improvement, which must be subset-invariant (θ-based).

## Finish line — a distributable `promptpotter-self` (drives the remaining work)

**The goal is no longer "make L4 run" (done) — it is "ship a `promptpotter-self` an operator can `new` and watch the optimizer improve its OWN optimizer prompts, at bounded cost, with spend visible."** Every remaining slice is judged against that. The AI agent owns L4 end-to-end and drives it autonomously (commit small green arcs); escalate to the operator only for genuine actions — real spend approval on a multi-campaign run, a provider/account change, or a compaction handoff. Definition of done:

1. **An inner benchmark the inner loop can still climb.** gsm8k is RETIRED — the inner model already aces it, so the loop has nothing to find and every outer candidate scores the identical composite. **Chosen: `justlogic-d234`** — JustLogic depths 2-4 (iid mix), a reasoning task where optimizer prompt quality plausibly moves the needle. `inner_tasks.yaml` points at it. (The operator's bet: the inner model's `Uncertain`-hedging under low effort is an ADDRESSABLE behaviour the loop can crack in the first few rounds, not a hard capability ceiling — so optimizer prompt quality has room to move the score. Earlier d6-7 / d23 / d234 cut-comparison + band measurements are VOID, from a data-deprecation-era bug — do not cite them.)

   > **There is no declared target, and no *expected* headroom — deliberately.** `target_score` and the `rounds_to_N` proxy are DELETED (2026-07-12). Declaring a target asserts up front how much room the benchmark has; that assumption reached no decision (the counter had no candidate gradient and the scoring formula already ignored it) and it was epistemically backwards. **A task the inner model looks bad at is a task it has not been TUNED for yet, not a task with a low ceiling** — gpt-oss-20b can be prompted a long way up on justlogic. The default posture is *optimistic*: assume the room is large unless the evidence is unambiguous. The lift core is the raw climb on the ability ruler and divides by nothing — not by a declared target, and (since 2026-07-13) not by an inferred "room" either.
   >
   > What this does NOT excuse: **improvement on justlogic is real but INFREQUENT.** A seed that fails to move under one optimizer prompt is weak evidence, not proof the seed is flat — do not read a quiet panel as "no headroom", and do not retire the benchmark on one run. The panel spans a *range* of seed difficulty rather than a wide count, because duplicated difficulty buys nothing — and it did NOT until 2026-08-02, when five of six seeds sat at 0.525-0.575 and the sixth held the low end only through seven impossible rows. Seeds are now chosen on measurement (`python -m promptpotter seed-screen`): a bank whose constant-answer floor EXCEEDS its origin is REJECTED — it pays a candidate for collapsing to one label — and the rest are ranked on `reasoning_margin` (origin − floor), never on accuracy, which conflates an easy bank with a large majority class. **The floor is exact and the origin is not**, so the verdict needs repeated passes (default 3) and is WITHHELD while the margin sits inside its own error bar; a single pass once condemned seed-5 on a margin its second read reversed. A thin POSITIVE margin disqualifies too — seed-4 left the panel at +0.021, one row in 40. Sizing: [`../concepts/optimizer-of-the-optimizer.md`](../concepts/optimizer-of-the-optimizer.md) § Sizing the panel.
2. **Outer mutations actually reach the inner optimizer prompts (slice 3). [SHIPPED `28f9c720`, 2026-07-01.]** With the standard `assets/optimizer/` outer prompts the outer L1 emits FLAT single-prompt field edits, which do **not** map to per-node `optimizer_prompt_overrides` — candidates would run identical inner cycles and the loop would optimize noise. The specialized outer prompt set that emits per-node `PromptTemplate` edits now exists and is wired end to end: `promptpotter/assets/optimizer/sets/self_optimizing.yaml`, selected by `campaign.yaml::optimization.optimizer_set: "self_optimizing"`, whose wire schema offers only the four inner nodes × their `PromptTemplate` fields → `connectors/promptpotter.py` → `runner/inner/cycle.py::set_optimizer_prompt_overrides` → `dispatch/llm_call/prompts.py`. **This is no longer the gating item** — it was still described as one here long after it landed, which is worth knowing when reading anything that cites this line.
3. **Spend is visible (slice 4 rollup). [SHIPPED.]** Each inner campaign's total rides its `CycleResult.spend` (read from the inner run's live dashboard state at finalize, never the debounced `dashboard.json`, which would race the read) and is returned as that outer sample's `step_tokens` — so it fans onto the outer ledger through the existing backend-cost channel (`sample_measurement`), keyed by the terminal node. The inner cost IS the outer sample's backend cost, so "spend is the headline" holds at the outer level without a second mechanism. `runner/inner/cycle.py::run_inner_cycle`. (Unmeasured or unpriced inner spend used to EXCLUDE the cell, because `delta_per_dollar` divided by it; with no cost term in the fitness that guard is gone and those cells are now measurable.)
4. **A bounded, cheap default config.** The committed `inner_tasks.yaml` + `campaign.json` must let `new promptpotter-self` complete at a cost an evaluator will tolerate. Cost is **geometric** (one outer round = n_variants × n_inner_tasks inner campaigns, each a full inner campaign) AND each inner optimizer call is slow (~176 s on openrouter/gpt-oss-120b). So: few inner tasks, few samples, few inner rounds, low outer `max_rounds`/`n_variants` by default — and consider pinning the inner+outer optimizer to a faster provider (groq) in the shipped config. Document the cost shape for the operator. The **stall brake** is what keeps the geometry honest: `inner_lives` (+1 per improving round, −1 per stall, stop at 0 → `LIVES_EXHAUSTED`) ends a stalling inner campaign early, so a dead optimizer prompt is cheap and only a compounding one buys depth. **INVARIANT: `lives.start` must sit well below `max_inner_rounds`.** Set near it, the bank cannot drain before the calendar cap — every inner then runs full-length regardless of quality, and that also removes the geometry's only brake, since an optimizer prompt that finds nothing then burns the same rounds as one that compounds (a term with no candidate gradient earns nothing — the governing law is the type, `domain/l4/proxies.py::OuterSampleProxies`; `inner_tasks.yaml` is a typed declaration now, not a place to write prose). **The brake is only free because the measurand divides by the BUDGET**: `mean_round_delta` holds the last adopted level forward over the rounds a stopped cell never ran (`held_levels`), so ending early saves the money without moving the score. Dividing by the rounds that ran instead — which it did until 2026-08-02 — paid a cell for quitting once it had lifted, i.e. the brake bought its own good grade. How the panel and the round cap are sized, and how to re-derive them: [`../concepts/optimizer-of-the-optimizer.md`](../concepts/optimizer-of-the-optimizer.md) § Sizing the panel.
5. **A run that demonstrably improves.** The validation gate (`proxy_lift_corr ≥ 0.6` over ≥4 paired branches) PLUS at least one real `new promptpotter-self` whose outer best beats its outer origin — the proof the cheap proxy predicts real lift and the loop climbs. **Gated on item 7, and this is the ordering that matters:** "outer best beats outer origin" is satisfied by noise at the current resolving power (6 cells, MDE ~1.7× the typical arm contrast), so a run that clears it proves nothing until the panel can tell an arm from a re-read of itself. Read `n_cells_to_verdict` first; treat this item as unmeasurable while it says `UNKNOWN`.
6. **The loop owns its own information flow (§6). [MECHANISM BUILT — Arcs 1+2+3. Open: the validating data run.]** A distributable `promptpotter-self` must let the optimizer improve *how its optimizer prompts are built*, not only their prose. Which signals each inner node sees (the per-node injection set) is the higher-value, dataset-agnostic axis. It **is now a searched axis** with a mandatory guard-rail floor: every optimizer node owns a `NodeLayoutSpec` in `NODE_LAYOUTS` (`domain/l1_layout.py`), and the L4 layout edit is wired for the three `editor == "l4"` nodes. What remains is **validation, not construction** — a full-signal run showing outer candidates that differ by inner-node *layout* (not only prose), with the winner's layout captured. Sequenced alongside 3–5 on the same run.
7. **The outer SNR instrument — the scientific gate that makes this whole phase falsifiable, and the trigger for when to trust the loop.** The finish-line goal — *tuning and searching a very good base configuration for the L1/L2/L3 loop, driven by the outer loop* — is only meaningful once the outer signal exceeds its own measurement noise. Below that line the outer loop is **noise-blind** and its verdicts are coin-flips: this is why `120B`-vs-`v4-flash` was unreadable, and why round-1-then-plateau makes every optimizer prompt look identical. So a **recurring power analysis** (a *gauge R&R* on the outer gradient) is a first-class deliverable, not a diagnostic afterthought:
   - **Two series, not one ratio.** Same optimizer prompt run K times → *within*-variance (noise σ); a few *different* optimizer prompts run once each → *between*-variance (signal, effect-size d). Log both over time — which one is stuck says which lever to pull: the instrument (noise) or mutation diversity (signal).
   - **The pivot is a sample count, not a yes/no.** At current (d, σ) a verdict needs ≈ `(2.8·σ/d)²` outer runs. Pivot to *loop-driven* tuning the moment that N drops inside the run budget. Until then, drive with **operator-controlled levers** — variance reduction (more seeds/samples, or a low-variance *one-step / fixed-inner* proxy in place of the high-variance full-trajectory measurement) and deliberately diverse hand-authored optimizer prompts — because a noise-blind loop cannot grow the signal by itself (the chicken-and-egg: the loop can't select what it can't distinguish).
   - **BUILT 2026-08-02 — as a derivation, not a re-run.** It was planned as a wrapper around `noise-floor` (buy K re-runs, measure the spread); it did not need to be. Both series were already on disk: `rank_optimizer_prompts` walks every pp-self round file and already accumulates, per optimizer-prompt state, a list of readings per cell. Repeat readings of one (state, cell) ARE the within series; the spread of `anchor_effect` across states IS the between series. `OuterSnr` (`application/optimizer_prompt_ranking.py`) pools them and serves `within_sd` / `between_sd` / `n_cells_to_verdict` on every read, **zero spend, zero new walk** — `python -m promptpotter rank-optimizer-prompts` and `GET /optimizer-prompt-ranking`. Its per-round peer `OuterVariance` (on every `outer_verdict`) splits ONE round's spread into estimation noise vs real between-cell difference, off the per-cell `theta_se` now carried end-to-end.
   - **First reading: `UNKNOWN`, and that is the finding.** Across 3 campaigns and 6 states, **no optimizer-prompt state has ever been measured twice on the same cell** — the within series is empty and the ratio has no denominator. So every "1.9× / 1.7×" figure quoted below is the *panel's own SE*, not re-run noise; the two are different quantities and one cannot stand in for the other. **The unblock is not a mechanism — it is running one arm twice.** Do that before reading any ranking here as a result. Still open on top of it: the appended on-disk series / control chart (the regression guard that catches an instrument getting *worse*), which needs more than one reading to exist.

## Running & supervising a live `promptpotter-self`

**The infrastructure is done; the optimizer *application* is not.** The loop, seams, recursion, and scoring gateway all exist and are green. What remains is making the optimizer *behave well*, and that is found empirically: **run `new promptpotter-self` on `justlogic-d234`, collect the data, read what the loop actually produced, fix the bug at its ROOT, re-run.** Expect several restarts; this is the loop, not a failure. **Read the run's resolving power before its result** (item 7) — a panel that cannot separate arms still prints a leader, and reading that leader as a finding is the failure mode this whole phase is exposed to. Most roots in this phase turn out to be prompts rather than code — that is where the causes have been, not a rule that the fix must be small.

**The cadence must be SELF-FIRING, not event-driven.** A supervising agent schedules its own
wake-ups (the harness's ScheduleWakeup / self-paced loop, ~150–270 s) the moment a run starts,
and each wake IS a researcher pass over the reading list below. A passive log Monitor does NOT
count as supervision — it only fires on patterns you predicted, and every real bug so far
(estimator inconsistency, evidence starvation, proxy annihilation) was found by reading the
run's own measurement files, not by a grep hit. Monitor stays as a supplementary alarm only.
Role split: the operator is the developer/user (UX); the agent owns everything else.

**Supervise every live run actively — never fire-and-wait.** While a run (or any long optimization) is in flight, poll its output at least **every 2 minutes** looking for a newly-surfacing bug — read the fresh dashboards/measurements/goldmine log, not just the exit code. **The 2-minute window is for fanning out and researching — not for pausing.** Spend each interval *actively investigating*: fan out parallel searches over the fresh dashboards/measurements/goldmine log, chase the newest anomaly, read what the loop just produced. The cadence is **not** "check once, then wait" — it is "keep investigating and researching, up to 2 minutes, then check again." An idle wait between ticks is the wasted-run failure mode this guards against. A background run you set and forgot is a wasted run; the bugs show themselves *while it runs*, and catching one early lets you kill-fix-restart before the whole spend drains.

**Run `new promptpotter-self` ONCE, in the FOREGROUND, to completion — never as a killable background task.** A harness/OS kill orphans whatever inner `justlogic` run is open; the reaper stamps it `producer_vanished` (a `FAILED` outcome — correctly excluded from scoring, so the headline is *not* corrupted, but NOT cached). The next relaunch therefore re-runs that seed, burning spend and littering the tree with dead cycles (an overnight run measured seed-1 three times — one success, two vanished — while cached seeds sat idle). And because the outer `cycle_id` is deterministic (hash of optimizer prompts + benchmark), every re-`new` with unchanged optimizer prompts collides on ONE `cycle_id` + ONE `.inner/` sandbox — the lineage tree then renders the *same* inner seed-runs under N campaign headers, so "N campaigns, identical stats" is one measurement shown N times, not N runs. To iterate a live run, `resume` it; only `new` again after an optimizer prompt/config change (which mints a fresh `cycle_id`).

**Default the fix to the prompts** (`promptpotter/assets/optimizer/` — `pipeline.yaml::resolved_prompts` for the inner set, `sets/self_optimizing.yaml` for the outer one: wording, evidence framing, the per-node edit schema). Reach past prompts to a code fix ONLY when the data shows a structural cause — broken information flow (a signal the prompt needs never reaches it), a missing analysis (evidence the loop should compute but doesn't), or a wiring gap. Name that structural cause before touching code; do not add new infrastructure to paper over a prompt problem.

### THE PER-CHECKUP READING LIST — every 2-minute tick reads ALL of these, not just the log tail

**A checkup that only greps the goldmine tail is NOT a checkup.** Each tick, open the newest
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
an outer sample returning in ~0.0s (stale-cache reuse — identity bug); off-enum grounding fields;
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
- **`replicate_survivors` stays 0 in the distributable** (opt-in dev-stage successive-halving
  replication on `OptimizationConfig`). It complements CRN, not substitutes: CRN (a per-cell
  inner LLM seed shared by origin + every variant, `runner/inner/cycle.py`) cancels
  *common-input* noise in the paired diff; replication averages out the idiosyncratic
  single-run draw on the *diverging* inner-prompt path, replicating the ORIGIN reference too
  (its extra draws thread only into the decision estimators, the base draw stays the display
  floor). Coverage counts distinct cells, so replicates never falsely satisfy the floor.
- The rounds-to-target counter and the per-seed cost multiplier are retired (no candidate
  gradient — see §4's governing law). The counter is now **deleted outright**, and with it
  `target_score`: a declared target is an assumed ceiling, and we assume the room is large
  instead. See § *No declared headroom* in `docs/concepts/optimizer-of-the-optimizer.md`.

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
  per-round verdict (`domain/l4/verdict.py::compute_outer_verdict`) pairs the round's
  variant against the **cached round-0 origin**.
- **Candidate-arm inner round-0 is NOT re-measured** — the tenant-global `measurements/`
  store under the cycle's `.inner/<key>/` sandbox + content-addressed reuse replays the
  origin-arm's rows into candidate arms by construction; a "share the origin across arms"
  fix is unnecessary.

## Live-run learnings — bake these in, don't re-discover

- **Inner sandbox is a FLAT registry, not physical nesting.** Inner campaigns live at `<workspace>/.inner/<key>/` (sibling of `projects/`), where the key identifies the owning `(tenant, campaign, cycle)` — NOT nested under the deep outer cycle dir — physical nesting blows Windows' 260-char `MAX_PATH` at depth 1; a flat registry named-by-but-not-under the spawning cycle stays shallow at every depth, so the re-entrancy invariant holds. (`runner/inner/cycle.py::InnerSpawnContext`.)
- **`in_process` connectors must NOT fetch a remote pipeline schema.** `init_services` skips the backend `GET /pipeline` for `in_process` execution and uses the local `pipeline.yaml` alone — otherwise it merges an unrelated backend's nodes under the dataset overlay.
- **L4 datasets carry their "samples" on disk, not as a CSV.** The outer "samples" ARE the inner tasks; `Connector.experiment_file` (`"inner_tasks.yaml"`) loads them through `extract_experiment` at bootstrap.
- **An inner failure must degrade, not propagate.** `run_inner_cycle` catches any inner exception and returns a zero-improvement proxy, so a bad outer candidate scores poorly instead of killing the outer cycle. Keep the fallback — but a *clean* origin-gate halt should return real proxies, not the exception sentinel; verify on the justlogic run.
- **Inner mints must NOT clobber the outer's active-pointer.** Every mint site threads `projects_root=session.store.projects_root` so a sandboxed inner mint stamps its own workspace, never the real tenant `active_session.json`. Same latent class in `sweep_runner.py` + `fork_siblings.py` (not hit by the inner loop; left).
- **The outer prompts must be TIGHT — gpt-oss-120b returns empty content on a verbose optimizer prompt** (`raw_chars: 0`, `l1_provider_empty_response`). Keep `promptpotter/assets/optimizer/sets/self_optimizing.yaml` terse; never fix this by touching the global `assets/optimizer/pipeline.yaml` node config (shared with every inner cycle).
- **The θ election needs graded outcomes, a scored origin, and ≥6 inner tasks.** `Observation.response` carries the graded per-sample fitness (the logistic MAP is valid ∀ y∈[0,1] — bit-identical for binary datasets); the origin is scored in every live run (no shape guess — the round-0 origin gate catches a genuinely-unscoreable origin LOUD); and offline replay shows the LCB election needs **≥6 inner tasks** to crown (at 2, θ_se exceeds the point-lift and it correctly refuses to crown on noise) — matching the composite-fitness panel goal (§4).
- **A zero-candidate round heals immediately.** `l1_zero_candidates` is folded into the existing `l1_generate_unusable` structural-breach rule (one widened predicate, `escalation_rules` stays 6) and fires L2 straight away instead of burning `l1_patience` dead rounds; the empty call is itemized in spend. Still measure on the supervised run how often the empty-content path fires and whether the L2 reframe recovers it.
- **The outer cycle heartbeats its OWN ledger while awaiting each inner run** ("inner rX/Y · best Z%") — `dispatch/llm_call/heartbeat.py` + `runner/inner/cycle.py`; the chat maps the heartbeat to one upserted progress chip. Without it a healthy outer round looks dead for the whole multi-minute inner campaign.
- **An L4 outer sample must stamp `terminated_at` = the LAST outer node (`l3_plan`), never a mid-chain one.** `terminated_at` is the archive's reuse contract; an inner campaign consumes the ENTIRE outer config at once, so a mid-chain stamp lets prefix-trust replay serve the ORIGIN's rows to any candidate editing a later node (fake 0.0s replays). Consequence: **every pre-fix layout-axis result was never honestly measured** — re-validate on a post-fix run.
- **A NO-OP probe's save REPLACES the origin's archive slot — by design, don't re-diagnose.** `MeasurementArchive.append_run` dedups on `content_hash` (newest wins); reuse stays correct, forensic origin rows live on in `round_0000.json` + the ledger, and index entries whose detail log was replaced dangle harmlessly.
- **Hang triage order: ledger tail → `dashboard.json::run_phase` → `.runtime/` flags → process table by command line → only then mtimes.** Control flags (`pause.flag`) are consumed at the next per-SAMPLE checkpoint — a mid-candidate pause stops within seconds and looks like a freeze to an mtime-watcher. The optimizer-call path already has a hard wall-clock (`_chat_under_deadline` → `OPTIMIZER_TIMEOUT`); overnight deaths with no terminal record are machine-sleep/session-end class, not code.
- **`token_budget` stays `null` for L4** (`datasets/promptpotter-self/campaign.yaml`) — the inner-spend rollup lands each inner campaign's tokens on the outer ledger as backend cost, so the normal-campaign default trips after a couple of inner campaigns while the USD budget sits nearly untouched. For L4 `spend_budget_usd` is the meaningful cap. (Root is L4's scale, not the rollup — the rollup correctly reports real tokens; don't uncount them.)
- **When the inner loop "stalls", suspect the SCHEMA before the prompt.** The inner `l1_generate` was emitting variants that mutated nothing on ~10% of calls, which cascaded (diversity 0.5 → cleanliness 0.0 → lives drain → 3-round stall → negative inner lift) and read as a bad optimizer prompt. It was neither the prompt nor the model: `L1Variant.required` was `[variant_name, changes_description]` — the schema **mandated the narration and marked all three override slots optional**, so a variant that named itself, cited a panel, described a change and mutated nothing was exactly what it asked for. The payload now generates BEFORE the prose that reports it, and at least one override is enforced at parse. Fixed `417e027e`; validated live (zero empty variants, zero repair re-asks). Consequence: **every inner-loop number taken before it is contaminated** by a defect that had nothing to do with the optimizer prompt under test.
- **Never hand an LLM a character budget — it cannot count characters.** `l3_plan` and `l2_context` were both told their cap explicitly, *with the consequence spelled out*, and both overran on essentially every round (66 × `plan`, 87 × `task_context`); L3's plan lost its back five sections to the truncation rail, silently, from the frame every downstream prompt reads. A char cap is a **runaway rail, not a budget knob** — express the budget in a unit the writer can honour (bullets, sentences) and let the cap catch only a genuine runaway. Fixed `21d6e289`.
- **An injection reaches a prompt through TWO channels, so "the prompt never says X" needs both checked.** A signal renders either from the node's `NODE_LAYOUTS` floor into an addressable slot, or from a `{{token}}` left in prose — and `fill` resolves both. `rebase_capability`/`terminate_capability` had been moved to the layout channel, but the `l2_context` `instruction` kept its two tokens, so **every L2 prompt stated both directives twice — ~1.5k of ~10.9k rendered chars**, verified on the banked ledgers (`.payload.view.l2_prompt`, 12 fires, 2 occurrences each). `dispatch-hub.md` had asserted the correct design ("no prose `{{token}}` carries them") the whole time. Tokens deleted; the layout floor is the only channel, and it is `mandatory` on both `l2_context` and `l3_plan`, so no prose rewrite or L4 layout edit can drop them.
- **The L3 rung has never fired — 0 times across all 14 banked ledgers** (121 `l1_generate` fires, 50 `l2_context`, 0 `l3_plan`), under `l2_patience: 2` / `l3_patience: 1` against `max_rounds: 2` outer and `max_inner_rounds: 4` inner. Recorded so nobody reads the top rung as exercised: every L3-side claim in this repo is design intent, not measurement. Deliberately NOT acted on — re-tuning patience changes what the engine decides and voids banked origins through `_identity_config`.
- **A stale prompt file is a live parse failure, not just documentation drift.** `promptpotter/assets/optimizer/sets/self_optimizing.yaml` (the OUTER L4 prompt set, selected by `optimizer_set: "self_optimizing"`) declared `variant_name` REQUIRED. `L1Variant` is `extra="forbid"`, so the moment that field was deleted from the model, every outer variant would have failed validation. **The Pydantic model, both `answer_format`s, and `resolved_schemas` move in ONE commit or the loop stops parsing.**

## 1. The shared in-process execution seam — SHIPPED

`Connector.execution == "in_process"` dispatches `run_query` to a connector-supplied
`Connector.in_process_run(query, payload)` returning the result shape the scorer already
consumes; the HTTP arm is unchanged, and dispatch stays on the declared mode, never the
connector name (`connectors/CLAUDE.md`). One connector rides the seam: **`promptpotter`**
(delegates to the inner-cycle runner, §2).

**Feature A (the no-server `llm_only` connector) is WITHDRAWN, not deferred.** It shipped,
then sat with **zero** dataset adopters for its whole life — the six single-node benchmarks
name an `llm_only` *node* inside a `termnorm` pipeline, which still needs the server. Its
in-process answer extraction duplicated what TermNorm's `_step_llm_only` already does over
the wire, and `llm_only.py` itself warned the two arms "must agree on shape … or one
measures a different thing than the other" — a standing divergence risk bought for a case
nobody ran. Deleted (−267 LOC). The single-node case is served by the **TermNorm connector
accepting an `llm_only` pipeline**; `llm_only` is now a node name only. Re-adding a
no-server connector needs a dataset that actually declares it, not a spec claim.

## 2. In-process recursion isolation — SHIPPED; keep it depth-agnostic

`run_inner_cycle` (`application/runner/inner/cycle.py`) mints + runs each inner campaign
via `run_optimization` in its **own `asyncio.Task`** (fresh per-task ContextVar copies:
`_CYCLE_LEDGER` / `_CURRENT_ROUND` / `_ABORT_CHECK`) under **sandboxed stores** at the flat
`<workspace>/.inner/<key>/` registry. The re-entrancy invariant that makes L5+
come free: the sandbox is named by *this* cycle (never a global path or a baked-in
outer-vs-inner split) and the fresh-task spawn happens at *every* level — never assume
depth 1. The real recursion ceiling is **economic and statistical, not architectural**
(geometric cost; `proxy_lift_corr` decays with depth), which is the right place for the
limit to live. Known forward item: the process-global rate limiter is shared, so inner spend
competes with outer for TPM/RPM. Execution home is CLI/headless (`new`/`resume`); the
read-only uvicorn app *observes* outer + inner cycles via the file tree — no second
optimizer process, no HTTP self-call.

## 3. Specialized outer optimizer prompt set — SHIPPED (lighter than specced)

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

## 4. Outer composite fitness — per-sample core SHIPPED; cross-sample terms open

**The proxy vector and its law are `domain/l4/proxies.py` — the type IS the law, and this spec
does not restate it.** What each term means, what it is bounded by and why, and the
floor / exclude / measure trichotomy all live there; they used to be restated here, in the
concept doc, and in an 8k-char JSON blob, and the four copies drifted.

What lives HERE is the composition, because that is `campaign.yaml`'s fact, not the type's — and
as of the first complete 39-cell panel there is barely a composition left. The formula in
`datasets/promptpotter-self/campaign.yaml::scoring` re-anchors **one** term:
`max(0.0, min(1.0, (mean_round_delta + 1.0) / 3.0))`.

**The term reads the MEAN of the adopted series, not its last step — and that is a
signal-to-noise decision taken on measurement.** On the same 39 cells: endpoint arm SD 0.077 /
residual 0.182; mean 0.064 / **0.134**, agreeing on the arm effects at r = +0.941. A 26%
quieter instrument at identical spend. **The mean is taken over the round BUDGET**, holding the
last adopted level forward across rounds the cell never ran (`domain/l4/proxies.py::held_levels`)
— dividing by the rounds that happened made the denominator a per-cell quantity, and `inner_lives`
stops a STALLING cycle, so it paid a cell for quitting once it had lifted. Near-inert on today's
corpus (almost every banked cell used its full cap) and it binds the moment the brake starts
firing, which is what a better optimizer prompt causes.

One thing this does NOT fix, and it is the open one:

- **The panel still cannot resolve one optimizer prompt from another.** At 6 cells the SE is
  0.055 against a typical arm contrast of ~0.09 — the smallest difference it can call is 1.7×
  larger than a difference between arms, down from 1.9×. Closing that needs more cells or
  **candidates that differ more than they currently do**, which is the cheaper lever and the
  untried one. Until then the panel is a behavioural diagnostic, not a selector; crowning a
  winner between near-identical arms here is reading noise.
  - **Two caveats, both worth knowing before this ratio is quoted again.** (1) The cell count
    was stated here as "~35 (wall-clock we do not have)", and that does not follow from the
    spec's own arithmetic: `se ∝ 1/√n`, so reaching 1.0 needs `6 × 1.7² ≈ 18` cells — which is
    also what item 7's `(2.8·σ/d)²` gives. Two sections stated two answers, and the larger one
    was the stated reason for not buying cells; at 18 that is a different conversation.
    (2) The 1.7 mixes corpora — the 0.09 contrast comes off the 39-cell `af6252`, the 0.055 SE
    off the current 6-cell panel — and its σ is the **panel's own SE, not re-run noise**, which
    nothing on disk has ever measured (item 7's first reading is `UNKNOWN` for exactly that
    reason). Read `n_cells_to_verdict` off `rank-optimizer-prompts` instead of re-deriving it in
    prose; it is served now, and a served number cannot drift from the disk it came from.

**RESOLVED, and the earlier entry here overstated it — the anchor wander does not survive the
subtraction.** θ_C0 is re-read on a ruler each cycle fits from its own candidates, and this spec
recorded seed-2's origin ranging −0.06 to +1.14 off ONE cached measurement, quoting "wander 0.588
against a 0.209 arm spread" and proposing a ~12-call-per-cell re-measure. Two corrections, both
from the 50 banked cells. **The 0.588 was seed-2's own sd (0.546) read as the panel's; the panel
mean is 0.234.** And a ruler shift moves θ_C0 and the round levels TOGETHER, so it cancels in
their difference: within-seed r(origin, ended) = **+0.75, slope +0.87**, leaving ~2% of the
delta's variance. **The re-measure is not worth buying** — it is a ~3% spend increase for ~2% of
the noise, and that is a measurement, not a refusal. What WAS worth fixing was free: θ_C0 came off
the JOINT `fit_rasch` (mean(θ)=0 re-anchored, σ_θ estimated by EB from whichever arms were in the
pool) while every round level is a `fit_theta_given_delta` MAP at the locked ruler. Two estimators
for two ends of one subtraction, and the shrinkage on the anchor moved with the arm — a bias, which
unlike noise does not average out over a panel. `_calibrate_delta_ruler` now reads θ_C0 through the
same conditional estimator, one expression for the cold and warm branches alike.

**Read the shape, not only the scalar.** Every cell records a per-round `improved` verdict — a
within-round paired comparison against the matched origin on the same samples, so it touches
neither the θ anchor nor the re-drawn subset. A 6-cell panel carries ~24 of those against 6
scalars, which is why a *shape* defect is legible where a 0.077-logit contrast is not. Rendered
per cell by `runner/inner/cycle.py::_lift_shape`. The target shape is: most cells lift in round
1, about half again in round 2, the stragglers land in round 3, thinning as they saturate.
Measured on the current run's six cells: **r1 3/6 · r2 1/5 · r3 2/3 · r4 1/3** — rounds 3 and 4
behave; round 1 lifts only half the time when it should be the easiest round on the board, and
round 2 nearly flatlines. That is the live L4 defect, and it is an `l1_generate` problem.

**Why the lift term reads the ADOPTED incumbent and not the round's proposals.** It averaged
proposals until 2026-07-28, and that made the metric anti-correlated with inner success: a round's
value to the search is what it *crowns*, so averaging in the arms it discarded prices exploration
as damage. Measured on `promptpotter-self__d8b5be`, both cells of the SAME optimizer prompt: the inner
campaign that climbed 52.5%→82.1% (θ +0.85) scored **0.069** while one that climbed 52.5%→57.1%
(θ +0.46) scored **0.199** — 2.9× higher for a quarter of the gain, because the first explored
harder.

**RESOLVED — `cleanliness` WAS measuring the seed.** This spec left that open, charging the term
"pending a run with a real optimizer prompt contrast". `promptpotter-self__af6252` is that run, and the
variance decomposition over its 39 cells is decisive: `cleanliness` puts **30.9%** of its variance
between SEEDS and **15.4%** between ARMS — twice as much signal about which data a cell drew as
about which optimizer prompt ran it. It is out of the formula.

The other three factors went with it, each for its own measured reason: `diversity_health` never
left the top fifth of its range (no candidate gradient at all); `delta_per_dollar` correlated
**0.958** with the lift core and flipped no ordering, making it the lift counted twice; and
`rounds_improved_frac` flipped nothing. All four were *multipliers*, so each held authority over
an ordering it could not justify, and together they compressed the fitness scale enough to roughly
double the apparent significance of the run's own conclusion (pooled paired t −4.62 vs the raw
term's −2.38, identical data).

For comparison, the re-anchoring window widened from `(x+0.5)/1.5` to `(x+1.0)/3.0`. The old one
clipped the best cell ever measured (+1.405 logits) and held zero cells in its lower third; the new
one clips nothing in the banked range and is linear, so the paired estimator's effect × 3.0 IS the
mean logit lift. `improvement_threshold` moved with it — it lives on the fitness scale.

**The quality EVENTS still act, structurally rather than twice.** An all-empty-response cycle is
FLOORED (`floor_reason`); a collapsed arm is dropped from the inner election and eliminated at
PoBB. Charging them a second time inside the fitness was a second mechanism for a job the loop
already does.

**Governing law: every term carries a candidate gradient** — terms without one stay out. That law
is what removed the four factors above, along with the earlier rounds-to-target counter, the
per-seed cost multiplier, and the `normalized_gain` divisor. Nothing is held
"emitted-but-out-of-formula" any more: the seven unused proxies are no longer emitted at all,
because an emitted-and-unread field is a field that drifts from its `observation_mappings`
declaration in silence.

**A term must also be blind to our measurement history, and one was not.** The measurement and
optimizer-call caches are tenant-global and content-addressed, so an inner cycle we have run
before *replays*: identical work, identical trajectory, nothing billed. That is not spread evenly
across the arms. The **origin** arm is the one that replays — a variant optimizer prompt writes
different prompts, so its every content hash is new and it pays full freight — so any term
denominated in the **bill** rewards the incumbent for cells we happen to have measured already.
It is a bias toward the origin, not noise, and it is invisible: the cell either scores as
absurdly efficient or, at the limit, records `$0.00` and is excluded outright. Live on
2026-07-13: seed-0 ran three inner rounds in four seconds and dropped out of the panel.

The resolution is that **cost and the bill are two different quantities**, and only one of them is
a property of the candidate:

- **The bill** (`CycleSpend.cost_usd`, `dashboard.json::spend.*.used_usd`) — money that left the
  account. Cache hits contribute nothing. This is the headline and what `spend_budget_usd` gates,
  and it must stay that way: billing a replay would halt a run that cost nothing.
- **The incurred cost** (`CycleSpend.incurred_usd`) — what the search would cost against a cold
  cache, with cache hits priced from the tokens they recorded. This is what a *measurement* of a
  candidate would have to divide by. On a cold cache the two are identical, which is why the gap
  stayed hidden.

**No fitness term divides by either one today** — `delta_per_dollar` is out of the formula and out
of the emitted vector. Both costs are still tracked and reported, because they answer different
operator questions, and this law is kept as the standing answer to any proposal to reintroduce a
cost term: it would have to be blind to our measurement history, and the bill is not. The same
argument already ruled out a per-second twin — wall-clock cannot be recovered the way cost can, so
it measures the cache rather than the optimizer prompt.

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

## Implementation order

1. **Shared `in_process` seam — SHIPPED** (§1). The `llm_only` connector it also yielded
   is **withdrawn/deleted** (zero adopters; see §1).
2. **`promptpotter` inner-cycle runner + isolation — SHIPPED & live-validated** (§2).
3. **[GATING] Inner benchmark with headroom + specialized outer prompt set — MECHANISM SHIPPED; full-signal data run open.** `inner_tasks.yaml` → `justlogic-d234` (the origin headroom is **[VOID]/unmeasured**, finish-line item 1) + the specialized outer prompt set (§3), live-validated to emit per-node edits of the INNER optimizer prompts. **Done when:** a real `new promptpotter-self` shows outer candidates with DIFFERENT proxies and outer best > outer origin.
4. **Enriched outer fitness + inner-spend rollup — rollup, per-sample composed fitness, and delta-led display SHIPPED; cross-sample terms remain** (§4). Each inner cycle's spend returns as the outer sample's `step_tokens`, fanning onto the outer ledger via the existing backend-cost channel. **Done when:** `proxy_lift_corr ≥ 0.6` over ≥4 paired branches.
5. **Distributable config + cost realism.** Tune the committed `inner_tasks.yaml` + `campaign.json` (and the shipped optimizer provider) so `new promptpotter-self` completes at evaluator-tolerable cost. The cost shape (geometric; wall-clock dominated by optimizer-call tails) is documented operator-facing in `dataset.md` § Cost shape — keep it there, not here. Default small; consider pinning groq.

   > **[VOID] Every cost anchor this item used to quote is unusable** — measured on an inner ladder that no longer exists and on deleted campaigns. **Re-measure before quoting a price to anyone.** Until then, `spend_budget_usd` in `campaign.json` is the only honest bound: it is a *cap*, not an estimate.

   **Done when:** a fresh clone can `new promptpotter-self` and watch self-improvement at bounded, disclosed cost.
6. **Per-node prompt layout — BUILT (Arcs 1+2+3); full-signal data run pending** (§6). **Done when:** the run shows outer candidates that differ by inner-node *layout* (not only prose) and the winner's layout is captured in `winner_pipeline_params` — validate alongside slices 3–5 on the same run.

Slices 1 + 2 shipped. 3 is the gating slice (no real signal without it); 4 + 5 make the result trustworthy + shippable; 6 (the information-flow axis) is required for the distributable claim and sequences after 3. The agent drives 3→6 autonomously, escalating only for real spend approval / provider change / compaction.

## 6. Per-node prompt layout — the searched information-flow axis

**BUILT (Arcs 1+2+3); `NODE_LAYOUTS` (`domain/l1_layout.py`) is the SoT — read the search
space off the code, not off this doc.** Which signals reach each inner optimizer prompt is a
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

**Open: the validating data run** (slice 6). The operator-facing layout matrix (generate it
from `NODE_LAYOUTS` into one researcher doc — don't fork the prose) and any webapp surface
stay deferred with the L4 UI lane.
