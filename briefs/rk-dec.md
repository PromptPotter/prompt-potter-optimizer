# rk-dec: one bar for crown, advance and stop; round 0 walked in its door (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `rk-dec`, arc `arc/rk-dec`.

- **Base:** `origin/land/wave-5` at `805feffd6`: v0.8.20 plus the symptoms batch, cfg-2 and rk-opt-10. Your `<base sha>` in common.md is this commit. Build on it, never beside it.
- **Spec:** the ledger section `### batch/26 · rk-dec` of `docs/specs/code-debt-cleanup.md`, every row. Also "`tests/test_identity.py` lays the spawn's arm beside its seat" (its rk-dec 3 landed at landing 50, so the row is yours).
- **This is a re-key window:** the walk reorders every leg's phases and the knobs sit in potter's hashed files. Decisions move on every leg once round 0 walks inside its door. Name each move and each digest move; none is a failure when your link names it.

## Decisions (orchestrator and operator; build them, do not reopen them)

- **One bar with a switch.** Crown, advance and stop read ONE predicate whose statistic is a switch. Every reading is built:
  - today's posterior bar, which stays the default until separability is read on banked runs (operator, 2026-11-12);
  - an exact paired sign test on the discordant cells, Holm-corrected across the round's arms;
  - an anytime-valid e-process on the paired discordance.

  - an empirical-Bayes posterior under the generator's measured lift prior N(μ0, τ²), with μ0 and τ estimated from the tenant's banked arm-vs-parent pairs. τ² is the observed variance of the paired lifts minus their mean noise variance, floored at 0. The arm's posterior mean is μ0 + τ²/(τ²+se²)·(x−μ0). Measured on 50 banked promotions: they claimed +0.63 θ and read −0.14 ± 0.16 on fresh rows, μ0 < 0 on all six datasets, τ ≈ 0 on four. This reading would have held 40 of them.

  Each reading names what a result quotes. `RoundResult.improved` reads the bar. The ceiling stop reads the pick's own one-sided, anytime-valid level bound. The advance reads the bar's switch.
- **Three race bugs, root-fixed with no switch.** A switch is only for a method worth ablating, like the bar's readings; these are defects (operator, 2026-11-13). All were measured on banked rounds, offline:
  - **An arm's cut depends on its slot.** `pobb.py::PoBBCheck.check` compares an arm with the parent plus every sibling decided before it, so the cut tightens by slot: 0.15 in slot 0, 0.33 in slot 4 (z=3.37), with arm quality flat and later cut arms +0.41 θ better. Every arm is now compared with the parent and every other arm at the checkpoint they share.
  - **The top-up has no stop rule.** `runner/round.py::measure_population` tops uncut arms up onto the origin panel through `_measure_gaps` with no eliminator. The round's eliminator is handed in, so the top-up is one more stretch of the same race, and `MemberRows.cut` comes from the walk. Replayed, this saved 8–33% of top-up rows, and the election held in 95 of 95 rounds.
  - **The origin panel ignores the walk.** `domain/origin_panel.py::origin_panel` takes the smallest slot ids among the origin's cells. It now takes the origin's first cells in walk order, so round 1's cold draw is the panel: about 30% of round 1's fresh cells, saved. Grep the readers of `overlap.positions` first: a resumed round must rebuild the same panel.
- **A noise walk is reported.** A campaign drifting on noise is a state the system enters but cannot report. Each round serves the generator's μ0, τ and promotion hold-up, as one projection over the round's own arm-vs-parent pairs. Here μ0 is the mean paired lift, and τ² is the observed variance minus the mean noise variance. Hold-up is a promotion's lift on the rows it met later as parent. Every entry point reads it on the existing round channel. This is L4's early-AUC instrument.
- **USD is the one matched axis**, named on `domain/spend.py::Budget` (operator, 2026-11-12).
- **One read per subject.** The spread reads the headline's own cells, and the election gets a one-arm read on its fit (the fake `{"read": rows}` arm goes).
- **Round 0 walks only inside its door.**
  - The origin is seated unmeasured, inherited (`origin.py::try_inherit_fork_origin`) or standing; it is walked in its door, and the criterion is locked at the close.
  - INIT comes before ORIGIN, and `establish_campaign_origin` goes.
  - An inherited origin is re-walked when another launch measured its rows.
  - `resumed_ending` keeps the operator's override.
- **The last round is decided once:** `target_tripped` joins `standing_tripped` in `termination.py`, and `post_round` and the adapter skip read only that.
- **`refuse_pipeline(schema)` at `Cycle.start`**, and the three `refuse_origin` hunks go. `bench/difficulty.py` reads the session's fields, and `require_scorer` / `require_partition` go.
- **Three knobs go, their shipped behaviour hard-wired:** `pobb.lock_in` (off), `origin_gate: critical_only` (`strict` and `off` stay) and `convergence_patience` (`lives` stays) (operator, 2026-11-12).
- **The `_l4_arm` journey** drives `_open_seat` over an offline inner campaign.

## Links (one commit each, in order)

1. The walk: round 0 in its door, the fork's holes, the last round decided once, `refuse_pipeline`, the difficulty accessors.
2. The bar: the predicate with its switch, `improved`, the stop and the advance, the spread, the one-arm read, USD on `Budget`.
3. The `_l4_arm` journey.
4. The three knobs.
5. The race's three bugs: a slot-symmetric cut, a raced top-up, the origin panel in walk order.
6. The noise-walk readout: μ0, τ and hold-up per round, served.

## Beside you

- **life-2b** (cloud, running now) shares two files, each by hunk:
  - `application/runner/mint.py`: yours is the origin's door, where `establish_campaign_origin` goes. life-2b's are `open_launch`'s money move, plus `onto_fork` and `_rebase_held` going async.
  - `application/runner/entry.py`: yours are round 0's door and the last round decided once. life-2b's are `_mint_and_rebase_fork` going async and `_cut_rebase_fork`.

  Edit only your hunks and name each in the commit body.
- **Main also holds an unpushed commit** touching `application/health.py`, `deploy-linux/update.sh`, `scripts/gate.py::_install_digest`, the end of `tests/test_security.py`, `docs/research/external-constraints.md`, `docs/specs/roadmap.md` § Optimizer tuning and the ledger's `batch/31 · box` row. Leave those hunks alone.

## Proof (beyond common.md)

- Equals-main: name each leg's decision move by link. Links 1, 4 and 5 move decisions. Links 2 and 6 must not, since the bar's default is today's and a readout decides nothing.
- Link 5, on `scripts/offline_run.py`; a miss is a stop, not a tune:
  - with equal arms, the cut share by slot is flat (today it is monotone);
  - a planted better arm at a random slot is cut no more often than today's average over slots;
  - the elected arm moves in at most about 2% of rounds, while the top-up spends at least 5% fewer cells.
- Each row's Re-test, before and after. The crown row's: a tenant-ruler offline run's `rounds_to_separable` beside `rounds_to_improved`.
