# valid-2: one fork door, a draft becomes its cycle, one mint record, a copy that revalidates (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `valid-2`, arc `arc/valid-2`.

- **Base:** `origin/main` at `de4c0cc47480f00b7b4e37fa015681c9ddddc0ad`, main's tip with valid's link 1 landed: one validity check, `RunMode` = `from_round` + `on_divergence`, `Cycle.rng` on `run_seed`. Your `<base sha>` in common.md is this commit.
- **Prior work:** `origin/wip/valid` (697ce2791) moved the seed and the babysat stamp onto `CycleMintedRecord`, and `origin/out/valid-report:report.md` says why link 2 stopped. Read both, then rebuild that slice inside link 3 on your base. Never cherry-pick it.
- **The code shrinks:** target ≤ −60 over `promptpotter/`. Expected per link, from measured spans:
  - link 1 ≈ +67 / −126;
  - link 2 ≈ +29 / −40;
  - link 3 ≈ +86 / −106;
  - link 4 ≈ +16 / −4.

  Run `git diff --shortstat <base> HEAD -- promptpotter` after each link. A link that leaves the chain net positive gets one rebuild, then the chain stops there. Link 4 lands only while the running total stays negative.
- **Spec:**
  - the ledger section `### valid` of `docs/specs/code-debt-cleanup.md`: all five rows;
  - from `### batch/26 · admit-2`, the row "A fork's cut lands before its admission", which rides link 1;
  - the row "A steer is typed, then sent untyped" is stale (`_typed_steer` returns `dict[str, NodeCall]`, and `CycleSeed.pipeline_overlay` is typed): delete it in link 1.

  Line numbers in the rows are stale: grep the symbols.
- **Wipe:** run data, once, at the landing. The ledger form changes: there are no `cycle_seed` lines, no rewound entry on a fork, and no `resolution.json`, and a draft dir becomes its cycle. Name it in the commit body that changes the form. No reader of two shapes.

## Decisions (orchestrator; build them, do not reopen them)

### Link 1: one fork door

- **The carrier is `cut_fork` → `ForkCut`, not `ForkCyclePayload`.** A system fork has no trigger or issuer, and the payload's seed refuses the budget a rebase carries. `ForkCyclePayload` is the operator's request, and it builds a `ForkCut`.
- **An operator relaunch is a launch kind whose `open()` cuts under its held claim.** The fork is admitted on the parent's hop, unresolved, before anything is cut. `ForkCut.withdraw` and `_unfork` go. The daily count (`jobs/quota.py::_new_campaign`) reads the request's campaign.
- **`fork_siblings.py::cut_operator_fork` seeds `origin_prompts` from the named candidate (`arm.state.individual`)** next to `_candidate_params`. The operator's edit wins over both. So a REST fork naming X runs X's prompts under X's params, and `SteerForkPanel.tsx` stops sending prompts it did not edit.
- **The CLI's own fork rules go:** the rewind + steer refusal and its own default reason. `_dispatch_fork`, `_fork_operator_rewind` and `_fork_operator_steer` become one builder that lays the whole payload. `verbs.py::_unsaid` checks nested payload models too.
- **`HeldRun.mode` comes from `Admitted.mode`.** `detach`, `run_inline`, `job_spec` and the CLI's `run_inline(…, mode=)` calls drop the parameter, and the CLI re-derives no `RunMode`.
- **A served act carries the payload it fills.**
  - `RunAdmission` serves the start-run payload; DIVERGED serves `{"mode": {"on_divergence": "fork"}}`.
  - `KeepDraftsAction.keep` becomes `payload`.
  - `useRunControl.ts` ("Resume run") and `LiveSegment.tsx` (the `arm_gate: author` hold) send what is served, unchanged, and the webapp copies none of it.

### Link 2: a draft becomes its cycle

- **`OriginResolution` folds into `DraftFile`.** `save_checkin_draft` is the one writer and writes one fact. `RESOLUTION_FILE`, `write_resolution` / `load_resolution` and their `data_root` KINDS row go. Drafts are `Fate.RUN`, so they are wiped.
- **The mint renames the draft dir into the cycle**, because a draft dir is already laid out like a cycle dir. `adopt_ledger` and the three `drafts.close(` calls go. No manifest field names a draft, and `draft_ids` needs no skip. Check that no check-in observer appends after the rename, and name what you found.
- **`stores.py::write_own_definition` stages its files and swaps the dir; the landing completes a torn swap.** A rewrite without an origin no longer leaves a stale `origin.yaml`. A digest on tenant files is refused as a design, because install and research files carry none, so every reader would handle two shapes.

### Link 3: one mint record, one move onto a fork

- **A fork's mint is ONE record.** `CycleMintedRecord` carries the seed, the cut and `babysat: bool`. Rebuild the wip's `intervention: str` as that bool. It never twins `InterventionRecord`, so the fork's grade stays an intervention fact.
- **The root mint reorders.** `CycleMintedRecord` (with its seed) comes first, then the partition, resolved run and wiring, and the manifest last, as the root's commit. A torn root has no manifest, and the landing removes it.
  - `ResolvedRunRecord` stays a record: `_init_cycle` restates it.
  - `PartitionRecord` stays a record, for its nine `last(…, PartitionRecord)` readers.
- **A mint is idempotent by content.** A re-mint whose record equals the minted one is a no-op; a different one refuses typed. The wip's `minted is None` guard silently dropped a re-mint's seed.
  - If a leg trips the refusal, the door re-minting it derives a second seed for one cycle. Fix that door to read the minted seed, and name the leg.
- **The rewound `RoundEnteredRecord` in `mint_fork_cycle` goes, because the mint's `cut_round` says it.** Only the two folds that read across the fork chain need the cut, and each takes `CycleMintedRecord(cut_round)` as `displaced(cut, cut)`:
  - `ledger_scan.py::_Standing`;
  - `LiveDashboardProjection._handle_round_entered`, seeded from the prefix.

  Activity, `RoundAudits`, `RoundFileProjection` and the readout already read the loop's own entry. `rewound` stays for `rewind_to_round`.
- **`mint.py::onto_fork(session, cut, observers, prep)` is the one in-process move onto a fork.** It owns the take, `session.bound`, `_rebase_held` and the wiring. These fold into it:
  - `open_cycle`'s mid-INIT rebind;
  - `entry.py`'s `_mint_and_rebase_fork` and `_cut_rebase_fork`.
- **The take is the cut's last step.** These all land before the take:
  - `_cut_rebase_fork`'s learned slices: the cut carries `hangs_from`, and the mint the restated branch;
  - `resume.py::_branch`'s `leave_comparison`;
  - `repair.py`'s `grade_fork` and its rebank.

  So a torn fork never supersedes its parent.

### Link 4: a copy revalidates

- **`StrictModel.model_copy(update=…)` validates the whole merged state.** It runs `model_validate({**dict(self), **update})`, sets `fields_set` to the old set plus the update's keys, and carries `__pydantic_private__` over (`DraftFile._draft_id`, `PipelineParams._laid`, spend.py's three). An unknown key refuses.
  - Do not validate `fields_set` only: that regenerates default-factory timestamps and breaks 23 `exclude_unset` dumps.
  - `Campaign.edited` and `search_point.py:45` collapse into it.
- **The `model_copy_updates` row in `scripts/complexity_ledger.py`'s `BASELINE` counts `model_copy(update=` calls by AST**, at the count your link leaves (151 in 64 files at the base; grep misses the 48 wrapped calls). It can only fall. It sits beside `package_lines`.
- **Time the offline run before and after.** Name any rise over 5%. The survey measured 2–9 µs per copy and expects under 1%.

## Not yours (sequenced after you; do not build)

- **The (session, CycleConfig, optimizer) triple** that `jobs/mint.py::OpenedCycle`, `runner/inner/spawn.py::_Opened` and `run_job.py::HeldRun` bundle three times, including `HeldRun.of` re-reading `cycle_config`, is the "one cycle handle" root. Change only `HeldRun.mode`.
- **These admit-2 rows:**
  - "Hold released twice";
  - "The inner door mints its seat before its admission", which needs a paid L4 re-measure;
  - "A mint takes no claim until `admission.py::claim_cycle`".

## Beside you

Nothing runs beside you. hubs follows you on `store.py` and `run_records.py`.

## Proof (beyond common.md)

- **Equals-main:** no decision moves on any leg, at any link. Link 3 changes the ledger's form: name it. Name every digest move.
- **Full gate** at the tip, `playwright` included: link 1 changes what the browser sends.
- **Re-tests, each before and after:**
  - a REST fork naming candidate X runs X's prompts and X's params;
  - a refused fork admission leaves no `FORK_CUT` line on the parent;
  - a torn fork (simulated: killed between its mint and its take) resumes on its own rounds, never its parent's, and never on stale learned slices;
  - a crash between the draft's rename and the mint leaves no duplicate mint and no lost check-in ledger;
  - a torn definition swap is completed by the landing;
  - a copy with a stale key, or with a state its validator refuses, raises.
- **Ledger:**
  - Delete the five `### valid` rows, the ridden admit-2 row and the stale steer row in the links that close them.
  - File these unless your links close them:
    - `io.py::append_jsonl` batches rows while `CycleEventLog.append` loops one at a time;
    - `HeldRun.of`'s second config read, under the one-cycle-handle root if no row names it.
