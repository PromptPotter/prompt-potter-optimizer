# valid: one validity check, one mint writer, one way onto a fork, a copy that revalidates (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `valid`, arc `arc/valid`.

- **Base:** `origin/main` at `5cf502c7b659e69aff6a308340d727e79a459f5d`, main's tip with rk-dec and rk-dec-2 landed (the race and the members' replay moved there: re-read them). Your `<base sha>` in common.md is this commit. Build on it, never beside it.
- **The code shrinks:** the chain's net over `promptpotter/` is negative (target ≤ −200; link 1 alone ≈ −250). `git diff --shortstat <base> HEAD -- promptpotter` after each link; a link that leaves the chain net positive gets one rebuild, then the chain stops there.
- **Spec:** the ledger section `### valid` of `docs/specs/code-debt-cleanup.md`, every row. Each row's line numbers predate rk-opt-10, rk-dec and life-2b: grep the named symbol, never trust the line.
- **This is a re-key window:** `Cycle.rng` moves onto `run_seed`, so every leg's draws move. Name each move and each digest move; none is a failure when your link names it.

## Decisions (orchestrator; build them, do not reopen them)

- **One validity check.** `resume.py::_divergence` becomes `ab_replay`'s verdict, walked by `mask/divergence.py::find_divergences`. The members' own `replay()` checks (capo, gepa, levi, the election, the race), `jobs/mint.py`'s defaults drift and `round_packages` / `rederive` read that one verdict, or go. A resume and a fork ask the same question once. Name the round a fork is now cut at wherever it moves.
- **`RunMode` is one `on_divergence: halt | accept | fork`**, not four bools. `--no-check` is `accept` and `--fork-on-divergence` is `fork`; the CLI flags map onto it at the door and nowhere else. `ForkCyclePayload` refuses `mode.from_round`, so `resume --rewind N --from M` refuses typed instead of cutting a fork and dying.
- **One seed.** `Cycle.rng` keys on `run_seed`; the campaign id and the clamp seed stop seeding it, and L4's bridge in `runner/inner/tasks.py` goes.
- **One mint writer.** `store.py::mint_cycle` writes the cycle's partition, seed, resolved run and `CycleMintedRecord` as one fact, after the mint, as `run_records.py`'s docstring says. Every multi-append in the ledger row ("A mint or a fork lands as several appends") becomes one append per fact. The manifest names the draft it adopted and `draft_ids` skips it. A companion file (`resolution.json` beside `draft.json`, the definition's framing and origin) carries the digest it was computed against, and a reader refuses a mismatch. The ledger's on-disk form changes: run data is wiped at the landing, never read in two shapes. Name it.
- **One way onto a fork.** The mid-INIT rebind, `entry.py::_run_line`'s rebase and an operator relaunch all lay one `ForkCyclePayload` through `mint.py::onto_fork`. `fork_siblings.py::cut_operator_fork` seeds the fork's prompts AND params from a named candidate, so a REST fork runs X's prompts under X's params. The CLI's two own rules (no rewind with steer, its own default reason) go, and its `--steer` lays the whole payload. `HeldRun` carries the admitted `RunMode`, so the CLI re-derives none for an inline run. A served act carries the payload it fills: the browser's "Resume run" and the `arm_gate: author` hold send what `phases.py` and `activity.py` serve, and the webapp copies none of it.
- **A copy revalidates.** `domain/strict_model.py::StrictModel.model_copy` revalidates whenever `update` is given and refuses an unknown key. A row `model_copy_updates` of `scripts/complexity_ledger.py`'s `BASELINE` (the gate's `surface-ledger`) holds the `model_copy(update=` count (105 sites in 56 files at main 1c8b006ab) where it stands, so it can only fall; no separate probe. Time the offline run before and after, and name any rise over 5%.

## Links (one commit each, in order)

1. One validity check, `on_divergence`, `ForkCyclePayload` refusing `from_round`, and one seed (the re-key).
2. One mint writer, one append per fact, one way onto a fork, and each door forking the same way, webapp included.
3. `StrictModel.model_copy` revalidates, and the `model_copy_updates` row.

## Beside you

No other code chain runs beside you. The gate's redesign (`scripts/gate.py`, `last_green.py`, `processes.py`, `offline_run.py`) and a `package_lines` row in `scripts/complexity_ledger.py`'s `BASELINE` land on main while you run; your `model_copy_updates` row shares that dict by hunk, and the orchestrator merges it at your landing.

## Proof (beyond common.md)

- Equals-main: link 1 moves every leg's draws (the seed). Name each leg's move. Links 2 and 3 move no decision, only the ledger's form (link 2).
- `scripts/gate.py` in full, `playwright` included, green at the tip: link 2 changes what the browser sends.
- Each row's Re-test, before and after. Add these:
  - `resume --rewind 2 --from 1` refuses typed, with no fork cut;
  - a REST fork naming candidate X runs X's prompts;
  - a torn fork (killed between its appends, simulated) resumes on its own rounds, never its parent's;
  - a copy with a stale key, or a state its validator refuses, raises.
