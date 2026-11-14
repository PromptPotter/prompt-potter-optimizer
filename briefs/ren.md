# ren: one word per thing — the filed renames, every twice-defined class, a check that keeps it (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `ren`, arc `arc/ren`.

- **Base:** `origin/main` at `88af3a62d`: main with faults-1, coupon and narr-3 landed (80). ren runs ALONE on the tip: a rename touches every chain's files.
- **The code shrinks:** a rename is line-neutral, so the chain's shrink comes from the folds below. Expect about −20 over `promptpotter/`. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link, and judge it as common.md says.
- **Spec:** the ledger section `### batch/32 · ren`, every row but "Node config is one document" (it rides with cfg, not you). Re-test each row at your base: rec, readings and la land before you and may have moved a name.
- **Every rename lands with its doc and `CLAUDE.md` row in the same commit.** Its re-test is the old word's `git grep -c` for that sense, which must be 0. Grep the new name for collisions before you use it.

## Survey at f589cbe9a (re-derive every count at your base)

- Every filed row still stands:
  - `overshoot` appears 3× in `heartbeat.py`;
  - the six filed class pairs are all still twice-defined;
  - `read_treatment` 4×, `needs_export` 3×;
  - `candidate_library` 73× in 26 files, and `PoolSource.LIBRARY` 6×;
  - `HierarchyCourse` 52× in 21 files, `"course"` 27× in 18, and `courseAddressOf` 8×;
  - `LinePayload|on_line|LineRun` 44× in 13 files;
  - `ArmSlot|slot_of` 53× in 19;
  - `CycleSeed|ForkSeed` 39× in 13;
  - `_DETACHED` 7× in 2 files;
  - both `knobs.py` and both `free_tier.py`.
- **Ten more top-level class names are defined twice, and the ledger missed them:**
  - `Refused` (`maintenance/landing.py`, `domain/arms.py`);
  - `Layer` (`potter/escalation.py`, `domain/layer_fold.py`);
  - `Folded` (`layer_fold.py`, `store/data_root.py`);
  - `CycleHold` (`run_phase_control.py`, `ledger_scan.py`);
  - `_Standing` ×3 (`health.py`, `served_dashboard.py`, `ledger_scan.py`);
  - `_Read` (`hierarchy_subtree.py`, `config/prompt_blocks.py`);
  - `_Moved` (`archive_maintenance.py`, `command_log.py`);
  - `_Graded` (`evidence/head_to_head.py`, `runner/bench.py`);
  - `_CycleFacts` (`evidence/read.py`, `ledger_scan.py`);
  - `_CellSends` (`connectors/dspy_module.py`, `infrastructure/backend.py`).

  Count them yourself: `git grep -hoE "^class \w+" -- 'promptpotter/*.py' | sort | uniq -d`.

## Decisions (orchestrator; build them, do not reopen them)

- **The check is a row of `scripts/complexity_ledger.py`**, not a new gate `Check`. The row counts the top-level class names defined twice in `promptpotter/`, private ones included, and its baseline is 0. It rides the `surface-ledger` channel that already exists, so a rise fails there.
- **A duplicate pair is either renamed or folded.**
  - Two names for different contents: each is renamed to what it holds.
  - Two classes of the same shape for one fact: they fold into one owner, and the fold is where the shrink comes from. Look hardest at `_Standing` ×3, `_CellSends`, `_CycleFacts` and `CycleHold`; `ledger_scan.py` appears in four pairs.
  - The knobs and free-tier module pairs follow the same rule. payer's census reads the free-tier pair first.
- **The words are the ledger's:**
  - `answer_terms` and `PACKS`;
  - "cycle" for "course" in the served and doc layer;
  - `live_cycle`;
  - `RoundSamples`, `ArmSamples` and `inner_panel`;
  - prompt field, region, `ArmPosition` and `position_of`;
  - `CycleStart*` and `ForkStart`;
  - `entry_kind`;
  - `lateness`.

  A collision your grep finds is the one reason to pick another word. Name the word you picked instead in the commit.
- **On-disk data:**
  - A file that a SURVIVOR row of `store/data_root.py` holds gets a one-shot migration under the landing (`docs/decisions.md` § 0011); rn-conn's `connector` migration is the model.
  - Run data whose shape moves is wiped, never read in two shapes. Name each wipe, and the landing key's old → new value.
  - `content_hash`'s `candidate_library` key re-keys root ids: name that move.
- **The wire is regenerated** (`types.generated.ts`, `openapi.generated.json`) through its scripts, never by hand. The webapp follows every served rename in the same link.

## Links (one commit each, in order)

1. `candidate_library` → `answer_terms` and `PoolSource.LIBRARY` → `PACKS`: `EditDraftPatch`'s three fields, the dataset file, and the root-id re-key.
2. The served hierarchy: "course" → "cycle" (`HierarchyCourse`, `level: "course"`, `/hierarchy/{course}`, `courseAddressOf`) and the discriminator `level` → `entry_kind`. They share every file, so they are one link.
3. The live-cycle "line" → `live_cycle` (`LinePayload`, `BenchLine.on_line`, `LineRun`). This changes the ledger's shape, so it is a wipe.
4. `Panel`/`ArmPanel` → `RoundSamples`/`ArmSamples`, and the L4 roster → `inner_panel`.
5. "slot": `ArmPosition`, `position_of`, prompt field, and region.
6. `CycleSeed*`/`ForkSeed` → `CycleStart*`/`ForkStart`.
7. Each of the two module pairs, `knobs.py` and `free_tier.py`, is renamed or folded.
8. Every twice-defined class is renamed or folded, the `complexity_ledger.py` row goes in at 0, and the small renames land:
   - the two misnamed functions;
   - `overshoot` → `lateness`;
   - `_DETACHED`.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg after each link: a rename moves no decision. If a behaviour key moves, name the behaviour that changed; a key that moved only because of a name is a defect in the key.
- Each row's re-test, before and after.
- `uniq -d` over top-level class names is empty, and the ledger row fails a planted duplicate.
- `playwright` is green at the tip: links 1–2 change served names the browser reads.
