# Tests, gate, CI: where the seconds go (2026-11-27, `42bd9ca42`, Windows desk, 16 logical cores)

One run per number; free RAM 1.0–2.0 GB of 16 GB. Everything timed ran before the `wt-land-w7` gate (20:35), except
the file-op probe (D).

## Measured

**A. One commit, desk vs CI.** Desk: the operator's gate. CI: run 46050489497 (4-core Linux), the gate's per-check
table in `gh run view --log`.

| check | desk | CI | × |
|---|---|---|---|
| pytest | 636 s | 70.6 s | 9.0 |
| offline-run (legs' span, the 18:46 run) | 753 s | 81.3 s | 9.3 |
| offline-run-controlled | 223 s | 22.5 s | 9.9 |
| optimizer-plugin · walk-world · vitest · eslint | 99 · 90 · 86 · 36 s | 14.4 · 18.6 · 18.2 · 10.7 s | 6.9 · 4.8 · 4.7 · 3.4 |
| playwright | 429 s | 184.6 s | 2.3 |
| surface-ledger (CPU-bound) | 41 s | 22.8 s | 1.8 |
| gate wall | 1,308 s | 273 s (`--py` 157.7 s and `--web` 203.2 s, parallel jobs) | 4.8 |

**B. pytest on the desk, HEAD vs `7dce00a96`**, both green: `.venv/Scripts/python.exe -m pytest -q -p no:randomly
--durations=0 -x --junitxml=…`, gate env (temp `PROMPTPOTTER_HOME`, `PYTHONPATH` pinned), serial (no xdist). Old tree:
`git archive` into `%TEMP%`, same venv (the lock differs only in its version).

| file | 7dce00a96: tests, s | HEAD: tests, s | Δ s |
|---|---|---|---|
| test_archive | 60, 29.3 | 72, 156.1 | +126.8 |
| test_scoring | 116, 87.9 | 140, 176.7 | +88.8 |
| test_security | 161, 106.5 | 141, 180.0 | +73.5 |
| test_spend | 138, 95.0 | 134, 135.1 | +40.1 |
| test_identity | 128, 76.4 | 142, 108.4 | +32.0 |
| other four files | 38, 21.3 | 44, 37.2 | +15.9 |
| sum of test phases (setup / call) | 641, 416.4 (51.3 / 357.6) | 673, 793.6 (297.7 / 479.0) | +377 |
| **wall** | **450** | **832** (636 inside the gate) | |

Tests in both trees: 471, 248.2 → 533.9 s. Tests over 2 s: 35 → 65. HEAD's CPU at 567 s: 220 s user, 179 s kernel.
HEAD's top 10, s: h2h arms controlled 43.0 · budget-cut table 36.6 · fork closes the axis 34.5 · cell banked under one
formula 30.0 · queued start 24.7 · round file the disk refuses 20.7 · L4 generator 18.2 · stamp compacts 18.0 · host
over stdio 17.7 (17.6 on the old tree) · dspy child + cold start 17.3.

**C. HEAD by fixture** (`pytest --setup-plan`, joined to the durations).

| class | tests | s | share |
|---|---|---|---|
| reads one of 11 module-shared world runs | 102 | 382.2 (setup 224.5) | 48% |
| takes `offline`, no shared run | 249 | 307.0 | 39% |
| pure, stores only | 296 | 73.6 | 9% |
| dspy child | 6 | 17.3 | 2% |

Shared runs are paid in their first fact's setup. Desk s (the docstring's Linux figure) × facts: head_to_head 40.8
(3.6) ×4 · steered_line 34.0 (2.7) ×7 · one_round 24.2 (1.7) ×28 · line 23.0 (2.4) ×32 · l4_outer 15.6 (2.2) ×3 ·
origin_forks 15.1 (1.6) ×2 · family_tree 12.2 (1.7) ×1 · displaced 11.8 (1.6) ×11 · ran_24 9.6 (0.5) ×20 ·
banked_line 6.7 (0.6) ×4 · gated 2.3 (0.3) ×2. In all, 195.3 s against 18.9 s claimed (10.3×). Setup per fact: an
`offline` fact 189 ms, a pure fact 17 ms. `--collect-only` alone takes 24.1 s, and `gate.py:1542` runs it before the
suite. The hand-seated share cannot be separated: its 27 sites (§3) sit among the pure and `offline` classes.

**D. Desk primitives** (`%TEMP%` on NTFS; Defender real-time is on, and its exclusions need admin to read). Across two
runs (`copybench.py`, `fsbench.py`), against WSL Ubuntu's ext4 on the same box (`fsbench.py`, `/tmp` and `~`):

| operation | NTFS | ext4 | × |
|---|---|---|---|
| create, write and close a file | 0.69–2.50 ms | 0.054–0.056 ms | 12–45 |
| `copytree` of 1,000 small files, per file (beside a gate) | 1.16 ms | 0.082–0.090 ms | ~13 |
| with `fsync` | 1.7–3.7 ms | 2.9–3.6 ms | ~1 |

A bare python spawn takes 102 ms. The engine A/B: `offline_run.py --optimizer=potter --straight` on a fresh home reads
"wall clock" 63 s at HEAD and 69 s on `7dce00a96`.

**E. offline-run's legs** (log timestamps, 18:46:19–18:58:52): 59 children, 5,680 child-seconds, 753 s span, 8 at once.
By optimizer: potter 2,339 · capo 976 · capo-pobb 941 · levi 823 · gepa 600. By family: warm/pressed/resumed/
pressed-resumed ×5 1,701 · skipped pair 920 · crossed ×3 582 · straight 577 · rebased 411 (the longest single child) ·
every-adoption 375 · wounded 241 · nine others 60–154 each. In the `42bd9ca42` gate: controlled 207 s beside
controlled-bare 154 s; walk-world's straight run 50 s plus L4 28 s.

**F. CI over time, s.** The desk's v0.8.21 gate took 737 s, pytest 315 s (`roadmap.md:274`).

| commit | pytest | offline-run | playwright | `--py` | `--web` |
|---|---|---|---|---|---|
| `5d150ac13`, before the rework | 95.6 | 76.8 | 144.6 | 179.2 | 170.2 |
| `c7c867197`, v0.8.21 | 75.8 | 77.4 | 165.1 | 160.9 | 185.9 |
| `2c7dff34e` | 60.5 | 54.2 | 153.2 | 120.2 | 175.8 |
| `f73c3cb84`, slow runner | 109.6 | 128.7 | 185.3 | 247.5 | 204.7 |
| `42bd9ca42` | 70.6 | 81.3 | 184.6 | 157.7 | 203.2 |

**G. Playwright.** `playwright test --list` counts 188 tests: cold 16, walk 86, touch 86. touch reruns all 86 walk
tests at 390 px, one worker; `responsive.spec.ts` holds 37 per project. **Per-spec seconds: a gap.** The gate keeps
playwright's output only on failure, CI's `github` reporter prints no durations, and `webapp/test-results/` dates
from Nov 25. The rerun was stopped because it competed with a gate.

## 3 · Is the rework complete? No.

**Landed:** `world.py`'s doors with `shared_run` / `adopt` / `reading`; `seat` is gone and every fact opens through
run init (`f812c4f0c`); dspy runs in one child and the cold start is paid once; the charter's probes read 0. New legs:
warm, pressed, resumed, pressed-resumed, arrived, crossed ×3, skipped ×2, every-adoption, bench-paused. Parked facts
23 → 19; tests/ 30,451 → 27,226 lines (+9,643 / −12,817); walk-world left CI's `check` from `f73c3cb84` on.

**Half-done, two mechanisms side by side.** Three calls the area-14 re-tests want at 0 stand: `write_resolved_run(`
at `test_identity.py:3183` and `:3244`, `ForkCut(` at `test_security.py:2650`, and the `execute_round` patch at
`test_spend.py:1004`. `test_scoring.py` drives `execute_round` / `post_round` by hand at 8 lines (4681–5421), against
the charter's "a journey is never replayed by hand". 27 ledger records are appended by hand, untriaged, e.g.
`RunPhaseRecord(Running())` at `test_security.py:2265`, `:2701`, `:3703`, `:3767`, `:4179`. 19 parked facts are kept
beside the leg that nearly carries them (S8 beside bench-paused, S9/S10/S12 beside replicated, S17/S18 beside
every-adoption).

Proven by a leg and a test (bodies read): a fork takes an arm out of its head-to-head (`offline_run.py:1659–1675`,
`test_an_act_on_an_arm_takes_it_out_of_its_head_to_head`, +REWIND/SKIP); an arm remembers only its walks (`:1614–1635`,
`test_a_controlled_arm_remembers_only_the_answers_its_campaign_walked`); the head-to-head table, which the leg computes
and only prints (`:1649–1652`) and three pytest facts assert on a 40.8 s run of their own.

**Never started:** "a guard is tested once over its doors"; on wave 6: the atomic operator act, the capability-menu
door, the knob tiers, archive under 8 s; on tests-w or area 14's chain: the wounded legs under load, the capo skip twin,
`built_key` absent mid-gate, `--changed` on a clean tree. No chain timed the suite on the desk: every wall recorded
(62–90 s) falls in CI's Linux range (inference).

## 4 · Why performance did not improve

**Where the repo measured it (Linux), a small net cost:** `7dce00a96` 62.0–67.0 s against tw-v's tip 65.2–67.9 s,
archive 3.4 → 16.4–19.4 s (row 805); w7-tests then went 77.8 → 87 s (Smells row). CI is flat within its runners' noise
(F). **On the desk, +382 s:** setup 51 → 298 s, suite 450 → 832 s (636 in the gate); the 471 facts in both trees 248 →
534 s (fork closes the axis 0.3 → 34.5, queued start 0.2 → 24.7). Archive is about 5× on both: one mechanism.

**The mechanism.** Hand-seated records, a few file writes each, were traded for real runs through run init: 11 shared
world runs (195 s on the desk) plus a home for every fact. On this box a file create costs 12–45× more on NTFS than on
ext4, while `fsync` costs about the same (D), and 45% of pytest's CPU is kernel time. It is not the engine (63 s
against 69 s) and not the CPU (surface-ledger runs at 1.8× CI). **Unseparated:** how much of the desk gate's
315 → 636 s since v0.8.21 is today's `f812c4f0c` (the same mechanism, on every fact) and how much is low free RAM.

## 5 · Deletion-first, ranked by desk seconds

The cuts are disjoint. "Spent" is measured; "saves" is an inference.

0. **The root, not a deletion: the desk's per-file cost** (A, D). Every engine check runs 5–10× slower than on CI.
   The operator's step is a Defender exclusion or a Dev Drive for the clone, `.gate_cache` and `%TEMP%\pp-*`, or the
   gate run on WSL ext4. Inference: it saves toward CI's 70.6 s pytest. Cutting `fsync` would not help (D).
1. **Slow offline facts: 190.8 s spent** by 21 facts over 3 s; the 16 that exist in the old tree take 142.3 s now
   against 53.0 s there. The largest: budget-cut table 36.6 (13.3 on the old tree, filed) · round file the disk
   refuses 20.7 · inner-run stop 12.9 (parked) · bench grades the pick 9.0 · every-adoption set-aside 7.1 · refused
   poll 6.9 (0.2 on the old tree). Root, by the charter's tiers: a rule reached only through a round becomes a pure
   function, a table at 17 ms a fact.
2. **Shared runs serving ≤7 facts: 90.4 s spent.** head_to_head 40.8: the controlled leg already computes its table,
   though it needs gepa as a third arm to carry the asserts. steered_line 34.0 · l4_outer 15.6. Ruled out:
   origin_forks 15.1 and family_tree 12.2 belong to archive, where "a cheaper run, not fewer" holds (debt row 805).
3. **Collection twice per gate: 24.1 s spent** (`gate.py:1542`). An upper bound, since the suite then collects warm.
4. **Potter's straight run built twice** (inference: same argv, not diffed) when both checks run: 50 s spent. The cut
   nests `--l4` into a copy of offline-run's `potter/data`; `roadmap.md:278` refuted sharing the L4 run, not this one.
5. **touch reruns every walk test at phone width:** 86 of the 188 tests. Its seconds are a gap (G).

**Smaller.** The two leg-and-test pairs: 2.5 s and 1.7 s of call. CI only: `42bd9ca42` was gated on its PR
(46049759856) and again on its push to main (46050489497), 273 s of runner wall each, and offline-run reruns on every
push (81.3 s) because CI keeps no greens. Not measured, so not ranked: "one guard per door" (security, identity).
