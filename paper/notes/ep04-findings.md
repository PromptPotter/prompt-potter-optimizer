# EP 04 livestream (2026-09-24) — what surfaced, and where each goes

Runs: justlogic-d234 × {qwen3.7-flash, glm-5.3-flash, gpt-oss-20b, mercury-2.5, laguna-xs-2.1, gemma-4-26b},
configs `C:\Users\dsacc\Streaming\stream_04_harness-arena\prep\jl-*.yaml`.

## Fixed in the working tree (uncommitted)

1. **A model's unparseable JSON aborted the whole walk.** TermNorm answers `422 json_parse_failed` after its
   repair turns; `_classify_http_error` filed every 4xx as CLIENT ("caller config rejected") → `skipped_after_CLIENT_error`.
   Now the three output-failure codes are UNSCOREABLE. `application/scoring/sample_measurement.py`.
2. **`samplescan` measured a different configuration under the arm's name.** The ladder step re-sent the cell on
   `pipeline_schema.to_pipeline_params()` (no node config, no prompt, no model) and banked it as the arm's row; under a
   spend ceiling it also halted the run (`backend_cell: no rate bounds what it may cost`). Step deleted.

3. **FIXED — parallel `new` launches cross-contaminated** (root: `new` re-read the tenant pointer after
   minting, `new.py:424` → `resolve_target`; `load_session` now takes the minted hop). Archive clean; `__37b160` holds
   laguna's origin beside gemma's; `__6aaae9`, `__1e4623`, `__d54a91` are orphan mints.
4. **NOT a phantom** — the 3/3 counted live producers; two of them drove one cycle (fallout of #3).
   Open: admission lets two producers drive one hop.
6. **FIXED — `origin-gate {proceed,rescore,abort}` CLI verb** on the webapp's dispatch path.
8. **FIXED — `set-concurrent-cycles` refusal names `MACHINE_RUN_CAPACITY`.**
5. **TermNorm "collapse" was a manual restart**; the 503s are TermNorm's fixed 60 s provider timeout
   (`config/pipeline.json:337`) against 8192-token answers, retried 5× by PP (each likely billed, recorded 0).
   PP gives an unreachable backend 15 s. Fix in flight (PP side + `.scratch/termnorm-timeout.patch`).
13. **`resume` on a partial origin (4/40) advanced to round 1** instead of finishing round 0 (`__da0bb8`, deleted).
14. **"Caching wasn't working"** (operator) — replay vs provider cache, diagnosis in flight.

## Open — root cause pending

3. **Parallel `new` launches cross-contaminate.** `justlogic-d234__37b160` (campaign.json: gemma) has a round_0000
   whose `pipeline_params` name `poolside/laguna-xs-2.1`; a 16:45 gemma launch printed `minted __1e4623` then
   `Campaign: __6aaae9` (a laguna campaign). Suspect: the process re-reads the tenant's shared active pointer after mint.
4. **Job registry counts dead producers as in flight** for ~1 min after a kill → `Concurrent-cycles ceiling reached (3/3)`
   with one live run.
5. **TermNorm fell over under ~3-4 concurrent runs** (`All connection attempts failed`), after a burst of
   `503 LLM request timeout`. Running with `uvicorn --reload`.
6. **Origin gate has no CLI verb** — a backgrounded run parks at `origin_gate_r0` forever; only stdin/webapp/python answer it.
7. **`max_rounds` cannot change on a running cycle** — `set-budget` takes usd/tokens only; no webapp control.
8. **`MACHINE_RUN_CAPACITY` needs `.env` + a server restart** and `set-concurrent-cycles` names neither.
9. **Parallel runs share `logs/latest.log` and the active pointer** — the webapp shows only the last launch;
   supervising N runs means N ad-hoc log files.

13. **FIXED — resume re-closes round 0 from the fresh origin measurement and re-runs the gate** until an L1 round
    has closed (`runner/loop.py`); **admission refuses a second job on a busy cycle** (`CycleBusyError`, 409 `cycle_busy`).
14. **Caching:** replay cache worked; the ladder discarded ~20% of replays over a bare warning (a successful schema
    repair). FIXED — `needs_rerun` gates the ladder on infra/fatal codes. Provider prompt-cache is UNMEASURED
    on task calls: TermNorm drops `cached_tokens` → `.scratch/termnorm-cache-read.patch`.
5b. **FIXED (PP side) — outage waits up to `BACKEND_OUTAGE_S`=600 s probing `/status`; a 5xx with `retryable: false`
    halts the cell after one POST.** TermNorm side = `.scratch/termnorm-timeout.patch` (NOT applied — operator).

## Origin probe (2026-09-24, 30 cells, direct /matches, `scratchpad/origin_probe.py`)

Gemma's ~6000 tokens were HIDDEN REASONING switched on by the dataset's `reasoning_effort: low` (gpt-oss's pin),
inherited silently: `probe-reasoning` shows gemma thinks 0 tokens unset/none, ~480 at low/medium/high. With `none`:

| model | base out / acc | lean out / acc | terse out / acc |
|---|---|---|---|
| mercury-2.5 | 608 / .70 | 464 / .83 | 450 / .57 |
| gpt-oss-20b:nitro | 734 / .40 | 212 / .47 | 187 / .47 |
| gemma-4-26b (effort none) | 517 / .67 | 173 / .57 | 90 / .47 |
| qwen3.7-flash (effort none) | 793 / .77 | 203 / .70 | 171 / .83 |

At n=30 the accuracy SE is ~±0.09, so accuracy moves are mostly noise; the TOKEN cut (−25% to −75%) is not.
Gemma at effort `low` timed out at TermNorm's 60 s on 25/30 cells.

**Applied 2026-09-24:** `justlogic-d234` origin → the lean variant (prompt + schema descriptions), `per_cell` pin
1546 → 692 (lean median total tokens, gpt-oss, 40 cells). L4's inner baseline moved with it. Everything after
this probe — the check-in A/Bs, the reasoning floor (FIXED), the open origin experiments — lives in
`.scratch/origin-research.md`, which supersedes this section.

## Wall-clock (7 justlogic campaigns, 13,245 s total; `.scratch/ep04-wallclock.md`)

1. 3,366 s — `__37b160` re-resumed a model TermNorm timed out on (8× `503 LLM request timeout` at 60 s). OPEN:
   root is TermNorm (`.scratch/termnorm-timeout.patch` → 504 `retryable:false`, PP halts after one POST).
2. 2,934 s (~50% of every finished run) — optimizer calls, deepseek-v4-flash:nitro, `l1_generate` at effort
   high, median 47-78 s/call, reasoning 3.5-6.6k tokens. OPEN — the largest recurring lever. EFFORT IS NOT
   IT: replaying 5 banked l1_generate calls at high/medium/low (`.scratch/l1gen-effort/`, $0.035) gave median
   63/62/56 s and reasoning 6.4k/6.4k/5.3k, and `low` produced the single worst call (a repair, 156 s). The
   model reasons ~5k tokens whatever the rung → next lever is the INPUT (panel size) or a faster optimizer model.
3. ~550 s — `__4d70b3` ran serial (avg 1.08 cells in flight; look-ahead never pressed). By design (browser-only).
4. ~3,555 s — parked campaigns (origin-gate waits, abandoned parallel mints). Practice, not code.
5. 30-50% of each run unattributed. VERIFIED on the validation run: 296 s of 997 s; `l1_critique` (~58 s per
   round) and non-escalation `l2_context` sit in no `CampaignPhase` bracket. FIXED: `wall_clock` derives
   per-node call spans from `token_usage` records (`unbracketed_call_s`); unattributed 296 s → 6 s. The run's
   anatomy: cells 461 s (serial) · l1_critique 181 s + l1_generate 179 s back-to-back (36%) · l2 55 s ·
   ~36 s/round of fresh cells AFTER `l1_score` exits — `measure_overlap`, the overlap series. FIXED: it now
   runs concurrently with diagnostics + `l1_critique` (`l1/execute.py`), which read none of its fields;
   validation `__374835` rounds 3m21/3m12 vs 4m55/4m23, overlap's 49 s hidden under critique's 80 s. Remaining
   levers in order: look-ahead (browser) for cells; fold critique into generate; a faster optimizer model.
   Critique has no off switch (`critique` is `L1_MANDATORY`); either lever changes the optimizer's prompts
   (L4 identity), so it needs an A/B that can RESOLVE a difference — several campaign pairs on the overlap
   line, not one — or an L4 run. Not a one-night change.

## Validation run (2026-09-24 night, `justlogic-d234__a0d512`, mercury-2.5, lean origin, 3 rounds, $0.065)

Every fix of the day live on one run: lean origin + model reasoning floor + `label_match` + ungradeable-output-is-a-miss.
16.6 min total (rounds 4m55 / 4m23 / 5m12; cells serial at ~1.2-3 s). Overlap line on 34 shared cells:
C0 67.6% → 76.5% → 79.4% → 79.4% — the stream's mercury run on the OLD origin read 55.9% → 61.8%.
Run 1 of this validation surfaced the truncation-as-hole halt (fixed; see sample_measurement `_answered_nothing`).

## Instrument

10. **The origin is verbose and saturates the strong models.** Median output tokens at the origin: mercury 580,
    qwen 651, gpt-oss 752, glm ~1600, gemma ~6000. Qwen sits at 90% on it. Experiment: a compact origin.
11. **`per_cell` token pin 1546 is gpt-oss-specific** — lineup configs used plain `fitness`.
12. Laguna cannot emit JSON on this route at all (3/3 cells `json_parse_failed`) — a model fact, caught by the 3-error stop.

## Information flow into L1 critique / generate (trace audit of the 6 rehearsal + validation cycles)

1. **FIXED (TermNorm) — the critique saw no model reasoning on 3 of 4 models.** `SAMPLE TRANSCRIPTS`
   reads `pipeline_data.reasoning_trace`, which TermNorm filled from HIDDEN reasoning only; a
   structured response's non-answer slots (the origin's `reasoning` field) were discarded. mercury /
   qwen / gemma (effort none) transcripts were query + label, so the critique "quoted" PREMISES as
   the broken step and invented mechanisms (a0d512 r1: "Mechanism: fails to test for
   contradictions", quoting a premise). gpt-oss showed only its hidden reasoning, never the visible
   working the prompt shapes. `api/pipeline_steps.py::_step_llm_only` now appends the other slots.
2. **FIXED — failing_samples crowded the transcripts out of the critique.** `compose.select`
   round-robins ITEMS, so ~34 150-char rows beat 3k-char transcripts: cb9ff0 r1 critique placed 1
   of 3 transcripts beside 14 failing rows (2.6k chars) whose content was a 100-char premise opener.
   Over enumerable labels the panel now renders confusion groups (`said X, true Y — n: #id(δ)…`).
3. **FIXED — dead diagnostics bytes.** SAMPLE DIAGNOSTICS (stems, no ids) renders only for a miss
   carrying rank / retrieval facts; MISSED OPPORTUNITIES keys on sample ids, not 60-char stems.
4. **FIXED — L2 read junk.** HISTORICAL BEST was 3× `acc=100% n=1 pobb_backfill` (single-cell
   backfills were the modal total); axis_memory's one-cluster `llm_only (100%)` line is silent. L2
   (cb9ff0 r1) moved axis_memory into L1's thinking_style slot on the strength of that junk.
4b. **FIXED — transcripts led with rejected edits' runs, never the parent's failures** (`3184b2acb`,
   v0.8.16): lost cells (parent hit, ≥2 edits missed) took the slots first, so every rehearsal
   round-1 critique read only rejected prompts' flips. The parent's misses now lead, lost cells
   interleave behind them.
4c. VERIFIED on `justlogic-d234__f9f8a5` (mercury, 2 rounds, 73.5%): the r1 critique read 3
   transcripts WITH reasoning and named three distinct mechanisms from the model's own words.
   Round 0 still had none — the origin replays cached rows banked before the TermNorm fix, so a
   replayed row carries no trace until it is re-measured.
4d. OPEN (v0.8.14 `f28dd1903`) — the optimizer render order puts all evidence LAST, after
   instruction + answer_format, for the prefix cache. Unmeasured quality cost; prime suspect.
4e. OPEN (`909fa50a4`) — L2/L3 carry the SKILL TARGETS block on every target; gate on the prompt
   delivery predicate (`on_demand`), which needs that fact on the bundle.
4f. OPEN — RARE-HIT SAMPLES renders 50-char stems + the hitting runs' names: no pattern to replicate.
4g. **FIXED — the search's menus answered for the DECLARED model, not the running one.** The run's
   schema resolved capabilities for `selectable_models` only, and `param_options` defaults to
   `current_config.model` = the schema's declared model — so mercury/qwen/gemma were offered
   gpt-oss's effort ladder (no `none`, their own floor). `pipeline_resolve.py::schema_as_run` stamps
   each active node with the model it runs + resolves its capabilities; the floor and
   `session.pipeline_schema` both read it, so every model-dependent query answers for the runner.
4h. Audit leads, not acted on (v0.8.13→HEAD): L1 may now switch the answer schema off
   (`response_format` toggle, 18f1d12f5) — a slot spent on a 3-label task; the generate instruction
   lost "a small model drowns in rigor it cannot execute" (f28dd1903); misses read raw fitness, not
   the token-priced composite (17b479cc6), so a correct-but-long answer never reaches the critique
   under a `per_cell` token formula; STATUS/TREND lost the accuracy series for "N rounds since the
   last election" (4a81783ba).
5. OPEN — the generate prompt argues against the lever the data points at. answer_distribution:
   gpt-oss answers Uncertain 75-80% vs truth 21-30%; the framing says anti-hedging backfires and
   the L1 instruction says "text that makes the solver EMIT more is paid" — under `per_cell:
   fitness`, where tokens are not priced. Every round-1/2 edit kept the origin's five-step cap.
6. OPEN — diversity collapses downstream of a one-cluster critique: cb9ff0 r2's three variants are
   one hypothesis (inference rules) in three fields. Expected to ease with 1-2; re-read after.
7. OPEN — the prompt block library serves 8 generic thinking_style lines ("long-term
   implications…") on a logic task, ~800 chars/round; silence was measured to starve L1, so this
   needs relevance, not removal.

## Dress rehearsal (4 models in parallel, 2 rounds, $0.08 cap each, per-model reasoning FLOOR, lean origin)

All four `new` launches admitted and ran side by side, one readout each; overlap line on 34 shared cells:
mercury 67.6→73.5 · gpt-oss 41.2→47.1→47.1 · qwen 70.6→79.4→79.4 · gemma 52.9→67.6 (then spend-halted).
Seen working under load: truncated gemma answers graded as misses; a dead gemma call → one `504 llm_timeout` at
its 205 s deadline (was 5×60 s retries); the $0.08 ceiling halting on unreported-bill reservation; qwen 429s
held by backpressure. Slowest step: mercury r1 `l1_generate` 142 s = 3 calls (schema repair + re-ask, 3
validation errors). FIXED the blind spot: `LLMResponse.schema_repair_errors` (one entry per paid retry,
replacing the bare count) lands in the ledger action and the round audit, and the retry warning prints it.

## Round-2 clone collapse on `justlogic-d234__f9f8a5` — root cause (2026-09-24)

Symptom: r2 L1 returned 3 variants whose `changes_description` each names a prompt edit
(thinking_style / instruction / answer_format), but every `prompt_fields_updates` was `{}` and every
`task_context_updates` was `{"upstream_context": "", "downstream_context": ""}`. Result: C2.1 "valid",
C2.2/C2.3 `duplicate_variant`, SP table shows all three `[clone]`, C2.1 cut 0/8. No schema repair fired.

Mechanism — ONE rule ("did this variant change anything?") had FOUR definitions that disagree on "":
- `L1Variant._reject_empty_mutation` (parse): `""` vs parent's 135-char upstream_context DIFFERS → a
  mutation → no repair retry. `{node: {}}` overlay also passes (container test).
- `l1_invariants.detect_invariants`: same value test → C2.1 live, the twins duplicates of it.
- `build_candidate_flat` (SP table): skips falsy values → `""` is NO edit → `[clone]`.
- `candidate_delta` ("the ONE delta definition"): covers prompt fields + params, NOT task_context.
So the model's "" (its spelling of "unchanged" for two properties the wire schema listed) was read as
"clear the operator's framing" by the gates and as "nothing" by the display.

Structural cause: `task_context_updates` is a third carrier for target-prompt text that
`problem_description` already carries, and it is half-built — `ScoredCandidate` persists only
`prompt_fields` + `pipeline_overlay`, so a task_context edit is invisible to ALREADY TRIED, the repeat
gate, earned blocks and (to check) resume/export. The wire schema listing both keys invites the model
to fill both.

Why the model emitted nothing: its FULL reasoning (24,780 chars, in `optimizer_reuse/`) planned all
three edits, then emitted empty slots. The wire schema, rebuilt offline, was correct (6 open fields,
params offered) and both rounds were served by Alibaba — a decode miss on the provider/model side.
What we own is that nothing caught it.

DONE (uncommitted, gate green):
- `domain/candidate_diff.py`: `CandidateDelta` (prompt + params, `signature()`, falsy = clone) from the
  ONE `candidate_delta`; "" and `{node: {}}` are never edits. Parse guard, `detect_invariants`,
  ALREADY TRIED, earned blocks, `parse_population` all read it.
- Deleted the context slot end to end: `task_context_updates`, `TASK_CONTEXT_OVERRIDES`,
  `TaskDecomposition.merge`, `CandidateProposal.prompt_fields_updates` (the child OSP carries it), the
  SP-diff `tc.` rows, the adopt's `advanced` entry, the webapp block.
- Wire: prompt values `minLength: 1`; parse drops blank values; prose says "omit a field you keep".
- `call.py`: ledger reasoning keeps head AND tail (the decision was in the cut tail).
- Export: `PromptTemplate` (base) now renders in the TARGET order; `OptimizerPromptTemplate` alone
  carries the cache order. `export.json` carries `problem_description` as scored (framing spliced),
  so `template().render()` == the scored prompt. Both were wrong before for every framed dataset.
- Test: `test_a_blank_answer_is_no_edit_at_either_boundary` (test_numerics § 9).

Still open: r1's winner pasted task_intent into instruction (CURRENT PROMPT shows the sentence
twice); L2 can move a panel into L1's own `thinking_style` slot, a name shared with the target field.

## Verification run 2026-09-25 — 3× justlogic-d234 in parallel ($0.15 cap each)

Campaigns `9943b8` (A), `464548` (B), `fbd663` (C); origin replayed, 42.5%. Rounds 1-3: every
variant carried a real edit in both slots; the blank-slot collapse did not recur. A's round-3 answer
came back all-no-op once; the parse guard caught it and the repair rung fired, as designed.

- **A crashed on a transport fault, fixed.** `ssl.SSLError: bad record mac` → `APIConnectionError`
  on the repair send. `_retry_wait` retried a 5xx and a connection never made, not one broken AFTER
  send, so one socket killed the campaign. `spend_book.connection_broke` (reset / TLS / protocol,
  never a timeout) now retries like a 5xx; the broken send stays held. Test rides the burst test in
  test_security. A resumed.
- **L2 layout shape mismatch, fixed at the listing.** CURRENT L1 LAYOUT listed slot → panels while
  the `l1_layout` edit is panel → slot; the schema-text fix did not hold for gpt-oss, which answered
  with slot names as keys and was refused. The listing now renders panel → slot, the edit's own shape.
- Every L2 fire so far picked `axis=thinking_style` and reported `l1_layout_voids_prefix` (still open,
  the name collision above).
- Log→campaign: A=`9943b8`, B=`fbd663`, C=`464548`. B elected r3-r5 (r4 C4.1 θ +0.037 vs parent -0.586,
  lift +0.62 ± 0.24), $0.084. C held all 5 rounds, $0.062.
- **Held-round caveat said "A winner was still elected"** on rounds that elected nobody. Reworded to
  state only what `_separability` knows (`l1/score/winner.py`).
- **Parent prior was named `R{last round}_winner`** even after a held round (C r5 compared against
  "R4_winner"; the parent was still the origin). Now named for the round that elected it
  (`l1/score/loop.py`).
- **A rescued parse failure left no trace of what it emitted** — only the errors. `schema_repair_errors`
  entries now carry the rejected content (`openai_compat._validation_summary`). The all-no-op first
  answer recurred at B r5 (repair failed too, clean re-ask rescued): 2 of ~15 generate calls; next
  run shows what the model actually wrote there.
- `index.json::best_accuracy` / `best_round` is a high-water over each round's MEASURED accuracy,
  including a held round's parent re-score on a new subset (C: best 55.9% at r4 with no winner).
  By design, but it reads as a winner's number.
- A finished after resume: elected r2-r5, $0.105. **r3 and r4 crowned arms whose raw θ sat BELOW the
  parent** (-0.061, -0.072) on the parent-selection-bias credit (+0.24, +0.22). Election law working
  as written, but a credit that size elects a regression; worth a look against `L4_FINGERPRINT_ROSTER`.
  Verification total: $0.25 for 3 × 5 rounds.

## Lineup runs 2026-09-25 (mercury `16bd65`, qwen `c20974`; gemma `66978b` paused for the harbor slot)
- **Election crowned a TIE on the credit alone:** "C3.1 won on θ lift +0.323 = +0.000 over the parent
  + 0.323 parent selection bias (θ +1.395 vs parent +1.395)". Same pattern as A r3/r4 (raw θ below the
  parent). The parent-selection-bias credit (~0.2-0.3) exceeds typical real lifts; decide whether the
  credit may promote an arm with zero or negative raw lift.
- `param_scope_discipline` dropped its `PARAM_UNLOCK_ROUND = 3` calendar floor (contract: no round
  thresholds in the loop); it scores only on mutation history (a prompt field unmutated two rounds).
- **FIXED — a tie or a trailing arm can no longer win.** `selection.py::elect_round_winner` now
  admits only on a positive RAW θ lift over the parent; the earned parent-selection-bias credit only
  reorders admitted arms. Test rewritten in `test_numerics.py` (the bar test). Trade-off accepted:
  the credit no longer lifts a slightly-trailing arm over an inflated parent bar.
