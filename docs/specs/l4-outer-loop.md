# L4 outer loop + the shared in-process execution seam

> The real L4 recursion runs end-to-end (`new promptpotter-self` mints + runs real inner campaigns). Slices 3 + 4 + the distributable config remain; this doc is the **living finish-line plan** for them (see § Finish line below).
>
> ## ⚠️ EVERY MEASUREMENT BELOW IS VOID — DO NOT CITE ONE
>
> On **2026-07-10** all 169 dev campaigns and the measurement cache were deleted on purpose.
> Every origin score, noise band, CI, per-inner cost, wall-clock and meta-campaign winner in
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

**The goal is no longer "make L4 run" (done) — it is "ship a `promptpotter-self` an operator can `new` and watch the optimizer improve its OWN meta-prompts, at bounded cost, with spend visible."** Every remaining slice is judged against that. The AI agent owns L4 end-to-end and drives it autonomously (commit small green arcs); escalate to the operator only for genuine actions — real spend approval on a multi-campaign run, a provider/account change, or a compaction handoff. Definition of done:

1. **An inner benchmark with real headroom.** gsm8k is RETIRED as the inner benchmark — its origin already aces the target, so the inner loop has nothing to climb and every outer candidate scores the identical "already-at-target" composite. The inner benchmark MUST have **origin clearly below target** (room for the inner loop to improve, so the proxies vary across outer candidates). **Chosen: `justlogic` at high depth** — a reasoning task where meta-prompt quality plausibly moves the needle. `inner_tasks.json` points at it (`justlogic-d67`; the numeric target lives only in that file — the goal is to improve, not to hit a number).

   > **[VOID] The headroom is UNMEASURED.** The ~~origin ≈ 0.44~~ that justified this choice came from the pre-reset meta-campaign AND from a run where justlogic was not seed-deterministic. **Re-measure the origin before trusting that headroom exists** — this is finish-line item 1's actual open work. `noise-floor --k 3` is the cheap way to re-establish it. (Numeric targets/origins are config and measurement values — dataset prose no longer quotes them.)
2. **Outer mutations actually reach the inner meta-prompts (slice 3).** With the standard `_optimizer/` outer prompts, the outer L1 emits FLAT single-prompt field edits, which do **not** map to per-node `meta_prompt_overrides` — so candidates run identical inner cycles and the loop optimizes noise. Slice 3 (the specialized outer prompt set emitting per-node `PromptTemplate` edits) is therefore **REQUIRED for any signal**, not optional polish. This is the gating slice.
3. **Spend is visible (slice 4 rollup).** Inner LLM cost currently lands in the sandbox ledger, invisible to the outer dashboard. For a distributable product "spend is the headline" — the inner-campaign cost MUST roll up to the outer cycle's spend. Build this with slice 4's fitness work.
4. **A bounded, cheap default config.** The committed `inner_tasks.json` + `campaign.json` must let `new promptpotter-self` complete at a cost an evaluator will tolerate. Cost is **geometric** (one outer round = n_variants × n_inner_tasks inner campaigns, each a full inner campaign) AND each inner optimizer call is slow (~176 s on openrouter/gpt-oss-120b). So: few inner tasks, few samples, few inner rounds, low outer `max_rounds`/`n_variants` by default — and consider pinning the inner+outer optimizer to a faster provider (groq) in the shipped config. Document the cost shape for the operator.
5. **A run that demonstrably improves.** The validation gate (`proxy_lift_corr ≥ 0.6` over ≥4 paired branches) PLUS at least one real `new promptpotter-self` whose outer best beats its outer origin — the proof the cheap proxy predicts real lift and the loop climbs.
6. **The loop owns its own information flow (§6). [MECHANISM BUILT — Arcs 1+2+3. Open: the validating data run.]** A distributable `promptpotter-self` must let the optimizer improve *how its meta-prompts are built*, not only their prose. Which signals each inner node sees (the per-node injection set) is the higher-value, dataset-agnostic axis. It **is now a searched axis** with a mandatory guard-rail floor: every optimizer node owns a `NodeLayoutSpec` in `NODE_LAYOUTS` (`domain/l1_layout.py`), and the L4 layout edit is wired for the three `editor == "l4"` nodes. What remains is **validation, not construction** — a full-signal run showing outer candidates that differ by inner-node *layout* (not only prose), with the winner's layout captured. Sequenced alongside 3–5 on the same run.

## Running & supervising a live `promptpotter-self`

**The infrastructure is done; the optimizer *application* is not — close it bug-by-bug, not by adding infrastructure.** The loop, seams, recursion, and scoring gateway all exist and are green. What remains is making the optimizer *behave well*, and that is found empirically: **run `new promptpotter-self` on `justlogic`, collect the data, read what the loop actually produced, fix the bug, re-run.** Expect several restarts; this is the loop, not a failure.

**The cadence must be SELF-FIRING, not event-driven.** A supervising agent schedules its own
wake-ups (the harness's ScheduleWakeup / self-paced loop, ~150–270 s) the moment a run starts,
and each wake IS a researcher pass over the reading list below. A passive log Monitor does NOT
count as supervision — it only fires on patterns you predicted, and every real bug so far
(estimator inconsistency, evidence starvation, proxy annihilation) was found by reading the
run's own measurement files, not by a grep hit. Monitor stays as a supplementary alarm only.
Role split: the operator is the developer/user (UX); the agent owns everything else.

**Supervise every live run actively — never fire-and-wait.** While a run (or any long optimization) is in flight, poll its output at least **every 2 minutes** looking for a newly-surfacing bug — read the fresh dashboards/measurements/goldmine log, not just the exit code. **The 2-minute window is for fanning out and researching — not for pausing.** Spend each interval *actively investigating*: fan out parallel searches over the fresh dashboards/measurements/goldmine log, chase the newest anomaly, read what the loop just produced. The cadence is **not** "check once, then wait" — it is "keep investigating and researching, up to 2 minutes, then check again." An idle wait between ticks is the wasted-run failure mode this guards against. A background run you set and forgot is a wasted run; the bugs show themselves *while it runs*, and catching one early lets you kill-fix-restart before the whole spend drains.

**Default the fix to the prompts** (the `_optimizer*/` meta-prompt set — wording, evidence framing, the per-node edit schema). Reach past prompts to a code fix ONLY when the data shows a structural cause — broken information flow (a signal the prompt needs never reaches it), a missing analysis (evidence the loop should compute but doesn't), or a wiring gap. Name that structural cause before touching code; do not add new infrastructure to paper over a prompt problem.

### THE PER-CHECKUP READING LIST — every 2-minute tick reads ALL of these, not just the log tail

**A checkup that only greps the goldmine tail is NOT a checkup.** Each tick, open the newest
`{cycle}/.runtime/cache/rounds/round_NNNN.json` (outer) and read every LLM tier's actual I/O:

1. **`l1_generate`** — rendered input (are the panels populated or empty?), raw output, parsed
   variants: `evidence_grounding.field` in the real enum? citations quote text that EXISTS in the
   rendered input? hypotheses distinct (not one idea relocated)? `variant_name`/`changes_description`
   populated? Any hallucinated node/param (validation drops)?
2. **`l1_critique`** — input carries the evidence (at the inner level: SAMPLE TRANSCRIPTS +
   MODEL REASONING present?); output `priority_fix`/`failure_highlights` quote CONCRETE evidence
   (a reasoning step, a premise), not recycled labels or scoring artifacts.
3. **Scoring** — per-candidate `candidate_scores` (accuracy, θ, θ_se, ci_lo), the
   **matched-origin** comparison (NEVER the cross-subset round-0 origin — subset drift reads as
   lift), PoBB stream (`p_best` moving off 0.5?), `decisions` (cuts firing, and on the right arm?).
4. **`l2_context` / `l3_plan` when fired** — validator failures (`paraphrase_repeat`,
   `dangling_trigger`), whether the task_context delta is evidence-anchored, plan text sane and
   within its render cap.
5. **Spot-check ≥1 inner campaign per outer sample batch** — the same four reads one level down
   in `.inner/<outer_cycle>/…/campaigns/justlogic__*/`.

Red flags that mean STOP-AND-DIAGNOSE, not keep-watching: `raw_chars: 0` / empty candidate list;
an outer sample returning in ~0.0s (stale-cache reuse — identity bug); off-enum grounding fields;
any optimizer call > 2 min; a headline Δ that disagrees with `matched_origin_*` / `improved`.

### Cross-run comparability — rules that always hold

- **Absolute outer numbers NEVER travel across runs.** Only a candidate's delta against ITS OWN
  run's origin is meaningful (same discipline as "verdicts compare lift-over-reference per model").
- Within a run, comparisons are **paired by seed** (each candidate runs the same
  `inner_dataset_seed`-pinned banks, per `datasets/promptpotter-self/inner_tasks.json`) — draw
  difficulty cancels; trust the paired PoBB/θ reads.
- The `inner_origin` identity fingerprint partitions runs into same-origin families; an
  origin edit = a NEW family. Never pool or compare across families.
- Residual cross-run noise = inner-process stochasticity (inner optimizer sampling, adaptive
  subset picks). Quantify it before trusting cross-run deltas (`noise-floor --k N`).

### The shipped ladder — read it off the files, not off this prose

**The config IS the source of truth** (`datasets/promptpotter-self/inner_tasks.json` +
`campaign.json`) — read every knob value there, never off this doc. The rules that stand:

- **No knob changes mid-run** — the JSON baselines are read per inner mint, so a mid-run edit
  splits the run into two fingerprint families.
- **`max_inner_rounds` ≥ 2** — at 1 the inner `levels` trajectory is length-1, so
  `after_N_rounds_delta` equals `first_round_delta` byte-for-byte and the formula's two delta
  terms silently double-count one number.
- **`elimination_n_min` is the panel-size floor** — keep the inner-task count at least one
  above it, or crowning starves.
- **`replicate_survivors` stays 0 in the distributable** (opt-in dev-stage successive-halving
  replication on `OptimizationConfig`). It complements CRN, not substitutes: CRN (a per-cell
  inner LLM seed shared by origin + every variant, `runner/inner_recursion.py`) cancels
  *common-input* noise in the paired diff; replication averages out the idiosyncratic
  single-run draw on the *diverging* inner-prompt path, replicating the ORIGIN reference too
  (its extra draws thread only into the decision estimators, the base draw stays the display
  floor). Coverage counts distinct cells, so replicates never falsely satisfy the floor.
- `rounds_to_N` and the per-seed cost multiplier are retired from the scoring formula (no
  candidate gradient — see §4's governing law); `rounds_to_N` is still computed for the inner
  narrative.

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
  per-round verdict (`domain/outer_verdict.py::compute_outer_verdict`) pairs the round's
  variant against the **cached round-0 origin**.
- **Candidate-arm inner round-0 is NOT re-measured** — the tenant-global `measurements/`
  store under the shared `.inner/<cycle>/` sandbox + content-addressed reuse replays the
  origin-arm's rows into candidate arms by construction; a "share the origin across arms"
  fix is unnecessary.

## Live-run learnings — bake these in, don't re-discover

- **Inner sandbox is a FLAT registry, not physical nesting.** Inner campaigns live at `<workspace>/.inner/<spawn_cycle_id>/` (sibling of `projects/`), NOT nested under the deep outer cycle dir — physical nesting blows Windows' 260-char `MAX_PATH` at depth 1; a flat registry named-by-but-not-under the spawning cycle stays shallow at every depth, so the re-entrancy invariant holds. (`runner/inner_recursion.py::InnerSpawnContext`.)
- **`in_process` connectors must NOT fetch a remote pipeline schema.** `init_services` skips the backend `GET /pipeline` for `in_process` execution and uses the local `pipeline.json` alone — otherwise it merges an unrelated backend's nodes under the dataset overlay.
- **L4 datasets carry their "samples" on disk, not as a CSV.** The outer "samples" ARE the inner tasks; `Connector.experiment_file` (`"inner_tasks.json"`) loads them through `extract_experiment` at bootstrap.
- **An inner failure must degrade, not propagate.** `run_inner_cycle` catches any inner exception and returns a zero-improvement proxy, so a bad outer candidate scores poorly instead of killing the outer cycle. Keep the fallback — but a *clean* origin-gate halt should return real proxies, not the exception sentinel; verify on the justlogic run.
- **Inner mints must NOT clobber the outer's active-pointer.** Every mint site threads `projects_root=session.store.projects_root` so a sandboxed inner mint stamps its own workspace, never the real tenant `active_session.json`. Same latent class in `sweep_runner.py` + `fork_siblings.py` (not hit by the inner loop; left).
- **The meta prompts must be TIGHT — gpt-oss-120b returns empty content on a verbose meta-prompt** (`raw_chars: 0`, `l1_provider_empty_response`). Keep `datasets/_optimizer_meta/prompts.json` terse; never fix this by touching the global `_optimizer/` node config (shared with every inner cycle).
- **The θ election needs graded outcomes, a scored origin, and ≥6 inner tasks.** `Observation.response` carries the graded per-sample fitness (the logistic MAP is valid ∀ y∈[0,1] — bit-identical for binary datasets); the origin is scored in every live run (no shape guess — the round-0 origin gate catches a genuinely-unscoreable origin LOUD); and offline replay shows the LCB election needs **≥6 inner tasks** to crown (at 2, θ_se exceeds the point-lift and it correctly refuses to crown on noise) — matching the composite-fitness panel goal (§4).
- **A zero-candidate round heals immediately.** `l1_zero_candidates` is folded into the existing `l1_generate_unusable` structural-breach rule (one widened predicate, `escalation_rules` stays 6) and fires L2 straight away instead of burning `l1_patience` dead rounds; the empty call is itemized in spend. Still measure on the supervised run how often the empty-content path fires and whether the L2 reframe recovers it.
- **The outer cycle heartbeats its OWN ledger while awaiting each inner run** ("inner rX/Y · best Z%") — `dispatch/llm_call/heartbeat.py` + `inner_recursion.py`; the chat maps the heartbeat to one upserted progress chip. Without it a healthy outer round looks dead for the whole multi-minute inner campaign.
- **An L4 outer sample must stamp `terminated_at` = the LAST outer node (`l3_plan`), never a mid-chain one.** `terminated_at` is the archive's reuse contract; an inner campaign consumes the ENTIRE meta-config at once, so a mid-chain stamp lets prefix-trust replay serve the ORIGIN's rows to any candidate editing a later node (fake 0.0s replays). Consequence: **every pre-fix layout-axis result was never honestly measured** — re-validate on a post-fix run.
- **A NO-OP probe's save REPLACES the origin's archive slot — by design, don't re-diagnose.** `MeasurementArchive.save` dedups on `content_hash` (newest wins); reuse stays correct, forensic origin rows live on in `round_0000.json` + the ledger, and index entries whose detail file was replaced dangle harmlessly.
- **Hang triage order: ledger tail → `dashboard.json::run_phase` → `.runtime/` flags → process table by command line → only then mtimes.** Control flags (`pause.flag`) are consumed at the next per-SAMPLE checkpoint — a mid-candidate pause stops within seconds and looks like a freeze to an mtime-watcher. The optimizer-call path already has a hard wall-clock (`_chat_under_deadline` → `OPTIMIZER_TIMEOUT`); overnight deaths with no terminal record are machine-sleep/session-end class, not code.
- **`token_budget` stays `null` for L4** (`datasets/promptpotter-self/campaign.json`) — the inner-spend rollup lands each inner campaign's tokens on the outer ledger as backend cost, so the normal-campaign default trips after a couple of inner campaigns while the USD budget sits nearly untouched. For L4 `spend_budget_usd` is the meaningful cap. (Root is L4's scale, not the rollup — the rollup correctly reports real tokens; don't uncount them.)

## 1. The shared in-process execution seam — SHIPPED

`Connector.execution == "in_process"` dispatches `run_query` to a connector-supplied
`Connector.in_process_run(query, payload)` returning the result shape the scorer already
consumes; the HTTP arm is unchanged, and dispatch stays on the declared mode, never the
connector name (`connectors/CLAUDE.md`). Two connectors ride the one seam: **`llm_only`**
(`connectors/llm_only.py` — calls the LLM client directly on the rendered prompt; six
committed datasets declare it, so the basic case needs no server — only `lca-termnorm`
still does) and **`promptpotter`** (delegates to the inner-cycle runner, §2).

## 2. In-process recursion isolation — SHIPPED; keep it depth-agnostic

`run_inner_cycle` (`application/runner/inner_recursion.py`) mints + runs each inner campaign
via `run_optimization` in its **own `asyncio.Task`** (fresh per-task ContextVar copies:
`_CYCLE_LEDGER` / `_CURRENT_ROUND` / `_ABORT_CHECK`) under **sandboxed stores** at the flat
`<workspace>/.inner/<spawn_cycle_id>/` registry. The re-entrancy invariant that makes L5+
come free: the sandbox is named by *this* cycle (never a global path or a baked-in
outer-vs-inner split) and the fresh-task spawn happens at *every* level — never assume
depth 1. The real recursion ceiling is **economic and statistical, not architectural**
(geometric cost; `proxy_lift_corr` decays with depth), which is the right place for the
limit to live. Known forward item: the process-global rate limiter is shared, so inner spend
competes with outer for TPM/RPM. Execution home is CLI/headless (`new`/`resume`); the
read-only uvicorn app *observes* outer + inner cycles via the file tree — no second
optimizer process, no HTTP self-call.

## 3. Specialized outer meta-prompt set — SHIPPED (lighter than specced)

The outer optimizer mutates whole meta-prompt templates, judged by meta-evidence
(mode-collapse, parse-fail rate, candidate stratification, proxy-lift), so it gets its own
prompt set: **`datasets/_optimizer_meta/prompts.json`** — prompt *fields* only. There is
deliberately **NO `_optimizer_meta/pipeline.json`** and `OPTIMIZER_PIPELINE_PATH` stays a
module constant: per-campaign pipeline *resolution* was not built because it would fork the
~600-line schema blob. The set is selected per-cycle by `OptimizationConfig.optimizer_set`
and applied through the **existing** per-node override channel
(`load_optimizer_set_overrides` → `set_optimizer_prompt_overrides` →
`_apply_prompt_override`) — the same channel the inner runner uses for its mutations, so
outer=meta / inner=default isolate by task with zero new ContextVar. The outer L1's per-node
edits ride the existing `L1Variant.pipeline_params_override` slot (no new
`OPTIMIZER_RESPONSE_MODELS` entry), and the meta-evidence panels are the existing
round-trace signals surfaced as outer injections, not re-derived.

## 4. Outer composite fitness — per-sample core SHIPPED; cross-sample terms open

Proxies are computed in-memory by `runner/inner_recursion.py::_compute_proxies` from the
returned `CycleResult` (no disk read; **there is no `outer_fitness` module**); the formula
lives in `datasets/promptpotter-self/campaign.json::scoring`, composing **lift core**
(normalized `headroom_lift`) × **bounded quality**
(`cleanliness · diversity_health`, floored 0.6 — a broken campaign is discounted, never
sign-flipped) × **efficiency** (`delta_per_dollar`, floored 0.7), times a worst-offender
token clip so a fat inner meta-prompt layout demotes (gate ≡ 1.0 for normal cost; missing
token data → vacuous 1.0; threshold deliberately high — tune down once a real run shows the
cost distribution). **Governing law: every term carries a candidate gradient** — terms
without one stay out (`rounds_to_N`; the per-seed cost multiplier). Held
emitted-but-out-of-formula pending the validation read: `rounds_improved_frac`,
`delta_per_candidate`, `delta_per_second`, `first_round_delta` (collinear with
`headroom_lift` — `max(levels)` includes `levels[0]`).

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
  the meta-prompt config *ranking* is statistically decisive, built on
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

1. **Shared `in_process` seam + `llm_only` connector — SHIPPED** (§1).
2. **`promptpotter` inner-cycle runner + isolation — SHIPPED & live-validated** (§2).
3. **[GATING] Inner benchmark with headroom + specialized outer prompt set — MECHANISM SHIPPED; full-signal data run open.** `inner_tasks.json` → `justlogic` (the origin headroom is **[VOID]/unmeasured**, finish-line item 1) + the `_optimizer_meta` set (§3), live-validated to emit per-node edits of the INNER meta-prompts. **Done when:** a real `new promptpotter-self` shows outer candidates with DIFFERENT proxies and outer best > outer origin.
4. **Enriched outer fitness + inner-spend rollup — rollup, per-sample composed fitness, and delta-led display SHIPPED; cross-sample terms remain** (§4). Each inner cycle's spend returns as the outer sample's `step_tokens`, fanning onto the outer ledger via the existing backend-cost channel. **Done when:** `proxy_lift_corr ≥ 0.6` over ≥4 paired branches.
5. **Distributable config + cost realism.** Tune the committed `inner_tasks.json` + `campaign.json` (and the shipped optimizer provider) so `new promptpotter-self` completes at evaluator-tolerable cost. The cost shape (geometric; wall-clock dominated by optimizer-call tails) is documented operator-facing in `dataset.md` § Cost shape — keep it there, not here. Default small; consider pinning groq.

   > **[VOID] Every cost anchor this item used to quote is unusable** — measured on an inner ladder that no longer exists and on deleted campaigns. **Re-measure before quoting a price to anyone.** Until then, `spend_budget_usd` in `campaign.json` is the only honest bound: it is a *cap*, not an estimate.

   **Done when:** a fresh clone can `new promptpotter-self` and watch self-improvement at bounded, disclosed cost.
6. **Per-node prompt layout — BUILT (Arcs 1+2+3); full-signal data run pending** (§6). **Done when:** the run shows outer candidates that differ by inner-node *layout* (not only prose) and the winner's layout is captured in `winner_pipeline_params` — validate alongside slices 3–5 on the same run.

Slices 1 + 2 shipped. 3 is the gating slice (no real signal without it); 4 + 5 make the result trustworthy + shippable; 6 (the information-flow axis) is required for the distributable claim and sequences after 3. The agent drives 3→6 autonomously, escalating only for real spend approval / provider change / compaction.

## 6. Per-node prompt layout — the searched information-flow axis

**BUILT (Arcs 1+2+3); `NODE_LAYOUTS` (`domain/l1_layout.py`) is the SoT — read the search
space off the code, not off this doc.** Which signals reach each inner meta-prompt is a
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
silently dropped). Watch item: `prompt_block_catalogue` is `char_cap=None` and renders the
whole block library on `l1_generate`'s floor — the one uncapped blowup vector.

**Open: the validating data run** (slice 6). The operator-facing layout matrix (generate it
from `NODE_LAYOUTS` into one researcher doc — don't fork the prose) and any webapp surface
stay deferred with the L4 UI lane.
