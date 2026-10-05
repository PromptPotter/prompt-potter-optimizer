# Where the wall-clock went — justlogic-d234, 2026-09-24 livestream

Source: `.promptpotter/projects/197ee2cf2aea7b14/campaigns/justlogic-d234__{4d70b3,458037,1a50b9,763db8,c3e73f,ec354d,37b160}`,
each cycle's `.runtime/ledger.jsonl` (full physical scan, all launches — NOT `index.json::final.wall_clock`,
which per `docs/operations/observability.md` is only the LAST launch's clock and undercounts a resumed
cycle badly: e.g. 4d70b3's index reports 12.4s elapsed when the ledger shows 1588s). Phase brackets
computed with the same enter/exit pairing algorithm as `ledger_scan.py::_phase_seconds` /
`_gate_seconds`, over the whole ledger. Method verified: recomputed average concurrency independently
matches the known-serial run (4d70b3) to 1.08x.

Every campaign below started between 14:23 and 16:22 on 2026-09-24 (`.promptpotter/projects/.../campaigns/justlogic-d234__*`).

## 1. Total wall-clock + phase breakdown, all 7 campaigns

| Campaign | Model | Stop reason | Span | init | origin | l1_generate | l1_score | refine_strategy(L2) | modify_plan(L3) | gate_s | unattributed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 4d70b3 | gpt-oss-20b:nitro | max_rounds(6) | 1588.0s | 6.9s (0.4%) | 60.0s (3.8%) | 414.4s (26.1%) | 508.9s (32.0%) | 102.0s (6.4%) | 26.6s (1.7%) | 0.0s | 469.2s (29.5%) |
| 458037 | qwen3.7-flash | max_rounds(6) | 2206.9s | 1.4s (0.1%) | 136.4s (6.2%) | 404.7s (18.3%) | 851.6s (38.6%) | 116.3s (5.3%) | 19.2s (0.9%) | 0.0s | 677.3s (30.7%) |
| 1a50b9 | mercury-2.5 | l3_patience_exhausted(7) | 1960.3s | 34.0s (1.7%) | 77.2s (3.9%) | 515.5s (26.3%) | 231.6s (11.8%) | 80.2s (4.1%) | 25.5s (1.3%) | 0.0s | 996.3s (50.8%) |
| 763db8 | (origin only) | abandoned at gate | 394.1s | 5.7s | 12.3s | – | – | – | – | 331.6s (84.1%) | 44.4s |
| c3e73f | (origin only) | abandoned at gate | 395.8s | 7.3s | 5.6s | – | – | – | – | 361.6s (91.4%) | 21.3s |
| ec354d | (checkin+origin) | paused at gate | 2765.0s | 16.3s | 473.9s (17.1%) | – | – | – | – | 1798.6s (65.0%) | 476.2s (17.2%, incl. ~16 min of checkin-form editing before origin even started) |
| 37b160 | google/gemma-4-26b-a4b-it | **backend_unreachable** | 3935.1s | 29.6s | 35.9s | – | – | – | – | 157.7s | **3711.9s (94.3%)** — dominated by repeated dead-model retries, see §5 |

`l1_score` = candidate/cell scoring for ALL rounds (origin's round-0 scoring is the separate `origin` bracket). `l1_generate`/`refine_strategy`(L2)/`modify_plan`(L3) are optimizer-only brackets; `l1_critique` has no bracket of its own and is folded into `l1_generate`'s wall time in the ledger's phase events but billed separately in `token_usage` (see §4).

**Total wall-clock burned across all 7 campaigns today: 13,245s ≈ 220.8 minutes (3.7 hours).** Only 3 of 7 ever produced a scored round beyond origin; the other 4 (59% of them) spent 3555s (59.3 min) reaching, at best, one origin scoring pass before being abandoned or dying.

## 2. Per-cell (backend) latency, cache hits, errors

| Campaign | live cells | cached cells | cache-hit rate | median | p90 | max | errors |
|---|---|---|---|---|---|---|---|
| 4d70b3 | 404 | 194 | 32.4% | 1.41s | 2.42s | 7.60s | 0 |
| 458037 | 583 | 200 | 25.5% | 8.94s | 18.47s | 46.00s | 0 |
| 1a50b9 | 551 | 380 | 40.8% | 1.67s | 2.81s | 7.63s | 1 (`UNKNOWN`, negligible) |
| ec354d | 40 | 0 | 0% | 8.18s | 30.15s | 60.29s | 1 `UNSCOREABLE` (422 json_parse_failed) |
| 37b160 | 16 | 0 | 0% | 60.02s | 60.06s | 60.53s | **12/16 (75%)** — 4 `UNSCOREABLE` (422 json_parse_failed on `poolside/laguna-xs-2.1`), 8 `SERVER` (503 "LLM request timeout" on `google/gemma-4-26b-a4b-it`) |

The three finished campaigns (4d70b3/458037/1a50b9) had essentially **zero retry/timeout cost** — 0-1 errored cells out of 530-931. Retries are not what made those three slow. 37b160 is the opposite extreme: every live cell but 4 failed, each burning a flat ~60.0-60.5s (near-identical durations = a fixed provider-side timeout, not organic variance) before erroring.

## 3. Concurrency achieved vs configured

| Campaign | `sample_lookahead` requested | Peak concurrent cells (measured from `sample_started`→`sample_scored` intervals) | Avg concurrency during scoring (worked_s ÷ scoring-phase wall-s) |
|---|---|---|---|
| 4d70b3 | **1** (never raised — no `.runtime/sample_lookahead.json`) | 1 | **1.08x** (fully serial) |
| 458037 | 16 (`auto`) | 34 | **6.3x** — well under both the peak and the request |
| 1a50b9 | last request `cells:1` (`auto`, throttled down mid-run from an earlier higher setting) | 34 | **4.6x** |
| 37b160 | **1** (never raised) | 1 | 1.0x (moot — died at cell #17) |

Peak concurrency (34) is a brief burst, not sustained throughput — average utilization on the two parallel runs is only 13-18% of the observed peak. This matches the already-known constraint in project memory (`project_cell_reservation_pins_concurrency`): the spend cap reserves each cell's *worst-case* cost, so the effective concurrency the reservation ceiling admits is well below the requested `sample_lookahead` once real spend accrues — not a new finding, but it is what explains why 458037/1a50b9 didn't scale anywhere near 16-34x even though they were configured to.

## 4. Optimizer calls (L1 generate + L1 critique + L2 + L3, all on `deepseek/deepseek-v4-flash:nitro`)

| Campaign | n calls | worked_s | **share of total wall-clock** | median | p90 | max | reasoning-tokens median |
|---|---|---|---|---|---|---|---|
| 4d70b3 | 14 | 820.6s | **51.7%** | 47.2s | 92.2s | 128.0s | 3556 |
| 458037 | 15 | 1105.0s | **50.1%** | 77.8s | 128.3s | 132.2s | 6556 |
| 1a50b9 | 18 | 1008.7s | **51.5%** | 51.3s | 100.1s | 121.5s | 4896 |

**Remarkably consistent: ~50-52% of every finished campaign's wall-clock is optimizer calls, independent of which backend model or dataset config was under test** — because the optimizer model/effort is a fixed install-global (`promptpotter/assets/optimizer/pipeline.yaml`: `deepseek/deepseek-v4-flash:nitro`, `l1_generate` pinned at `reasoning_effort: high`, l1_critique/l2/l3 at `medium`). `l1_generate` alone (the `high`-effort call) is the single largest bracket in 2 of 3 runs (404-515s). Per-call latency is driven almost entirely by hidden reasoning tokens (median 3.5k-6.6k) — matches `project_optimizer_call_is_mostly_reasoning.md`.

## Top 5 wall-clock sinks, ranked by measured seconds

1. **A dead backend model retried for 65 minutes — 3,365.9s (85.5% of that run's span), campaign 37b160.** `google/gemma-4-26b-a4b-it` returned an identical `503 LLM request timeout` on every attempt (8 timestamped hits at 15:31→16:18, ~60.0-60.5s each), plus 4 earlier `422 json_parse_failed` on a second model (`poolside/laguna-xs-2.1`) — this campaign was clearly a live model bake-off. Structural cause: `backend.py::BackendClient.run_query`'s 5xx path (`_classify_http_error`, exponential-backoff loop) has no circuit breaker across repeated `resume` attempts on the *same* dead node, and the terminal message ("restore the backend or the network it needs") never names the failing model — inviting another blind `resume`. **OPEN.** The in-tree `BACKEND_OUTAGE_S=600` fix (`_reachable_within`, `backend.py`) covers *connection-never-made* errors only, not this 503/"request timeout" shape — it would not have shortened this incident. Fix: surface the specific `model`/`provider` in the `backend_unreachable` stop reason, and stop after N consecutive identical failures instead of re-inviting `resume`.

2. **Optimizer LLM calls, every run, ~50% of wall-clock — 2,934.3s summed across the 3 finished campaigns (820.6+1105.0+1008.7s).** Fixed to `deepseek/deepseek-v4-flash:nitro`; `l1_generate` runs at `reasoning_effort: high` unconditionally. This is the single biggest *recurring* structural lever — bigger in aggregate than any one candidate-scoring phase, and untouched by any of today's fixes. **OPEN** — matches `project_optimizer_call_is_mostly_reasoning.md` / `project_l1gen_creativity_redesign.md` (untested lever = input size; NEXT = draft the axes). Fix candidate: a lower `reasoning_effort` rung for `l1_generate` on interactive/livestream campaigns, and/or trimming the optimizer-prompt input mass that the reasoning is proportional to (`docs/developer/dispatch-hub.md`'s field catalogue).

3. **Fully-serial scoring on two of seven runs — ~510-570s recoverable, 4d70b3 (measured, avg concurrency 1.08x) + 37b160 (moot, died first).** No `.runtime/sample_lookahead.json` was ever written for either — both stayed at the code default `max_cells_in_flight: int = 1` (`infrastructure/projections/live_dashboard/state.py:290`). This is **declared** behavior, not a bug (`promptpotter/presentation/CLAUDE.md` § Sample look-ahead: browser-only, absence is the boundary), but it is the single cheapest, fully-in-the-operator's-hands lever that went unused on 2 of 7 runs today: sibling runs that did raise it (458037, 1a50b9) hit 34x peak concurrency. Fix: raise sample-lookahead (webapp slider / `set-sample-lookahead`) immediately after every Start, before round 0 scores — a stream-practice fix, no code change.

4. **Parked/abandoned campaigns — ~3,555s (59.3 min) spent reaching at most one origin score.** 763db8 (394.1s, 84.1% at the origin gate), c3e73f (395.8s, 91.4% at gate), ec354d (2765.0s: ~16 min of checkin-form editing with 5 retried `start-checkin` commands, then 473.9s origin scoring, then **1,798.6s / 30 min sitting at the origin gate** waiting on an operator decision). `gate_s` is deliberately the one HUMAN leg (`docs/operations/observability.md`) and is not itself fixable in code, but minting 4-5 origins in parallel during the same 8-minute window (all created 15:09-15:17) and then working through their gates serially is what turned "compare some models" into 59 minutes of stream time for near-zero scored data. **Not a code fix** — a stream-practice fix (answer/kill gates before minting the next comparison origin).

5. **Unattributed wall-clock with no phase bracket — 469-996s per finished run (29.5%, 30.7%, and 50.8% of span).** No `CampaignPhase` bracket exists for round-close bookkeeping (election, PoBB backfill, dashboard/ledger flush) between one round's `l1_score` exit and the next round's `l1_generate` enter, so a fifth to a half of every run's clock is invisible even to `scan_ledger_wall_clock` itself — 1a50b9's 50.8% is the largest of the three and the least explained. **OPEN**, and it's an observability gap before it's a speed problem: per the repo's own doctrine, a cost nothing reads is not tracked, so this can't yet be prioritized against items 1-4 with real numbers. Fix: add an `ELECTION`/round-close bracket to `CampaignPhase` (`domain/phases.py`) so the next investigation doesn't have to reconstruct this by subtraction.

## Already fixed today (in the working tree, uncommitted) vs still open

**Fixed, confirmed in `git diff`:**
- 422 `json_parse_failed`/`schema_validation_failed`/`output_truncated` no longer aborts the cell as a hard client error — reclassified `UNSCOREABLE` (`sample_measurement.py::_classify_http_error`, `_OUTPUT_FAILURE_CODES`).
- `samplescan` deleted from `STALE_DATA_LOAD_PROTOCOL` (`sample_measurement.py`).
- Replay-ladder now re-sends only on a fatal code (`needs_rerun`/`classify_result(...).is_fatal`), not on any pipeline warning — no longer discards a gradeable cached row over an advisory (`sample_measurement.py`, `query_loop.py`).
- 600s outage wait: a connection-never-made error now polls `GET /status` for up to `BACKEND_OUTAGE_S=600` before declaring `backend_unreachable`, instead of ~15s of exp backoff (`backend.py::_reachable_within`). **Does not cover the 503 "LLM request timeout" shape that actually sank 37b160 (#1 above) — that gap is still open.**
- `origin-gate` CLI verb exists (`cli/commands/lifecycle.py::cmd_origin_gate`) — but note ec354d/763db8/c3e73f were all still answered through gate waits, i.e. this verb wasn't what closed today's gate time.
- Reasoning-effort floor per model: a dataset no longer hardcodes one `reasoning_effort` rung; `pipeline_resolve.py::_apply_model_floors` floors it per the model actually running the node.
- Lean justlogic origin: `datasets/justlogic-d234/pipeline.yaml` dropped the verbose reasoning-trace instruction and `reasoning_effort: low` pin; C0's median tokens dropped 1546→692 (55%), which is consistent with 4d70b3's fast per-cell median (1.41s).

**Still open (from this data):**
1. No circuit breaker / model-naming on repeated `backend_unreachable`/SERVER retries (top sink, #1).
2. Optimizer `reasoning_effort`/input-mass on `l1_generate` untouched (#2) — the single biggest recurring lever.
3. `sample_lookahead` still defaults to serial with no CLI/launch-time knob (#3, declared-by-design but still the cheapest lever left unpulled twice today).
4. No round-close phase bracket, so 30-51% of every run's clock is unattributed (#5).
