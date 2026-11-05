# Persistence, state and recovery

Work lives in `.promptpotter/`: one campaign per `campaigns/{campaign_id}/`, every cycle (root, forks, diags) flat under its `cycles/`; a campaign is paused, resumed, rewound or forked, never finished.

## Four entities on disk

Owned by [`../architecture.md`](../architecture.md) § Four entities; this page adds the on-disk shape.

| Entity | On disk | Id |
|---|---|---|
| Workspace | `projects/{tenant}/` | the tenant |
| Dataset | tenant upload `projects/{tenant}/datasets/{slug}/` first, then shipped template `promptpotter/assets/datasets/{name}/` | its content (`shared/hashing.py::dataset_hash`); the slug is a label |
| Campaign | `campaigns/{campaign_id}/campaign.json` (`domain/campaign.py::Campaign`) | `{dataset}__{rand6_hex}`, fresh per `new`; a nested name's `/` is written `.` |
| Cycle | `campaigns/{campaign_id}/cycles/{cycle_id}/` | `cycle_{content_hash[:12]}` (+ `_fork_`/`_diag_`); a path resolves as `(campaign_id, cycle_id)` |

### The hierarchy address

Every served entry (`GET /hierarchy`, [`../specs/frontend-surface-contract.md`](../specs/frontend-surface-contract.md) § The served hierarchy) is keyed by an address the server mints and never renames:

```
campaign := [inside "~"] campaign_id                 bbeh__20261024-091500
course   := campaign "::" cycle_id                   bbeh__20261024-091500::cycle_7f3a
round    := course "/r" round_n                      bbeh__20261024-091500::cycle_7f3a/r2
candidate:= round "/" arm_key                        bbeh__20261024-091500::cycle_7f3a/r2/<ArmKey>
step     := course "/@" offset                       bbeh__20261024-091500::cycle_7f3a/@418
inside   := course ("~" course)*                     the L4 sandbox chain, the `?descend=` wire byte-exact
```

One minter and reader, `domain/cycle_paths.py::Subject` (`address` / `parse_address`); a course's address IS its encoded `CyclePath` (`webapp/lib/ids.ts::encodeCyclePath`), a step's offset the line index in its course's own ledger. A fork is stored as a sibling cycle, served as a child course; its `facts.spawn` is its `domain/run_records.py::ForkSpec`.

## Active session pointer

`projects/{tenant_id}/.workspace/active_session.json` (`{campaign_id, cycle_id}`, `store/session_pointer.py::active_pointer_path`) is the latest launch (`new`, a browser Start, a fork): the terminal's default target and what an unpinned webapp follows, never "what is running" (each course's `state` on `GET /hierarchy`). `--campaign` / `--cycle` override it for one command; `--tenant` (default `"default"`) picks the partition.

## Verbs

`python -m promptpotter <verb> --help`. Written verbs are `presentation/cli/commands/verbs.py::_WRITTEN_VERBS`; every other verb is a `domain/command_kinds.py::CommandKind` row's `cli_verb`, its payload's fields its flags (`verbs.py::command_verb`); a `BROWSER` row has none.

| Verb | Does |
|---|---|
| `new <name \| file.csv>` | mint from a dataset, or ingest a CSV into a check-in draft and Start it once the readiness gate passes (a gap prints its questions and exits non-zero: `--set k=v`, re-run); `--diag`, `--halt-at` |
| `head-to-head --head-to-head ID --arm …` | mint every arm of one controlled comparison in one act |
| `resume` | continue from the last closed round; `--from N`, `--rewind N`, `--fork-on-divergence`, `--no-check`, `--steer`, `--diag` |
| `verify <subject>`, `bench` | fresh search cells on one candidate (never a re-ask of a scored one); grade origin and selection on the bench rows |
| `ab` | replay the election under the current engine, stop at the first flipped decision |
| `evidence`, `cycles`, `machine-status`, `trail`, `price-head-to-head <plan>`, `export --tier` | reads whose answer is in no file |
| `seed-screen`, `noise-floor`, `probe-reasoning` | CLI-only paid diagnostics outside any cycle |
| `compact-archive <inventory \| compact \| restore \| purge-cold> [--apply]` | the only verb that can destroy a measurement: `compact` moves `domain/scoring.py::UNREAD_PIPELINE_KEYS` off `panel` answers into `measurements/cold/`, `restore` puts them back, `purge-cold` is irreversible; `origin`/`parent` answers are never eligible |
| `reset`, `land-data`, `close-admission` | the data-root verbs — [`../decisions.md`](../decisions.md) § 0011 |
| `reindex`, `pin-roster`, `offer-extras`, `health` | rebuild `measurements/index.jsonl`; pin a Harbor roster; list the extras the offered datasets need; check backends and sign-in providers |
| `pause`, `origin-gate`, `submit-arms`, `skip-searchpoint`, `step-cycle`, `set-limits`, `set-concurrent-cycles`, `cancel-queued`, `delete-cycle`, `cleanup-empty-cycles`, `archive`, `unarchive`, `delete [--keep-results]`, `rename`, `bank-snippet`, `discard-draft` | `CommandKind` verbs, each a `CommandRecord` the dispatcher acks |

Ctrl+C once pauses (exit 130), twice force-quits; a sent call is cancelled only where that stops its bill (`Connector.cancel_stops_billing`). Nothing the loop decides reads a diagnostic. A paid diagnostic or `bench` holds a machine slot and an account reservation, refused `409 cycle_busy` / `409 machine_busy` rather than queued (`application/jobs/launcher/admission.py::paid_pass`).

## Layout

```
.promptpotter/
  block_packs/{name}/pack.json          # the install's block packs, read-only to every account
  jobs/{job_id}.json                    # machine-global admission records (`domain/job.py::JobStage`)
  logs/latest-readout-path.txt          # names the newest terminal launch's `readout.log`
  .cache/source_facts.json              # the install cache, where an install has no checkout
  .cache/huggingface/                   # the box unit's HF_HOME (`deploy-linux/install-service.sh`)
  projects/{tenant_id}/
    user.json  grant.json  api_keys.json
    consent.jsonl                       # the account's opening, sign-ins and terms acceptances
    charges.jsonl                       # the account's bill, one line per hold and billed send
    datasets/  task-context/  block_packs/  uploads/  banks/labels.jsonl   # what the account authored
    .workspace/                         # run data: `reset` drops it with the campaigns
      active_session.json
      events.jsonl                      # workspace ledger — commands with no cycle, uploads, refusals
    head_to_heads/{id}.json             # `domain/campaign.py::HeadToHeadRecord`
    drafts/{draft_id}/                  # an open check-in, until Start or Discard
    ruler.jsonl                         # the tenant's δ ruler, `store/ruler_store.py`
    campaigns/{campaign_id}/
      campaign.json  log.md
      cycles/{cycle_id}/                # root + forks + diags, ALL FLAT
        dashboard.json  index.json  export.json  optimized.md
        log.md  review.md  hard_samples.json  readout.log
        rounds/round_NNNN.json
        langfuse/  prompts/
        .runtime/
          ledger.jsonl                  # append-only spine
          producer.lock                 # what "attached" means
          cache/rounds/
    measurements/                       # PAID; rooted at `Stores.archive_home`
      index.jsonl  configs/  cells/  cold/  claims/  sample_folds/
    optimizer_reuse/  judge_reuse/      # PAID; a grader never reads the loop's answers
    diagnostics/                        # scoring verbs that mint no cycle
    datasets/  materialized/  task-context/
    block_packs/account/pack.json       # the account's own snippets, `bank-snippet`'s alone to write
    uploads/{sha256[:16]}.{ext}         # every ingested file, kept before parsing; never served
```

Every file is one row of `infrastructure/store/data_root.py::KINDS` — its fate (SURVIVOR, CACHE, RUN, MACHINE) and its reader's shape; `reset` and `land-data` are that table applied ([`../decisions.md`](../decisions.md) § 0011). `config/paths.py::SHARED_CACHE_DIRS` is the whole paid tier. An archived campaign stays in `campaigns/`, hidden by `lifecycle_status`.

## File reference

Each file's contract is its model; this table says only what the type cannot.

| File | What the type does not say |
|---|---|
| `campaign.json` | One editor, `CampaignStore.update_campaign` (`Campaign.edited`). The origin's locked criterion is a `ScoringLockedRecord` on the root ledger, never written back. |
| `dashboard.json` | `LiveDashboardState`, a fold of the ledger; a read serves `application/served_dashboard.py::ServedDashboard` off `projection.py::fold_at`, never the file. |
| `index.json` | `CycleIndex`, one writer, no reader — readers fold the ledger (`projections/cycle_index.py::read_cycle_index`); branch kind is parsed from the id (`store/layout.py::sibling_kind`). |
| `rounds/round_NNNN.json` | The round's last statement, for the operator; a round resumes from its close on the ledger (`closed_rounds.py::closed_rounds`), never from its file (`closed_rounds.py::RoundFileProjection`). |
| `.runtime/ledger.jsonl` | The only surface saying which optimizer node RAN (`llm_call` records); an `arm` record carries the individual's whole `lineage`. |

**The cycle runs on ledger records read over the fork chain**: `bank_partition` (`domain/bench.py::PartitionRecord`), appended at mint, a fork reading its parent's (`loop_start.py::cycle_partition`); `resolved_run.declaration` (search SPACE) LAST wins, `resolved_run.experiment` (CELLS) FIRST wins (`pipeline_resolve.py::resolve_pipeline_for_campaign`); a `domain/run_records.py::BackendRevision` drift is served, never refused.

## Diagnosing a live or stuck run

**Ledger tail → `cycles` → process table by command line → only then mtimes.**

1. **Ledger tail** (`.runtime/ledger.jsonl`) — the chronology, and each `command`'s `command_ack`: `accepted` (armed), `applied` (taken), `rejected` (no loop will).
2. **`python -m promptpotter cycles`** — derived `run_phase`, the producer reading, the pause cause.
3. **Process table, by command line** — which process holds the cycle.
4. **Mtimes** — only to date what the above explained.

A command is taken at the next **per-sample** checkpoint, so a pause asked mid-candidate looks like a freeze for seconds.

### Phase and liveness

`domain/phases.py::RunPhase` is the vocabulary. `run_phase` is derived (`infrastructure/runtime_flags.py::derive_run_state`) from the ledger and the producer lock, never written; `dashboard.json::declared_phase` is the runner's own record and says `running` forever after a `kill -9`. `queued` and `starting` read `producer: claimed`, attached to every guard (`launcher/admission.py::claim_cycle`); which verb a cycle admits is served (`RunAdmission`). **Attached is the producer lock (`infrastructure/producer_lock.py`) and nothing else**; `live` / `idle` / `wedged` is graded off the LEDGER's mtime, never `dashboard.json`'s (debounced). `application/jobs/reaper.py` stamps `producer_vanished` only on a `running` cycle whose lock nobody holds.

### Commands ride the ledger

`.runtime/` holds no control file: a command is acked `accepted` into the cycle's inbox (`ledger_scan.py::Controls`) and `applied` at the checkpoint that takes it; a new `running` declaration or any stop clears what stood. The standing budget is the last `standing_budget` record on the cycle's ledger (`runtime_flags.py::standing_budget`); its money axes are the campaign's, the last `campaign_ceiling` record on the ROOT's ledger (`ledger_scan.py::scan_campaign_ceiling`).

### Where the error text is

A failed cell's `ErrorCategory` and message land in the latest `rounds/round_NNNN.json` and `readout.log`. A death with no terminal record is machine-sleep or session-end class. Only a hard kill leaves containers behind; the next run on that backend sweeps them (`infrastructure/docker_host.py::reap_dead_producers`).

## Recovery: resume, rewind, fork

| Workflow | Command | Effect |
|---|---|---|
| Resume | `resume` | Continue the active cycle from its latest closed round. |
| Rewind | `resume --from N` | Same `cycle_id`; rounds after N displaced; resume at N+1. Admissible only if round N closed. |
| Operator rewind | `resume --rewind N` | Fork a sibling lifting rounds `0..N-1`, continue at N under the fork's overrides. |
| Fork on divergence | `resume --fork-on-divergence` | Mint a sibling cycle rooted at the divergent round and continue. |

Until an L1 round closes, a resume re-enters round 0 and holds at the origin gate. Refusals, from every way in: `409 cycle_busy`; `409 config_drift` (the dataset no longer resolves to the minted origin, unless policy-only — `application/jobs/mint.py::resolve_resume`); `409 ledger_unreadable` (remedy `delete <campaign>`, which keeps the caches). **Which edits need a fork** is a knob's `Scope` (`domain/campaign_config.py`): `POLICY` resumes in place; `DATA` and `IDENTITY` run divergence detection and halt rather than drift — revert, or fork.

### The primitive

A fork is a new cycle whose first record (`cycle_minted`) names its `parent`, `forked_at_offset`, `cut_round` (parent rounds it LIFTS; `0` = clean offshoot) and `ForkSpec`. Nothing is copied: the parent's prefix is walked (`ledger.py::ledger_chain`) and the parent's ledger gets a `FORK_CUT` checkpoint. `from_candidate` resolves once at mint (`fork_siblings.py::fork_edge`). A fork inherits no standing budget (`served_dashboard.py::fork_remainder`; its money is the campaign ceiling); `config_overrides` carries its whole `OptimizationConfig` delta. A new fork driver adds a `ForkTrigger` member and fills a `ForkSpec` — no edit to `mint_fork`.

**A cut has a DIRECTION** (`ForkSpec.resolved_direction`): a diag or steered fork is an `offshoot`; `scoring_divergence`, `operator_rewind` and an L2/L3 rebase **supersede**, retiring the parent at the cut (`CampaignStore.mark_superseded`) unless a producer holds it; a correction's measured answer is the only source of `equivalent` ([`../../promptpotter/infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree). **Every resume first repairs** cells recorded without a measurement, on a branch cut first and graded second so the parent stays byte-identical (`repair.py::_rebank_on_branch`); a hole is plugged with a real measurement, never an archive row.

### Human in the loop

HITL is the fork primitive: the webapp **Steer & fork** (`SteerForkPanel`) and the terminal's `--steer` / `--rewind` fork a sibling via `CycleEventLog.inherit_from(parent, offset)`; `keep_rounds` picks the act, the shape an applied mask takes ([`mask-projection.md`](mask-projection.md) § Applying one). The button, `pause` and the first Ctrl+C all fire `pause-cycle` through `CommandDispatcher`. **The arm gate** (`optimization.arm_gate`: `off` · `review` · `author`) holds a round before any arm is measured; `submit-arms` answers it (`SubmittedArm`), banked as a second `round_proposed` record so a resume re-holds only where no answer stands. An inner cycle stops when its owner does (cause `enclosing`). The look-ahead's request is the accepted `set-sample-lookahead` ([`access-model.md`](access-model.md) § host-admin ↔ user).

## Will a config change re-score? — the measurement cache

The measurement key is `node_configs` (overlay-merged `pipeline_params`, model included) plus the sample's content (`Sample.key`), never its dataset or position; a change at node *N* re-measures every sample whose pipeline ran past *N*. A running campaign runs its minted DECLARATION: editing `dataset.yaml` changes no existing campaign. Two campaigns on one dataset+config share a root `cycle_id` (`runner/campaign_ids.py::origin_content_hash`), so key a surface by `(campaign_id, cycle_id)`. **On L4 the identity inputs are read live** (`application/runner/inner/connector.py::_identity_config`) — land config fixes before an origin is measured. **Concurrent walks buy a cell once** (`measurement_archive.py::ReplayFeed`, `search_point_scorer.py::_claim_cell`): claim by OS file lock, share the row when it returns; `force_fresh` claims nothing.

## Changing the composite formula — fork, never swap

The scorer compiles once at run init (`initialization/loop_start.py::populate_session_scoring`): edit `campaign.json::scoring`, then `resume --fork-on-divergence`; a mid-cycle swap makes the composite incomparable to its own past (`escalation/state.py::_improved`). A `per_cell` formula is what θ is fit on (`domain/scoring.py::Scorer`), the only route a latency, cost or reliability term reaches the election; keep correctness `per_sample`. Names: `scoring/formula/compiler.py::objective_namespace`. `target_prompt_chars` is a fact about the CANDIDATE, so a length penalty's normaliser is a constant in the formula — the scorer id hashes formula text alone.

## Lifecycle — archive, delete

`archive` is a flag (`lifecycle_status`), restorable by `unarchive`; `delete` removes the tree, or with `--keep-results` spares the keepsake. Both refuse only while a cycle is LIVE, record on the **workspace** ledger first, and never touch the measurement store. Every byte lands in one `store/data_root.py::Leaf` of `CAMPAIGN_ROWS`, reported by `application/maintenance/storage_report.py` off `store/layout.py::declared_files`: a surface missing from it is deleted by `--keep-results`.
