# Fitness comparability — collapse the θ/accuracy boundary

> **What is built and what is not is § Implementation order — this line does not restate it.** Open at the top level: the cross-round headline surfaces, the lineage `/N` badge, and feeding graduated discrimination into selection. **Prerequisite to [`l4-outer-loop.md`](l4-outer-loop.md)** — the L4 outer fitness reads inner-campaign improvement, which is comparable only because this landed.

## Why — one quality number, accidentally split into two

Quality was computed twice by modules that never reconciled: a difficulty-aware **ability θ** that reached only sample selection and the heatmap, and a difficulty-blind **accuracy/composite** that did the gating. **Per-round resubset is what woke that dormant duplication** — under a fixed subset raw accuracy is monotone with θ and the split is invisible, but once candidates are measured on different signal-chased subsets the two orderings disagree. That disagreement IS the per-candidate-fitness distortion, and it is what kept resubset off until θ gated. Paired candidate-vs-origin matching was only a partial guard; θ is the cross-candidate-invariant version of what it was reaching for.

## Decision — gating fitness IS θ

**Collapse the boundary: the gating fitness becomes the latent ability θ that already exists.** This is wiring, not building — the 1PL estimator runs every round; it is trapped on the selection/display side of a wall that should not exist.

This is the **standard** fix, not a bespoke one: difficulty-adjusted ability is exactly what Item Response Theory / Computerized Adaptive Testing use to compare test-takers who answered *different* question sets — the Rasch "specific objectivity" property. It is a small, simple statistical model applied **wide**: one fit, consumed by both sample selection and the gate, that removes the per-round sample-set drift at the root rather than patching each downstream symptom. The cost is one joint fit per round (already paid for selection).

- **θ gates; accuracy/composite stay as recorded display.** Election, PoBB elimination, and the improvement gate compare candidates on θ (PoBB and the improvement gate additionally use `theta_se`; the round-winner election ranks on the point-estimate θ lift, no SE margin). Raw `accuracy` and `composite_fitness` remain recorded and shown to the operator — but as *subset-relative* display numbers, explicitly flagged `mode: measured`, never the cross-candidate gate.
- **The improvement gate's `delta_ok` moves to θ-units (logits).** `delta_ok = θ_winner − θ_matched_origin > improvement_threshold_logits`, from a pairwise joint Rasch fit (winner + origin folded in) — the origin is always in the pool because it is scored. The accuracy-space `improvement_threshold` is recalibrated to logits **per round** by local linearization at the matched-origin operating point (slope `p(1−p)`), so the knob keeps its meaning ("min accuracy delta") and no config file changes. **`c0_ok` — the cross-round floor against the frozen round-0 origin (C0) — needs the stable δ bank**, because θ is comparable only within one joint fit and its own `mean(θ)==0` anchor; slice 2's fixed ruler is what lets it read θ rather than accuracy across rounds.
- **Comparability lock — stabilize the δ bank.** θ is comparable *within* a joint fit (shared δ, mean-θ=0 anchor), but δ currently re-fits over an accumulating observation union each round, so the difficulty scale drifts between rounds. Pin it: the per-dataset δ bank is the **accumulating grade-A measurement archive** (the provenance grade, `domain/measurement_provenance.py`) — calibrated from clean measurements, refreshed at round boundaries with **fixed-parameter (anchor) updating** rather than a from-scratch refit, so δ moves smoothly and θ stays on one scale across rounds. The bank *is* the clean corpus L4 later ingests — same accreting asset.

## Selection + efficiency (already mostly built)

Per-round resubset can turn back **ON** once fitness is θ, because θ makes the drifting subsets comparable. The adaptive queue already chooses informatively: `adaptive_queue_mechanism.py::pick_value = decision_information_gain (verdict MI: will this sample move θ_c past θ_s?) + delta_learning_gain (δ Fisher-information entropy drop)`. That is the "always be in the maximum-signal range" behavior, wired into `select_round_subset` (between-round). **There is no online within-round re-fit** — that mechanism was deleted; within a round every candidate shares one deterministic seed-stratified order (`build_round_order`). See [`verdict-resolution.md`](../methods/verdict-resolution.md) for the two-mechanism split. Textbook **maximum-Fisher-information-on-θ** selection is a small refinement of the headline term if wanted; the information-theoretic acquisition is present today.

**Stopping = sequential elimination on θ.** The live mid-round stop is `PoBBCheck` (`pobb/checks.py`) — it eliminates a candidate the moment its `p_best` (closed-form P(best) on the fixed δ ruler) drops below ε, measuring the fewest samples needed for a decisive verdict. Cross-cycle/engine comparison is the deterministic A/B replay engine.

## 1PL → 2PL graduation (per-dataset, gated)

1PL fixes the drift now with the data we have; **2PL adds power once enough data is collected.** "Some samples carry more signal" is **item discrimination** — a per-sample **2PL** parameter (aᵢ) that 1PL/Rasch cannot represent: it is the sample's **signal-to-noise**, how sharply it separates able from unable candidates. 1PL says only *how hard* a sample is; 2PL also says *how much it tells you*, so both selection and the gate can weight high-signal samples harder and discount noisy ones instead of treating every sample as equally informative. Built as an enhancement, adopted only where it provably wins:

- **One ruler, the switch invisible above the seam.** The difficulty ruler value is `RulerEntry = float | tuple[float, float]` (`exploration.py`): a bare δ is 1PL (a≡1), a `(δ, a)` pair carries discrimination — the richer 2PL value rides **inside the same mapping** every θ consumer already reads. `fit_theta_given_delta` generalizes to `p = σ(aₛ·(θ − δₛ))` (flat where cold: absent sample → δ=0, a=1), so election / c0_ok / the stall ladder / PoBB / the replayers read the chosen model with **zero call-site change** — only the seam's body unpacks. `RaschPosterior.ruler()` folds δ + a back into the one mapping.
- **Both estimators behind the seam.** `fit_rasch_2pl` is the hierarchical 2PL joint fit (warm-started from the 1PL fit; alternating-Newton over θ, δ, log aₛ; `log a ~ N(0, σ_a²)` shrinks toward a=1 and pins the a-vs-θ scale degeneracy). `RaschPosterior` gained `discrimination` + `discrimination_se`.
- **Switch is per-dataset bank**, gated by `graduate_ruler_model` on **both**: (1) a cheap **BIC pre-check** on the full fit (2PL must clear `2·Δloglik > n_s·ln N` before the costly CV runs — refuses to buy `n_s` discrimination params that don't pay for themselves); (2) **cross-validated held-out log-likelihood** on (sample, hit) pairs as the **primary** test (deterministic stride folds, scored only on candidate+sample seen in train — won't reward overfitting). `_calibrate_delta_ruler` calls it once per cycle; the model serializes nowhere (re-derived at calibration, same as the ruler).
- **Hysteresis** — 2PL must win held-out by a per-response `margin` and the decision is only re-evaluated at each calibration refresh, so the bank does not flip-flop round to round. The held-out gate also means 2PL can **never regress** a dataset (a cold or non-discriminating dataset stays 1PL automatically). Operator knob: `optimization.enable_2pl_graduation` (default ON, self-gated by CV); OFF pins 1PL everywhere. **Forced OFF inside an L4 inner cell** (`runner/inner/tasks.py::inner_instrument_config`): under 2PL the ruler carries discrimination `a`, so θ is in units of `1/a`, and each cell graduating on its own CV would make the panel average — and t-test — a mixture of scales. Tagged on the field itself (`Knob(..., Estimand.DISCRIMINATION, ...)`) + the `graduation_self_gated_on_holdout` info coupling in `knobs.py`.

## Implementation order

**Slices 1–3 have shipped** — θ into the gating seams + resubset ON · the one fixed δ ruler (`fit_theta_given_delta` on the per-cycle `cycle.delta_scale`) · 2PL graduation behind the seam. The numbers survive because code cites them (`optimization/cycle.py`, `intelligence/exploration.py`). *Open follow-up to slice 3:* `select_round_subset` + `hard_sample_sorter` still fit 1PL for the selection/heatmap δ — feed the graduated discrimination into the **acquisition** term so signal-chasing weights by real aₛ.

4. **Webapp parity — surface θ so the θ-elected winner is never shown losing on a number with no explanation, and let the operator choose which metric they read.** The engine now *decides* on θ but the webapp reads no contract carrying candidate-level θ — every surface (fitness bars, lineage node values, scoring inspector, "Best" tile, sidebar `best_accuracy`) still renders subset-relative `accuracy`/`composite_fitness`. The visible failure: in an expanded lineage lane the θ-winner glyph can sit beside a *losing* sibling showing a **higher** accuracy, unexplained — the operator sees a contradiction the engine resolved internally. Reach parity in three parts:

   **(i) Thread θ onto the read-models.** `theta`/`theta_se` on `ScoredCandidate` → `RoundSummaryCandidate` (dashboard) + `LineageNode` (the served tree), stamped from the single election fit (`elect_round_winner` now returns `(winner_id, abilities)`); TS types regenerated. Live `current_round` θ deferred — mid-round has no election fit (it would be the online adaptive-queue θ, a different source).

   **(ii) Operator-selectable headline metric — θ is jargon, so never force it on the user.** The *gate* is always θ (engine truth); the *headline number the operator reads* is **selectable among `accuracy` / `composite` / `ability` (θ)**. The shape (cross-round surfaces + the subset badge are the remainder, below):
   - **θ can't be a 0–1 bar** — it's a logit. So the selector governs the **single-value text surfaces**, **not** the `FitnessChart` bar series (accuracy stays the bar; composite/what-if stay its existing toggles; θ stays in the tooltip + inspector that (i) shipped). **θ is not a lens option** — a lens re-projects a *scoring formula* server-side, but θ is a different statistic already served per-candidate, so the headline switch is a **client display toggle seeded by a served default**, not a server lens re-projection.
   - **The default is `CampaignConfig.headline_metric`, NOT `OptSearchPoint`.** It is *display* config (how fitness is shown), not optimizer *search* state (what mutates per candidate) — so it doesn't ride `OptSearchPoint` (that would be the sidecar the rules forbid). It lands beside the scoring formula on `CampaignConfig`, declaring itself `Knob(Scope.POLICY, Estimand.DISPLAY)` (display-only, no data fork), stamped onto `LiveDashboardState` at INIT:exit (beside `run_limits`, so a fork carries its own default) and served at `dashboard.json::headline_metric` — the same state→dump path `composite_fitness_formula` rides. The webapp seeds the toggle from that served default (`dash.headline_metric`); a manual pick overrides for the session.
   - **Surface = the candidates card** (the named "visible failure": the θ-winner glyph beside a *higher-accuracy* losing sibling). ONE multi-select Metric control (Acc / Comp / θ) drives the whole card — the bar series AND the node values in both the dendrogram and the forest, so the two halves cannot disagree about which number is being read. Accuracy/composite render as a percent; θ is a logit (`θ 0.41`), so it rides its own right-hand bar axis and stays strictly sparse (a missing θ is never coerced to 0 — 0 is a real ability). `composite_fitness` is served per candidate on the tree — `GET /campaigns/{c}/cycles/{cy}/tree`, verbatim from the dashboard round summary — so settled forks honor the composite selection on the same basis as the active cycle; the webapp never re-selects composite-vs-accuracy (the TS `displayFitness` re-implementation is deleted — `domain/rendering.py::display_fitness` is the sole owner of the rule). **The winner glyph keeps its always-on θ tooltip line** regardless of the selected headline (true after (i)) so "won on harder samples" never disappears.
   - **`mode: measured` vs `all` — reuse, don't add.** The subset basis the number was measured over is **already served**: `scored_samples`/`expected_samples` on `RoundSummaryCandidate` (dashboard) and `n_samples`/`n_expected` on the webapp `CandidateRow`. No new enum field (it would raise the ledger for a fact already on the wire — surface-ledger rule). `LineageNode` now serves `scored_samples`/`expected_samples` too; badging them as a `/N` on the node is the unbuilt half.
   - **Remainder (documented, not built):** (a) extend the toggle to the **cross-round** surfaces (the "Best" tile, the TopStrip sparkline, the sidebar `best_accuracy`), which compute a cross-round aggregate — the number they read is decided below (§ The accumulated cross-round number); the fixed ruler removes the "different per-round anchors" objection. (b) the lineage subset `/N` badge.

## The accumulated cross-round number — DECIDED (2026-08-02)

The operator asked for an **accumulated one-to-one comparable number**: every candidate ever
measured on one scale against a common reference, not just within-round pairing. Two candidates
were on the table, they give different numbers, and only one can headline.

**It is θ on the cycle's locked δ ruler, re-projected to accuracy units for the human.**
Subset-invariance is not a property θ approximates — it is what the Rasch model is *for*, so
"comparable across rounds that drew different samples" is satisfied by construction rather than
by an accumulation step. It is already computed (`cumulative_theta` rides every round summary on
every banked cycle, with `calibration_model` stamped beside it), already the gate, already the
election's rank key. The jargon objection that kept θ off the text surfaces is answered without a
new mechanism: `ruler_expected_accuracy(θ, δ_ruler)` re-projects the ability onto the ruler's one
fixed reference set, giving a percentage that is still subset-invariant. It already ships — it is
what `theta_accuracy_ci` draws the candidate whisker from.

**The alternative — an accumulated paired-vs-origin accuracy over the union of shared samples — is
refused, on a measurement.** That union is not a neutral sample of the dataset: `build_round_order`
partitions each round on the *incumbent's own grades* (every 4th slot a cell it passed, the rest
cells it missed) and `select_round_subset` chases information gain, so the union is assembled by
conditioning on outcomes. An accuracy accumulated over it inherits exactly the pathology measured
in `scoring/metrics.py::matched_origin_stats` — where the origin's rate on such a set is `⌊n/4⌋/n`,
a function of how far a candidate got — only at larger `n`, which makes it look *more* trustworthy
rather than less. It also cannot be computed without new spend: the origin is re-scored per round
on that round's panel, so it holds no measurement on late-round cells at all.

**Honest absence, not a fallback.** A cold ruler makes θ collapse to that round's logit-accuracy
(`adopted_level_trajectory` states this), so the accumulated number is **absent** there — never
silently substituted with raw accuracy, which is the subset-relative quantity this whole spec
exists to stop headlining.

Unbuilt: pointing the three cross-round surfaces at it.

   > **The ruler has THREE states, not two.** A cold ruler is **flat** — θ degenerates to logit-accuracy — so it is neither 1PL nor 2PL; `calibration_model` is `None` there, and the popover says *not yet calibrated*. Naming it "1PL" claims a fitted difficulty ruler where none exists, so never collapse the third state into the first.

   **Done when:** the operator can pick which metric headlines the lineage node values (seeded from the campaign default). Remaining: the cross-round readouts ("Best" tile, sparkline, sidebar) follow the toggle too, reading `ruler_expected_accuracy(cumulative_theta, δ_ruler)` and absent where the ruler is cold; and the lineage subset `/N` badge.

## Named seams — open work only (the shipped mechanism reads off the code)

| Concern | File |
|---|---|
| Feed aₛ into selection (slice 3 follow-up) | `exploration.py::select_round_subset` + `hard_sample_sorter.py` — still fit 1PL for the selection/heatmap δ; the acquisition term should weight by the graduated discrimination |
| Selectable headline metric — cross-round remainder | the "Best" tile, TopStrip sparkline, sidebar `best_accuracy` follow the toggle. **No longer blocked** — the number they read is decided (§ The accumulated cross-round number): `ruler_expected_accuracy(cumulative_theta, δ_ruler)`, absent where the ruler is cold. Plus the lineage subset `/N` badge (`scored_samples` — now served on `LineageNode`; the badge itself is unbuilt) |

## Non-goals + validation

- **Non-goals:** removing accuracy/composite as *display* (they stay, flagged subset-relative); changing the inner scoring backend. (The webapp θ surface was *initially* a non-goal but is now **planned as slice 4** — parity is required so a θ-based decision is never shown as an unexplained accuracy inversion.)
- **Validation (silent-harm — a wrong fitness is invisible):** a regression test in the numerics suite (`tests/test_numerics.py`) that constructs two candidates on *different* subsets where raw accuracy and θ disagree, and asserts election/PoBB now follow θ (the candidate with higher ability on harder samples wins) — the exact drift the boundary caused. Plus an efficiency check: resubset-ON reaches a decisive θ-ranking in fewer measurements than resubset-OFF reaches a stable accuracy ranking.
