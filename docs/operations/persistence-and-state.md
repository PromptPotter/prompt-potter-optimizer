# Persistence, State, and Recovery

Your work lives in `.promptpotter/`: `campaigns/{campaign_id}/` is one campaign per directory, and every cycle (root + forks + diags) sits flat under its `cycles/`.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-dark.png" alt="The files view: the cycle's round files on disk, with round 6 open as a scoreboard and its verdict" width="100%">
  </picture>
</p>

**A campaign is an object on disk, not a script that has to finish.** The file tree is the dashboard: each round is a JSON file you can open in an editor, and the app renders the same file as a scoreboard. Pause a campaign, resume it, rewind to any round, or fork a sibling and compare — [§ Recovery](#recovery-resume-rewind-fork).

## Four entities — where each one lands on disk

**Workspace → Dataset → Campaign → Cycle** — owned by [`../architecture.md`](../architecture.md) § Four entities (outermost → innermost). This page adds only the on-disk shape.

- **Workspace** — `projects/{tenant}/`. Every directory is named for what it holds, and the root partitions by lifecycle, which is how "what survives a delete?" is answered by looking.
- **Dataset** — resolved tenant-first: a tenant upload (`projects/{tenant}/datasets/{slug}/`, the `new <file>` ingest path) wins over a shipped template (`promptpotter/assets/datasets/{name}/`). An ingested slug is first-class to both `new <slug>` and `resume`, not just the mint that made it.
- **Campaign** — `campaign_id = {dataset}__{rand6_hex}`, minted fresh per `new`, a nested name's `/` written `.` (`bbh.word_sorting__…`), which no name may hold. `campaign.json` carries its `declaration` (the run it was minted to make — only the keys a layer set) and the `bench_defaults_pin` under it, `root_content_hash` (resume's drift check, which names a moved default), `treatment` (the optimizer it runs, its manifest digest the optimizer's defaults — [`../architecture.md`](../architecture.md) § Three identities) and, `purpose` (`optimize`, or `compare` naming the head-to-head arm it runs under — § The controlled comparison there); none is the id.
- **Cycle** — `cycle_{content_hash[:12]}` (+ `_fork_`/`_diag_` on branches). Path resolution is always `(campaign_id, cycle_id)`.

### The hierarchy address

Every served entry (`GET /hierarchy`, [`../specs/frontend-surface-contract.md`](../specs/frontend-surface-contract.md) § The served hierarchy) is keyed by an address the server mints and never renames:

```
campaign := [inside "~"] campaign_id                 bbeh__20261024-091500
course   := campaign "::" cycle_id                   bbeh__20261024-091500::cycle_7f3a
round    := course "/r" round_n                      bbeh__20261024-091500::cycle_7f3a/r2
candidate:= round "/" arm_key                        bbeh__20261024-091500::cycle_7f3a/r2/<ArmKey>
inside   := course ("~" course)*                     the L4 sandbox chain, the `?descend=` wire byte-exact
```

`(round, arm_key)` is the standing rounds' own join, unique per course, so a candidate's address is
the key of a row on every surface; its individual is not, since a repair re-measures one individual on
two courses. `webapp/lib/ids.ts::courseAddressOf` is the one place a candidate's address is cut back to
its course.

One minter, `domain/cycle_paths.py::hierarchy_address`, and **a course's address IS its encoded `CyclePath`** (`encode_cycle_path`, `webapp/lib/ids.ts::encodeCyclePath`), so the webapp mints none: it reads `address` and decodes a course's where a route wants typed hops. A cycle directory is never renamed, so a retired branch keeps its address for ever — which is what makes it the key of an open set, a list and an ETag. **A fork is stored as a sibling cycle and served as a child course of its campaign**, its `facts.spawn` naming the event it was born at: the parent course, the round and ledger offset of the cut, the address of the candidate it was cut from, the trigger and the direction (`CycleIndex` + `domain/run_records.py::ForkSpec`).

**There is no Session tier** — owned by [`../architecture.md`](../architecture.md) § A campaign has one root cycle. `active_session.json` is a pointer at your latest launch and holds nothing a cycle needs; there is no `sessions/` tree.

## Active session pointer

`projects/{tenant_id}/.workspace/active_session.json` (`{campaign_id, cycle_id}`) is your latest launch — the workspace root selects the file, so the tenant is the path, not a payload field (`store/session_pointer.py::active_pointer_path`).

**It names ONE cycle and runs can be many.** A CLI `new`, a browser Start and a fork each overwrite it, so it is the terminal's default target and what an unpinned webapp follows — never "what is running". That is each course entry's own `state` on `GET /hierarchy`, which the webapp's jobs dock and sidebar marks read; a view the operator pinned moves only when the pointer lands on a new cycle of that same campaign.

- **`new`** mints a fresh campaign + root cycle and overwrites the pointer. Re-running `new` on an unchanged declaration reuses the content-addressed root-cycle id and origin score (cache-served), then diverges from round 1.
- **`resume`** reads the pointer and picks up that cycle. No re-`new` needed.
- **fork** mints a new cycle in the same campaign and retargets the pointer.
- **`--campaign <id>` / `--cycle <id>`** override the pointer for one command, on `resume` as on every run-control verb. Everything a launch needs is the campaign's manifest and the cycle's ledger; nothing is picked separately. **`--tenant <id>`** (default `"default"`) selects the partition under `projects/`.

Every subcommand runs as `python -m promptpotter [--tenant <id>] <subcommand> [options]`. **Loop-mint:** `new`, `resume`. **Lifecycle:** `archive`, `delete`, `unarchive`, `reset`, `discard-draft` (closes an open check-in under `drafts/` without minting). **Manifest-edit:** `rename` (display name only; `campaign_id` still addresses it). **Run-control:** `pause` (stops a running cycle at its next checkpoint, resumable), `set-limits` (raises or lowers a live or paused cycle's `--max-usd` / `--max-tokens` / `--max-rounds` / `--max-wall-clock` bound — the four budget flags `new` and `resume` share, each `none` lifting its axis — how a budget- or round-halted cycle is continued), `cancel-queued` (withdraws a launch still waiting for a machine slot; `pause` refuses one, since no loop exists yet to take it), `skip-searchpoint`, `step-cycle`, `origin-gate` (answers a cycle holding at the round-0 gate — the only answer a run launched without a TTY has in the terminal), `submit-arms` (answers a cycle holding for a round's arms under `optimization.arm_gate`), `bench` (grades the origin and the selection on the held-out bench set, for the cycle holding the campaign's line — `CampaignConfig.bench_trigger` defaults to `manual`, so no launch sends that pass unasked; refused while the cycle has a run in flight). **Diagnostic:** `verify`, `ab`, `noise-floor`, `seed-screen`, `probe-reasoning`. **Read:** `evidence`, `cycles`, `machine-status`. **Export:** `export --tier chat|report|notebook|deploy|package` (one cycle's files re-shaped for the reader's next move — `chat`/`report` print, the rest write under `--out`; the same tiers as `GET …/cycles/{id}/export?tier=` and the candidates card's copy and download). **Maintenance** — the three that REWRITE stored artifacts rather than reading them, all dry-run by default and all refusing while a producer could still be appending: `reindex`, `restamp`, `compact-archive`. **Dataset authoring:** `pin-roster` (a Harbor dataset's task roster, fetched from the registry once and written beside the connector, so a run reads the pin and never the registry).

Reads happen by opening the on-disk artifact tree. The read VERBS are the four whose reading is in no file: `evidence`, a comparison ACROSS subjects — a campaign, one branch or one searchpoint, repeated `--subject` and at any L4 depth, and the campaigns' bench headlines side by side; `cycles`, every cycle's `run_phase`, producer and pause reason, derived at each read (`--inside <campaign>::<cycle>` lists an L4 inner run's, and is the only verb that takes it); `machine-status`, occupancy and the queue, derived across every job on the box; and `trail`, an account's sign-ins, consent and commands merged across its ledgers in time order (bare, this tenant's; with an email, what that person did in every workspace on the box).

**`compact-archive` is the only verb that can destroy a measurement.** `compact` moves the fields nothing reads — `hit`, `scored`, and `pipeline_data`'s `reasoning_trace` / `total_time` / `turns` / `step_phases` (`domain/scoring.py::UNREAD_PIPELINE_KEYS`; never a ranking, since a row cannot tell a moved ranking from one that returned nothing and a recall evaluator would grade it a real miss) — out of `panel` answers (a candidate's own walk) into `measurements/cold/{config_key}.jsonl.gz`, stamping each answer it moved from so a compacted row is never mistaken for one that never carried the field. `restore` puts them back row for row, not byte for byte (compact separators, the restored key last in its object); `purge-cold` deletes the cold store, and only that step is irreversible. `origin` and `parent` answers are never eligible — they serve the overwhelming majority of cache replays — and an L4 outer row (one carrying `PipelineData.inner`) keeps its trace, because the L4 narrative panel reads it.

**The diagnostics are fenced — nothing the loop decides reads one, and nothing may.** The loop does fire one: a round reading 100% runs `verify` on its winner (`application/diagnostics/verify.py::verify_on_saturation`), banked and served like an operator's, and the loop reads none of it. **An operator's paid diagnostic or `bench` holds a machine slot and a reservation against the account for as long as it scores, and is refused `409 cycle_busy` on a cycle a run or another pass holds, and `409 machine_busy` on a full machine rather than queued** — one door for every way in, `application/jobs/launcher/admission.py::paid_pass`.

- **`verify`** re-scores ONE candidate on search cells it has never met, moving no round and no election — **the canonical way to deepen a winner you want to believe.** It is addressed as `evidence` addresses a searchpoint (`candidate:<campaign>/<cycle>/<candidate_id>`), banks the pass on that cycle's ledger as facts, and reads the fresh cells ALONE beside the round's own, with the lift over C0 paired on every cell both scored (`application/diagnostics/verify.py::read_verify`). `--strategy hard` buys the ruler's highest-δ unseen cells. Depth comes from more samples, never from re-asking a cell: a re-ask measures how noisy the model is, which the loop cannot act on, and against the recursive L4 backend it replays the first answer outright.
- **`ab [--campaign <id>]`** re-derives a campaign's recorded decisions under the current engine/scorer and reports where the change stops carrying over ([`mask-projection.md`](mask-projection.md)). Zero LLM calls. It ablates code, not knobs: PoBB's ε and lock-in floor replay off each decision's recorded `inputs_ref`.
- **`noise-floor`** re-scores a campaign's cached origin `--k` times with `force_fresh`, reading the backend's run-to-run noise.
- **`seed-screen`** scores a seeded bank draw of ANY dataset against that dataset's own origin over repeated passes, and rejects any bank whose constant-answer floor EXCEEDS the origin by more than the measurement's own error bar — such a bank pays a candidate for collapsing to a single label, the degeneracy the instrument exists to catch. Each pass also reports median and mean per-call latency, wire cost, and the share of answers that went to a single label, so screening a candidate *model* needs no second instrument. The gap between the two latency readings says the route is retrying; the answer share is the twin of the floor — the floor says what a constant answer would SCORE, the share says how nearly this model IS one. Its concurrency is `--parallel N`, never the browser's ⇉ control — a look-ahead depth is spent by the round that scored under it, and a screen (N independent draws) has no round to spend one. `--model` / `--provider` pin the solver over the dataset's own, as an L4 pool environment pins it (`domain/cells.py::Seat.inner_model`), so a (dataset, solver) pair is screened as its cell would run without a second dataset directory; the sidecar carries the solver in its name.

## Layout

```
.promptpotter/
  block_packs/{name}/pack.json         # installed block packs, the operator's (`config/paths.py::block_packs_root`)
  projects/{tenant_id}/
    .workspace/
      active_session.json              # { campaign_id, cycle_id }
      events.jsonl                     # workspace ledger — commands with no cycle to address, and the account's own events
    head_to_heads/{id}.json             # a declared head-to-head: its origin + the shared part of one declaration
    drafts/{draft_id}/                  # an open check-in: its draft, upload and ledger, until Start or Discard
    campaigns/{campaign_id}/            # {dataset}__{rand6_hex}, fresh per `new`
      campaign.json                    # manifest, typed and frozen (`domain/campaign.py::Campaign`):
                                       #   dataset, backend_url, the run declaration + its defaults pin — no run state
      log.md                           # campaign digest — root + forks + rounds
      cycles/{cycle_id}/               # root + forks + diags, ALL FLAT
        dashboard.json                 # the BANKED live state (`LiveDashboardState`) — a fold of the ledger
        index.json                     # the cycle's own facts, folded off its ledger — written for you, read by nothing
        export.json                    # the winner + its provenance, for a program that is not us
        optimized.md                   # which resolved values the optimizer MOVES, and whether the
                                       #   model can see each one (`PipelineSchema.value_tree`)
        log.md  review.md              # per-cycle digests (derived — safe to recompute)
        hard_samples.json              # the cycle's graded cells on its δ ruler, rewritten each round
        readout.log                    # the run readout, ANSI-stripped, every launch appended
        rounds/round_NNNN.json         # serialized RoundResult — a checkout of the round's close
        langfuse/  prompts/            # trace shadow; rendered optimizer prompts
        .runtime/
          ledger.jsonl                 # append-only spine: the cycle's facts, commands, decisions, walks, spend
          producer.lock                # held by the process driving the cycle — what "attached" means
          streams/round_NNNN_{member}.jsonl  # the eliminator's race telemetry (sparkline in log.md)
          cache/rounds/                # per-round node I/O
    measurements/                       # PAID — measurements. Cross-cycle/campaign and into an
                                        #   L4 sandbox, WITHIN one archive home: `build_stores` roots
                                        #   it at `Stores.archive_home` — this tenant's dir by default,
                                        #   `.promptpotter/.archives/<SHARED_ARCHIVE>/` when the host
                                        #   names one, owned by the install.
      index.jsonl                  # one row per configuration per dataset; `reindex` rebuilds it from cells/ + configs/
      configs/{config_key}.json    # the configuration a key names, written once
      cells/{config_key}.jsonl     # the cell store: one appended line per ANSWER, never rewritten, filed under
                                   #   the node-chain prefix that produced it. A cell keeps every answer
      cold/{config_key}.jsonl.gz   # fields `compact-archive compact` moved off answers (restorable)
      claims/{cell}.lock|.json     # a cell some walk is measuring now, and its row until taken (transient)
      derived/                     # read models folded FROM the cells (regenerable)
    optimizer_reuse/{hash}.json         # PAID — optimizer-LLM answers, replayed instead of re-sampled
    judge_reuse/{hash}.json             # PAID — LLM-as-judge grading replies, same shape, own tree:
                                        #   a grader must not read the loop's answers
    diagnostics/                        # the scoring verbs that mint no cycle
      runs/{ts}_{config_hash}.json      #   noise-floor, as a typed DiagnosticRunRecord. A verify
                                        #   banks on its cycle's ledger instead (`verify:graded`)
      seed-screen-{dataset}-{date}.json #   seed-screen's own shape — it screens SEATS, before a campaign
                                        #   exists, so the record's source-campaign fields would name
                                        #   nothing (`seed_screen.py` argues it). None of it is served.
    datasets/  materialized/  task-context/     # dataset tier — definition, materialized rows, decomposed context
    uploads/{sha256[:16]}.{ext}         # every file dropped on ingest, kept before parsing; never served
```

**Why this shape.** Telemetry is *temporal* — each cycle owns its `dashboard.json`, so `tail cycles/{cycle_id}/dashboard.json` follows exactly the cycle you're watching. Audit is *structural* — frozen records keyed by the cycle that produced them. Cycles sit flat because a fork tree keyed by `parent` scales where nested fork-of-fork directories don't. `measurements/` is a cross-cycle peer of `campaigns/`, so a fresh `new` on an unchanged declaration cache-hits every origin sample yet still gets its own `campaign_id` and trajectory; `optimizer_reuse/` is its peer on the optimizer's leg and `judge_reuse/` on scoring's.

**`config/paths.py::SHARED_CACHE_DIRS` is the whole of the paid tier** — that tuple is what `build_stores` roots at the archive home, what `reset` preserves and what the workspace storage report counts as the home's, so a cache is added to it or it is silently destroyed by the first. Everything else in the root is campaign state, regenerable, or config. There is deliberately no recycle bin: an archived campaign stays in `campaigns/` and is hidden by `campaign.json::lifecycle_status`, so a campaign has ONE home and no enumerator has a second parent to remember. The `langfuse/` mirror is observability only — resume/rewind read solely from the cycle ledger and `measurements/`.

## File reference

| File | Lives at | Content |
|------|----------|---------|
| `campaign.json` | campaign dir | The manifest, one typed model (`domain/campaign.py::Campaign`): dataset, label, `root_cycle_id`, `root_content_hash`, `backend_url`, lifecycle intent, and the `RunDeclaration` with its `bench_defaults_pin` (single owner — no per-cycle copies; `campaign_config.py::effective_config` is what it runs). Its one editor is `CampaignStore.update_campaign(**CampaignEdit)` (`Campaign.edited`), which admits only the fields a campaign may change after mint. The criterion the origin locked is NOT written back into it — it is a `ScoringLockedRecord` on the root's ledger, read through `pipeline_resolve.py::resolve_campaign_config`. Run state is per-cycle and the ledger's. |
| `dashboard.json` | the cycle's dir | The banked live state (`LiveDashboardState`, strict): round, origin, best, candidates, counters, spend. One stream per cycle, a fold of that cycle's ledger. What a READ adds — `run_phase`, the producer reading, the fork remainder, the armed ceiling — is `ServedDashboard` (`application/served_dashboard.py`), built per request off the ledger's fold (`projection.py::fold_at`) and never on disk; no read parses this file. Only a replay to a moment before the run declared its wiring — inside the check-in a root's moved-in draft ledger opens with — serves `WarmingDashboard`. |
| `log.md` (campaign) | campaign dir | Campaign digest. |
| `hard_samples.json` | per cycle | The cycle's graded cells on its δ ruler (`domain/cells.py::HardSamples`), rewritten at every round's close. The one such file: campaign scope reads one cycle's (`application/scoring/measurement_log.py::campaign_scope_cycle`), dataset scope is folded per request. |
| `head_to_heads/{id}.json` | workspace | One declared head-to-head (`domain/campaign.py::HeadToHeadRecord`): the instrument every arm is graded under and the budget each may spend. Written once, by its first arm's mint (`new … --arm {id}:{key}`, or `mint-campaign`'s `purpose`), which declares it off its own; a later arm adopts its split and budget and, on any other instrument, is refused before anything is minted. Its arms are the campaigns whose `campaign.json::declaration.purpose.arm` names it — the record lists none, so a mint never rewrites it. |
| `index.json` | per cycle | `domain/cycle_index.py::CycleIndex`, written out — the cycle's mint, lineage, ending, result and standing rounds. **Every field is folded off the ledger** by `projections/cycle_index.py::read_cycle_index`, the one reading every listing, lineage walk, guard and render takes; the file has one writer (`write_cycle_index`) and no reader, so deleting it loses nothing and `restamp` rewrites it. A branch's KIND is not stored — `store/layout.py::sibling_kind` parses it from the id. |
| `export.json` | per cycle | The winning prompt by field name, the node config it ran under, and the provenance a consumer needs to trust the number (fitness under its named formula, n, lift + CI, θ, the rows' hash, the optimizer manifest) — all the optimizer's own reading, beside `bench`, the deployment estimate on rows it never read, stamped with the `scorer_id` it was read under. Written from the same call that appends `cycle_final`; absent when no round ever closed. Contract: `domain/export.py`. |
| `optimized.md` | per cycle | Which of the resolved values the optimizer MOVES, and the channel each reaches the model by — the reading of the declaration its `resolved_run` record holds that the declaration cannot give, since it names a key and never whether the model will ever see the value. Markdown: its only reader is a person. Same cadence as the declaration. |
| `log.md` / `review.md` (cycle) | per cycle | Per-cycle digests. Derived views — safe to delete and recompute. |
| `readout.log` | per cycle | The live run readout (per-sample HIT/MISS, round summaries, SP tables), ANSI-stripped — the headless tail when you're not watching `dashboard.json::current_round`. **Every launch writes it, whatever started it** — terminal, browser, embedded host, an L4 inner cell — and the browser opens it from the cycle's Files tree. **One file per cycle, appended per launch**: each launch opens with a `Readout:` line, so why the last one stopped sits directly above it. A fork mid-run moves the readout to the fork, and the parent's file ends on the line naming it. The repo-root gitignored `logs/latest-readout-path.txt` holds only the newest TERMINAL launch's readout PATH; a fork inside that launch is named by the last line of the file it points at. |
| `rounds/round_NNNN.json` | per cycle | Serialized `RoundResult` — a CHECKOUT of the round's last statement (`round_closed` or `round_restated`), its rows read back from `measurements/`, written for the operator: no code reads it back, and a fork carries none of its parent's. Its sole writer is `application/scoring/closed_rounds.py::RoundFileProjection`, a ledger subscriber: a close or a restatement rewrites its round's file, entering a round removes that round's and every later one. |
| `.runtime/ledger.jsonl` | per cycle | Append-only fact stream. Every decision a node takes rides a `ResumeCheckpointRecord` stamped with that node and a `CheckpointKind` — potter's escalation firings are its `l2_escalation_trigger` / `l3_escalation_trigger` — and no separate signals stream exists. **It is also the only surface that says which optimizer node actually RAN**: every `llm_call` record carries `prompt_chars`, and potter's, which alone composes through dispatch panels, adds what each cost it — `injection_chars` / `injection_dropped` / `injection_silent` (`optimizers/potter/dispatch/facade.py`). The round file cannot answer either — its `optimizer_state.prompt_hashes` names every node on every round by construction. **An `arm` record is an individual's provenance, whole**: its `ArmState.individual` embeds the individual's `lineage` (`domain/opt_search_point.py::IndividualLineage`), and `runner/round.py::announce_population` appends one `Proposed` per proposal — so a reader folds an individual's in-edges as the union over its `arm` records. |
| `.runtime/streams/round_NNNN_{member}.jsonl` | per cycle | Per-sample race standings, one file per eliminator `member`. |
| `.runtime/cache/rounds/` | per cycle | Per-node optimizer I/O. |

**The records a cycle's facts are.** `cycle_minted` (first record of every ledger; a fork's names its parent, the parent-ledger offset its history begins at, and its `ForkSpec`) · `run_phase` `terminal` with a `stop_reason` (the ending — the runner's own, or the reaper's / a supersede's declared for a cycle whose runner is gone) · `cycle_final` (the result block + `interrupted_round`) · `cycle_superseded` · `fork_graded` (a correction's measured direction) · `intervention` · `spawned` · `standing_budget` (the operator's standing budget AND the job's reservation beside it) · a bench pass (`PhaseRecord` `bench:graded`, folded by `scan_bench_passes`) · `launch_clock` (one launch's clock, banked as it ends — a pause's included — by the cycle holding the campaign's line, the root or where supersede cuts handed it on; an offshoot banks none). A campaign's result is the fold of those over its line: the passes its headline is read off (`runner/bench.py::read_bench`, for the head-to-head, the campaign list and the export), its clocks (`runner/campaign_result.py::line_launches`), its calls and spend. A later `running` declaration retires a standing ending and supersede, which is what makes a resume of a finished cycle unfinished again with no file to edit.

**What the cycle runs on is ledger records too, read over the fork chain.** `bank_partition` (`domain/bench.py::PartitionRecord`) is the draw `CampaignConfig.dataset_split` declares, appended at the mint as positions in the bank its `dataset_hash` names: the search split every optimizer draw reads, the bench set the headline is scored on — with `bench_rows`, the content identity a head-to-head compares — and the demo pool. A fork appends none and reads its parent's, so every launch and diagnostic verb restores one draw by position (`loop_start.py::cycle_partition`), and a re-cut that moved the dataset's label onto another bank never moves a cycle off the one it drew. `resolved_run` (`ResolvedRunRecord`), appended by `init_cycle` on every launch, holds two halves at opposite cadences. Its `declaration` is the search SPACE — the live backend's, under the dataset overlay — and the LAST wins, because the operator is owed what the next round will search; it exists because a committed dataset file snapshots values and not the backend's `param_keys`, and before any launch the dataset file answers. Its `experiment` is the set of CELLS — the dataset's `panel` with everything it only NAMES resolved — and the FIRST wins, because a roster owes what every round already measured: a later resolution that disagrees is logged, never adopted, and is read before any live re-resolve (`pipeline_resolve.py::resolve_pipeline_for_campaign`).

Material facts land on disk in human-readable form. Entry points never write campaign artifacts directly — every write rides the per-cycle ledger through two projections (live telemetry + audit). The allowlist is a structural invariant that fails loud; no standing test, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).

**A round is resumed from its close, never from its file.** `resume` and every fork read the prior rounds off the ledger: each round's last statement (`round_closed` or `round_restated`) is the round, whole, its rows read back from `measurements/` by the addresses the close carries (`application/scoring/closed_rounds.py::closed_rounds`), and what a round's proposer generated is its `round_proposed` record. Three records decide what STANDS — entering round N displaces N and every later round, a `rewound` entry (a `--from`, a branch minted to continue at N) displaces what was generated for them too and every skip an operator made there, and a `round_restated` record restates a closed round without closing it again. Editing a round file therefore changes nothing a resume reads; to continue from a different point, fork with a seed. The close's `optimizer_state` envelope is `{manifest, population, prompt_hashes, slices, outputs}`: `population` is the bench's — the individuals the run carries into the next round — and a resume or fork restores it onto `Cycle.population` beside the optimizer's own slices (`bench/cycle.py::Cycle.replay_priors`).

## Diagnosing a live or stuck run

A run that looks frozen is one of a few things, and they are distinguishable in a fixed order. Follow it — guessing from file timestamps first is how a healthy pause gets read as a crash.

**Ledger tail → `cycles` → process table by command line → only then mtimes.** Each step answers a question the next cannot:

1. **Ledger tail** (`.runtime/ledger.jsonl`) — the append-only chronology. The only surface that can say *against which rival* and *in what sequence* — and what the operator asked for: a `command` record, whose `command_ack` says how far it got — `accepted` (armed, the loop has not reached it), `applied` (the loop took it), `rejected` (no loop will).
2. **`python -m promptpotter cycles`** — the derived `run_phase`, the producer reading and, for a paused cycle, who or what paused it.
3. **Process table, by command line** — which process holds the cycle. Match the command line, not the image name; several python processes are normal.
4. **Mtimes** — last, and only to date something the three steps above already explained.

**The trap this order exists to avoid:** a command the loop takes is spent at the next **per-sample** checkpoint, not at the round close. A pause asked mid-candidate takes effect within seconds — and to anyone watching file timestamps, a deliberate, clean, resumable stop is indistinguishable from a freeze.

### `declared_phase` is not `run_phase`

Two different facts, and conflating them is the costliest mistake here. **The declaration** is the runner's own `run_phase` record on the ledger, written by the process that dies — so read raw it says `running` forever after a `kill -9`. `dashboard.json::declared_phase` mirrors it and no read parses the mirror. **`run_phase`** is **derived**, in exactly one place (`infrastructure/runtime_flags.py::derive_run_state`), for every reader, from the cycle's ledger and its producer lock — no projection is an input — and is never written to disk.

### The phase vocabulary

`RunPhase` (`domain/phases.py`) composes two orthogonal facts — lifecycle (active or finished) and control + liveness — and `RUN_PHASE_INFO` beside it is what each phase reads as on every surface. Derivation is a first-match ladder (`runtime_flags.py::read_run_state`). A check-in is a draft, never a cycle, so no phase names it:

| Phase | Means | Written by | Derived from |
|---|---|---|---|
| `queued` | A launch holds the cycle, waiting for a machine slot | `claim_cycle` (`launch_claim` `stage: queued`, the job's own stage) | the last `launch_claim`, while its claimant's lock is held and neither a `run_phase` record nor that job's `launch_released` follows |
| `starting` | Past the queue, no process holds the cycle yet | `claim_cycle` (`launch_claim` `stage: starting`) | same |
| `running` | A producer holds the cycle and drives it | the runner (`run_phase` `running`) | producer lock held |
| `scoring` | A paid pass (`bench`, `verify`, `noise-floor`) holds the cycle outside its loop; `pause-cycle` stops it at its next cell, its cells banked | nobody (`launcher/admission.py::paid_pass` holds the pass lock beside the producer lock) | both locks held, ahead of the lifecycle: a pass scores ended cycles too |
| `gate` | Holding for the operator — at the round-0 origin gate, or for a round's arms; the declaration's `gate` says which | the runner (`run_phase` `gate`) | declaration + lock held |
| `paused` | Left cleanly, **active and resumable** | the runner's stop (`run_phase` `paused`, with `cause` + `detail`) — or, before the loop reaches it, an `accepted` `pause-cycle` | the declaration, else the inbox |
| `terminal` | Finished; the reason is its `StopReason` | whoever ends it (`run_phase` `terminal`) | the last declaration |
| `detached` | Active lifecycle, nobody holds it | nobody | no lock, no live claim |

`queued` and `starting` read `producer: claimed`, which every guard treats as attached: a second launch, a delete, a paid verb and a `step-cycle` are refused from the moment the launch is accepted, not from the moment a process exists. A launch that ends without a run appends `launch_released` with what refused it; one killed in the window releases by dying, since its claim stands only while its lock does. The launch stage has ONE vocabulary — `RunPhase` — and one writer of it onto the ledger (`launcher/admission.py::claim_cycle`, from `Job.stage`).

**Which verb a cycle admits now is served, never inferred from the phase** — `RunAdmission` (`domain/phases.py`), on the dashboard, a course entry's `state.admission` on `GET /hierarchy` and the `cycles` verb, and the dispatcher refuses on the same value. An L4 inner cycle (`RunState.inner`) answers every run verb but the look-ahead with that verb's own sentence, raised by the command as `inner_cycle_unaddressed`.

**Every paused exit names its cause** (`PauseCause`): `command` (a `pause-cycle` the loop took — `detail` names who), `enclosing` (an L4 inner cell under its outer's pause), `interrupt`, `cancelled` (the host cancelled the task), `step` (the launch reached its round allowance), `bound` (a declared bound cut the panel). One seam writes it (`run_observers.py::declare_run_stop`), never each raise site; the dashboard and a course entry's `state` (`GET /hierarchy`, the `cycles` verb) serve it as `pause` (`PauseReading`).

### Liveness — the lock says attached, the ledger grades it

**Whether a producer is attached is its lock on the cycle (`infrastructure/producer_lock.py`) and nothing else.** What GRADES an attached one — `live`, `idle`, `wedged` (`ProducerState`, served as the producer reading beside `run_phase`) — is the LEDGER's mtime and its last non-heartbeat append; a heartbeat is an append. No projection's file time enters it: `dashboard.json` is debounced, so its mtime lags the run it describes.

**`detached` ≠ `paused` ≠ wedged.** `paused` is a clean, deliberate, resumable exit; `detached` means nobody holds the cycle; **wedged** is a producer attached and no longer *progressing*, which is a producer state and not a phase — nothing takes the cycle from under it.

**No silence is reaped.** The liveness reaper (`application/jobs/reaper.py`) stamps `terminal` with `producer_vanished` only the cycle whose last word was `running` and whose lock nobody holds — a long await, a slept machine and a wedge all still hold theirs. **A long await must still heartbeat** — owned by [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md) § Conventions; what it buys here is that a quiet producer is not served `wedged` to an operator deciding whether to kill it.

### Commands ride the ledger

`.runtime/` holds no control file and no lifecycle file. A command for a running loop is delivered by the ledger it is recorded on: the dispatcher acks it `accepted`, which arms it in the cycle's inbox (`ledger_scan.py::Controls` — the standing pause, the skips, the look-ahead, the gate decisions); the loop's `RunControl` acks it `applied` at the checkpoint that takes it, which spends it. A new launch's `running` declaration and any stop clear what stood, so a command cannot outlive the run it was sent to — and one sent where no loop will take it (a pause on a cycle nobody holds, a gate decision before the gate holds) is refused at dispatch. An `auto` look-ahead is a mode, so its ack leaves it standing.

**The standing budget is ONE store**: the last `standing_budget` record on the cycle's own ledger, written whole and naming only the axes an operator named. A launch reads it and so does a run in flight (`runtime_flags.py::standing_budget`, an incremental fold), so `set-limits` from another process binds at the cost of one stat. The round bound is on the same record — `budget.rounds` (the cap) and `pause_at_round` (a `step-cycle`'s allowance) — read at each round boundary (`runner/loop.py::round_bounds`, which answers the config the round runs under and the pause point). The money axes it names bound the CAMPAIGN, one ceiling every live cycle of it shares.

### Where the error text is

A failed cell's typed `error_category` (`shared/errors.py::ErrorCategory`) and its message land in the latest `rounds/round_NNNN.json`, alongside the cycle's `readout.log`. The optimizer-call path carries a hard wall-clock (`_chat_under_deadline` → `OPTIMIZER_TIMEOUT`), so a hung optimizer call terminates itself. **An overnight death with no terminal record is machine-sleep or session-end class, not a code fault** — do not go looking for a bug in the loop.

**A killed run outlives itself in its containers.** A containerized cell is torn down by the process that started it — on cancellation and on failure alike — so only a hard kill leaves one idle container per in-flight cell, holding a trial nobody will collect. The next run on that backend sweeps them, and its trial scratch, off the lock each carries (`infrastructure/docker_host.py::reap_dead_producers`); what the sweep keeps is the task images and the package cache, which are what make the resume cheap.

## Recovery: resume, rewind, fork

Three workflows over one fork primitive.

| Workflow | Command | Effect |
|----------|---------|--------|
| **Resume** | `resume` | Pick up from the latest completed round of the active cycle. |
| **Rewind** | `resume --from N` | Same `cycle_id`; archive rounds after N; resume at N+1. |
| **Fork on divergence** | `resume --fork-on-divergence` | On divergence — a round produced by a different optimizer, a package that no longer reproduces, or a decision that re-derives differently — mint a sibling cycle rooted at that round and continue. |

**Until an L1 round closes, a resume re-enters round 0.** Every launch re-measures the origin — cached cells replay free, cells never sent or errored are sent — then closes round 0 from that measurement and holds at the origin gate on its verdict. A round-0 file a stopped run left has passed no gate, so no resume reaches round 1 past a partial origin. **One live run per cycle:** a launch targeting a cycle that already has an unfinished job is refused `409 cycle_busy`, naming that job. **A resume of a campaign whose dataset no longer resolves to the origin it was minted on is refused `409 config_drift`** from every way in (`application/jobs/mint.py::refuse_drifted_resume`), unless the edit is policy-only or the cycle's own seed steers the pipeline; the terminal then offers a fresh `new`. **A cycle whose ledger holds a line this build cannot read is refused `409 ledger_unreadable`** by every reader of that line's kind, from every way in, never half-read; the refusal names the line and its remedy, `delete <campaign>`, which keeps the measurement caches, and `restamp` lists every such cycle.

### The primitive

A fork is a new cycle whose FIRST ledger record (`cycle_minted`) names its `parent` — the parent cycle's directory relative to its own, `../{cycle_id}` for a sibling — the `forked_at_offset` on the parent's ledger its history begins at, and its `ForkSpec`. Its KIND is stored nowhere, because the id already answers it — `store/layout.py::root_cycle_id` / `::sibling_kind` know exactly two separators (`_fork_`, `_diag_`). Forks land **flat** under `cycles/`; the tree is reconstructed from the mint records, never from directory nesting and never from `index.json`. The parent's ledger gets a `ResumeCheckpointRecord(kind=FORK_CUT)` naming the child's `cycle_id` and the cut round.
A fork owns its own `dashboard.json` and a ledger carrying **own appends only** — the parent's prefix is walked, not copied. **Nothing is copied across at the cut**: the mint is followed by one `round_entered(rewound)` at the round the branch continues from (0 for an offshoot), so every reading of its rounds keeps the parent's before that and nothing after, and the δ rulers are read over the chain the fork CONTINUES (`ledger.py::continued_chain`; an offshoot starts over and inherits none). The standing budget is scanned on the cycle's OWN ledger, so a fork never inherits one — what it runs under is its seed's declaration, its rounds bounded by what its parent's cap has left (`served_dashboard.py::fork_remainder`) and its money by the campaign's one ceiling, shared with every live cycle. What minted a course is its entry's `facts.mint` (`root` · `fork` · `diag` · `inner`), worded by its `mint` tag; the trigger that cut a fork rides `facts.spawn`.

Every cut serializes ONE typed `ForkSpec` to `FORK_CUT.data.fork` + the fork's `cycle_minted.fork`, and its callers differ only in what they fill: **scoring divergence** (trigger/reason/issued_by only) and an **operator-steered fork** (`seed: CycleSeed` + `from_candidate_id`). The primitive does not know which fired — a new caller adds a `ForkTrigger` member and nothing else.

**`from_round` is provenance; `mint_fork(fork_from_round=…)` is mechanics.** The arg says how many parent rounds this cut LIFTS (`0` = a clean offshoot lifting none); the spec field says which round it was CUT FROM. A rebase makes them equal, so the seam back-fills the spec when its author left it unset — but only then. Only a steered cut names `from_candidate_id`, so only it can be labelled by the candidate it came from.

**Three checks for a new fork driver.** If any fails, the primitive has reached its scope and the feature wants its own layer: the driver must be **trigger-agnostic** (a new `ForkTrigger` member and a filled `ForkSpec`, no edits to `mint_fork`'s body); its override must be **OSP-carriable** (a different pipeline shape or scoring formula is a layer above); and it must cause **no data fracture** (no parallel persistence directory, no duplicate of something already in `measurements/`, `rounds/` or the ledger). Library measurements are deliberately not on the tree — content-addressed by `JobSearchPoint.content_hash`, two forks see identical hashes and read the same `measurements/` row, which is why a second fork's origin costs zero LLM calls.

### Rewind — `resume --from N`

Use when the active cycle went somewhere you don't want. `cycle_id` stays; rounds after N are deleted, state is restored from round N, and the run resumes at N+1. The measurement archive is preserved — per-sample results replay without backend calls.

**Partial rounds.** Ctrl+C mid-round (a resumable pause, `StopReason.PAUSED`) leaves ledger events but no `round_closed` record; the public `rounds/round_NNNN.json` stays absent (the audit cache carries the partial with `"interrupted": true`) and the cycle stays non-terminal and resumable. `--from M` is admissible only if round `M` has a closing event — so after a pause mid-round-1, `--from 1` refuses and `--from 0` resumes cleanly.

### Fork — `resume --fork-on-divergence`

Use when a **data-affecting** edit (scoring formula, `pipeline_overlay`, `exclude_nodes`, `dataset_name`) makes resume's replayer find recorded decisions no longer hold. The optimizer halts rather than drift; either revert, or commit with `--fork-on-divergence`. It mints a new `cycle_id` **in the same campaign**, rooted at the divergence point, keeps the pre-divergence rounds by reading them where the parent's ledger closed them, records its `parent`, and re-runs the divergent round under the current scorer. The shared archive is not duplicated — both cycles read the same measurements through their own scoring ledger. **Why rewind isn't enough:** rewind restarts under the *same* policy and would re-hit the same divergence.

**A cut has a DIRECTION** — which side the run continues on, written at the cut and served as `fork_direction`. The trigger usually implies it (`FORK_DIRECTION`, derived, so every fork already on disk answers it): a diag / steered fork is an `offshoot`, the child hanging off a line that keeps running; a `scoring_divergence`, `operator_rewind` or L2/L3 rebase **supersedes**, the child being the continuation the pointer moves to and the *parent* what was left behind. Same shape on disk, opposite reading — which is why nothing is deleted on a supersede. A correction is the one cut taken before its consequence is known, so it records the answer it later measured on `ForkSpec.direction`, which outranks the derived default; that is the only way `equivalent` arises. **How a cut READS once served** — where the fork's course hangs, which side wears `superseded`, which cycle speaks for the campaign — is owned by [`infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree. What THIS layer must get right is that the direction, and how far it reaches, are on disk before any reader asks.

**A cut retires only as far as the branch actually got** — owned by [`infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree; the write side hands the branch exactly the candidates it retires (`repair.py::_rebank_on_branch`), and the reach is read back off the last round the branch's own ledger minted a candidate for, so the two sides cannot drift.

**A supersede retires the parent, on disk, at the cut** — `mint_fork` appends `cycle_superseded` and, where no ending already stands, declares the parent terminal with `StopReason.REBASED` (`CampaignStore.mark_superseded`) — unless a producer holds it, which declares its own stop: a run-init fork ends the parent it opened on before its readout moves (`runner/entry.py`). The parent stops writing *by design*, and an undeclared deliberate stop would read as a crash: `detached`, then `producer_vanished` at the reaper's next sweep. Resumability is untouched — a later launch declaring `running` retires both records. An **`equivalent`** cut moves the pointer the same way and retires nothing.

**After a REPAIR both sides carry the same `candidate_id`** — owned by [`infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree (*identity outranks position*); the withdrawn measurement is the one the round was actually steered by. A retired candidate wears **no crown**: it was elected over rows the cut replaced, so its `reading.election` wears no `selected` and no `crown` until the branch re-elects.

**Policy-only edits** (PoBB knobs, patience, thresholds, `n_variants`, `exploration.*`) can't have changed the data trace, so resume continues in-place and `--fork-on-divergence` is a no-op. Past decisions stay as the audit record of the policy that made them.

**Unless a repair lands.** Every resume first makes each closed round re-derive from its own rows — re-measuring cells it recorded without a measurement, and re-projecting a headline that no longer matches its winner's row. That is *incompleteness*, not divergence, so it runs whatever the config diff says; the winner replay runs on top, so a flipped crown forks rather than overwrites.

**A correction cuts first and is graded second.** Whether a round needs correcting is decided from the round file alone, and the branch is taken right there — before any re-measure begins, because the version a correction replaces is the version its descendants read and there is one copy of it. The parent therefore stays byte-identical to what ran, and the operator watches the old round move to its own branch while the repair is still running. The grade lands once the correction does: every round's optimizer packages are fingerprinted before and after, each rendered at its own point in the run, and the cut is stamped `supersede` if anything read differently or **`equivalent`** if nothing did. An `equivalent` cut is not a dead end — both sides carry the same content forward, so candidates already generated for the next round come *across* rather than being regenerated.

**The cut is a CANDIDATE, not a round** (`repair_cut` → `ForkSpec.from_candidate_id`): everything from the first candidate the repair moves retires with it, so a break in a round's third candidate leaves the first two on the line — while the fork still *lifts* whole rounds, because a round is the unit of election.

**The correction reaches the branch's LEDGER, not only its round file** — `repair.py::_rebank_on_branch` takes each corrected round through the whole ingress (mint, walk, election, close): a round banked without its close is invisible to every scan, a round file nothing on the ledger backs.

**A resume's own corrections branch without asking.** `--fork-on-divergence` decides what happens when something changed from OUTSIDE — a different scorer, a different optimizer — and those still halt by default. A repair, or a generation the resume finds stale, is the resume doing its job.

**A cached generation records what it read.** Candidates are persisted with `consumed`, the `round_document_digest` of the round they were composed from; on resume that digest is recomputed from disk and compared. Both sides are persisted JSON, so unlike the package differential this reproduces across processes — which is what catches a critique re-distilled by an *earlier* resume, the common case. A cache with no recorded digest is **unvouched**, and unvouched branches too.

A hole is plugged with a **real measurement, never an archive row** — a cached row for that `(node_configs, sample_key)` may have been produced as a PoBB *backfill*, measured out of the round's shared order to fill someone else's paired comparison, and adopting it as this candidate's own panel cell is what makes a repaired round unreproducible. The re-measure bypasses only the outer archive, so the inner spawn still resolves content-addressed and **continues the furthest-along campaign banked for that cell** instead of restarting it.

### Human in the loop — steer & fork, pause

**HITL is not a separate I/O kind** — it collapses into the fork primitive above. The operator forks via `resume --fork-on-divergence` (CLI) or the webapp **Steer & fork** flow (the searchpoint drill-in on either cladogram → `SteerForkPanel`; it refuses below the top level, where `fork-cycle` carries no `descend`): pause the run, edit the chosen searchpoint's prompt + node config + limits, and fork a sibling cycle tagged `operator_steered`. The fork roots at the chosen offset via `CycleEventLog.inherit_from(parent, offset)`, inheriting the parent's typed state at the cut.

**One verb, two acts, and `keep_rounds` is which.** Unset it is the steer above: a clean offshoot from the origin, rounds numbered from 1, re-scoring the edited searchpoint. Set, the same command mints an `operator_rewind` — rounds `0..N-1` are LIFTED and the branch continues at N under the seed's `config_overrides`. That is the shape an applied scoring mask takes ([`mask-projection.md`](mask-projection.md) § Applying one). It refuses a seed carrying `origin_prompts`: the lifted round 0 already IS the origin. The terminal's equivalent gesture is `resume --rewind N`.

**Pause run.** The webapp button and the CLI `pause` verb fire the same `pause-cycle` command through the same `CommandDispatcher`, which records it on the cycle's ledger (§ Commands ride the ledger). The loop takes it at its next checkpoint and the worker exits cleanly, but the cycle stays **non-terminal and resumable**, declared `paused` with `StopReason.PAUSED` and cause `command` — so *who asked* is on disk. `python -m promptpotter pause` targets the active cycle (`--campaign` / `--cycle` to name another, `--reason` to annotate). Ctrl+C at the producing terminal is the same command (`cli/commands/launch.py::_terminal_inputs`) and the fastest route, since it also cancels the run instead of waiting for a checkpoint.

**The arm gate — the operator names what a round measures.** `optimization.arm_gate` (`off` by default) holds every round after its proposers ran and before any arm is measured. Under `review` the proposals are **drafts**: the hold lists them, and the operator answers with drafts kept by index, arms of their own, or both. Under `author` no proposer runs and every arm is the operator's. The answer is the `submit-arms` command — `python -m promptpotter submit-arms --arms arms.yaml [--keep 0,2]`, `POST /commands/submit-arms`, or `embedded_run.py::submit_arms` — refused wherever the cycle is not holding for arms and wherever `--keep` names a draft the hold does not offer. An arm of the operator's is an EDIT of the round's parent (`SubmittedArm`: prompt fields, a pipeline overlay, a required `changes_description`), minted like any child and measured, eliminated and elected like any other; its lineage's `variations` name `operator` as the node and the mode, and the cycle is marked babysat.

**What the hold leaves on disk, and what a resume does with it.** The drafts are the round's `round_proposed` record and the answer is a second one, `submitted`, standing beside it rather than over it (`StandingRounds.submitted`) — so the arms the operator wrote are readable twice, as the `command` payload and as the minted proposals. A resume into the round replays the drafts as it always did; where an answer is already banked against the same parent it IS the round's arms and nothing holds, and where none is, the gate holds again. A pause at the hold exits resumably like any other. The knob is `policy`-scoped: who wrote an arm rides the arm's own lineage, so an edit to it governs unevaluated rounds only and never forks the trace.

**An inner cycle stops when its owner does.** Its `RunControl` carries the cycles it measures for (`enclosing`, outermost first) and folds each one's inbox beside its own, so a pause asked of the outer stops the inner at its next checkpoint, declared `paused` with cause `enclosing` — where one outer *sample* is an entire inner run.

**Make a slow round finish sooner — the look-ahead control.** The remote's **⇉** control runs the round with several calls in flight instead of one — its candidates walk together and decide where a serial round would — cutting its scoring wall clock roughly in proportion. Suggest it whenever someone asks why a round is taking so long; it is the only speed lever needing no config change and no restart. **Every clause of it** — who may press, what one press buys, why the overshot sample is discarded — is owned by [`access-model.md`](access-model.md) § host-admin ↔ user. What this layer must hold is the on-disk half: the operator's *request* is the accepted `set-sample-lookahead` command in the cycle's inbox and what the loop actually ran at is the served dashboard's `sample_lookahead`, never the request served as that.

## CLI flags — `new` and `resume`

`new <name>` mints a fresh campaign + root cycle from an authored `promptpotter/assets/datasets/<name>/` and runs from round 0. `new <file>` (a CSV — `Path.is_file()`) parses the file into a durable check-in draft, runs the AI origin check-in (the same `checkin` node the web ingest uses), auto-confirms high-confidence findings, and — once the readiness gate passes — Starts it, minting the campaign, and runs the loop inline. If a gap survives the resolver, `new` prints the open fields + questions and exits non-zero — nothing is minted on a guessed default; confirm with `--set` and re-run. After a successful file run the committed slug is first-class to `new <slug>` / `resume`.

**The flag set is `presentation/cli/commands/verbs.py`** — every row a page like this could carry was its `help=` string one `--help` away, and the two drifted. Read the flags there; this page owns what they do to the tree, above.

### Interrupt handling

- **First Ctrl+C** — records `pause-cycle`, banks completed work, declares the cycle `paused` (resumable), exits **130**. On `bench`, `verify` and `noise-floor` the same press stops the pass: its cells stay banked and a rerun replays them. A sent call is cancelled only where cancelling stops its bill (`Connector.cancel_stops_billing`); elsewhere it lands.
- **Second Ctrl+C** — force-quits immediately.

An interrupt mid-round leaves ledger events but no closing `round_closed` record — see **Partial rounds** under Rewind for which `--from N` offsets are then admissible. Nothing is left running to hunt for: a cancelled cell is torn down by the process it belongs to, and only a hard kill leaves anything, which § Where the error text is covers.

## Will a config change re-score? — the measurement cache

The single most-asked operating question: *"I edited a connector tunable — will the next run actually re-measure, or replay the old score?"* Four facts answer it. (`configure_and_apply_pipeline` applies `exclude_nodes` + `pipeline_overlay` and returns the `pipeline_params` that flow unchanged through both `new` and `resume`; a `None` result means the backend runs its full pipeline.)

1. **The measurement key includes the connector config, model included.** Per-sample results pool in `measurements/` keyed by `node_configs` — the effective per-node config derived from the overlay-merged `session.pipeline_params` — and by the sample's content (`Sample.key`), never its dataset or its position: a sample two datasets share is measured once. On a config change at node *N*, the prefix match breaks at *N*: **every sample whose pipeline ran past *N* is re-measured**; only samples that short-circuited upstream replay. So changing `entity_profiling.model` from `120b` to `20b` genuinely re-scores the LLM-path samples.

2. **A running/resumed campaign runs its minted DECLARATION.** `campaign.json` owns it; editing the dataset's `dataset.yaml` does **not** change an existing campaign. To apply a connector-config change, mint a fresh `new` — it reads the edited dataset configs, gets a fresh `campaign_id`, and re-scores a new origin. For an *in-place* re-explore after a data-affecting edit, that is `--fork-on-divergence`.

3. **The cycle id is config-aware, and agrees with the measurement key.** `Campaign.root_content_hash` is `runner/campaign_ids.py::origin_content_hash` over the SAME overlay-merged params (and the root `cycle_id` is `root_cycle_id_of` that hash and the declaration) the measurement key hashes, so two origins differing only by model get **distinct** `cycle_id`s.

   **Invariant — always key a surface by `(campaign_id, cycle_id)`, never `cycle_id` alone.** A direct consequence of the content-addressed id: two campaigns on the same dataset+config share the **same** root `cycle_id` and differ only in their random `campaign_id`. Every persistence path already resolves as the pair, and the webapp keys its unit map by it, so two same-dataset campaigns render distinctly. Any **new** read/write surface MUST carry the campaign id too; a lookup by bare `cycle_id` would cross-wire siblings.

4. **On L4 the identity inputs are NOT frozen — editing one mid-campaign re-measures the origin.** `application/runner/inner/connector.py::_identity_config` fingerprints the inner cells' **treatment** (`SelectedOptimizer.treatment()` — each node's prompt body, its resolved response schema, which is prompt text riding every call as `response_format`, and its config; every member's knobs; and **the source deciding what each node is handed**, the runtime's `source_digest` — potter's hashed set also carries its per-node layouts and its library), **the estimator's own source** (`_measurement_source_digest`), and the inner benchmark's `{dataset}/dataset.yaml::pipeline` node configs *and* `::campaign` — the worker model and the scoring formula included, since either changes what every cell measures. Both source digests are normalized through the AST, so a comment costs nothing while an expression voids the origin. Fact 2 does not cover these — they are read live, not snapshotted — so an edit lands on the *running* campaign: the banked outer origin stops joining and the next round pays to score it again. **Land config fixes before an origin is measured, never between its rounds.**

   **Deliberately NOT in it, because a corpus that cannot survive them cannot accumulate:** the manifest's non-inner nodes (`checkin`, descriptions, `available_models`), `APP_VERSION`, and the panel's `domain/cells.py::Seat` roster — each seat is its own sample's `source_pin`, so adding or editing one re-measures that seat alone. The version constant voided every banked cell on every release while saying nothing about whether the measurement had changed.

### Concurrent walks buy a cell once

Walks in separate processes often score one configuration on one panel at once — several campaigns measuring the same origin, a fork beside its parent. The cell is the replay key, `(node_configs, sample_key)`, and a cell measured once is the row every walk reads; a second measurement is money spent to make headlines disagree, since the backend is not deterministic and a run log keeps the last row per sample. The protocol is `measurement_archive.py::ReplayFeed`, driven by `search_point_scorer.py::_claim_cell`:

1. **Replay rows are read forward.** Before measuring a cell, a walk reads the index entries and run-log bytes banked since its last read (`ReplayFeed.advance`), so a cell another process banked after this walk opened is replayed, not bought.
2. **A cell is claimed before it is measured** — an OS file lock at `measurements/claims/{cell}.lock` (`ReplayFeed.claim`). A cell another walk holds is waited for, polled, and the wait heartbeats so it never reads as a vanished producer. The wait has sent nothing, so a pause breaks it and every stop cancels it, never waiting out another process's call.
3. **The row is shared the moment it returns**, as `{cell}.json` beside the lock, and every waiter replays it then. No waiter depends on the holder's walk order, so two walks can never hold each other's cells. Only a row a replay could serve is shared — no error, not deprecated, graded at least `REUSABLE_MIN_GRADE`; otherwise the claim drops and the waiter measures.
4. **The holder releases once the row is on disk or discarded** — after `Walk.take` persists it, or when `Walk.end` drops it (a look-ahead cell past a cut, a pause, a cell still landing after its walk ended, a catch-up no walk took). The shared row is unlinked before the lock, so a row found under a free lock outlived its holder and the next claimer deletes it — including one a concurrent reader held open, which a release leaves behind rather than fail its walk.
5. **A crash needs no expiry.** The kernel drops the lock with its process, and the next poll takes the cell over; a live holder is never timed out, however long its cell runs.

A waiter that replayed a row its holder later discarded keeps it: it is a measurement of that cell, though the holder's walk filed no answer for it. **`force_fresh` claims nothing** — repair, `noise-floor` and the origin gate's re-measure measure on purpose — and neither does a configuration with no node configs, which has no cell identity.

## Changing the composite formula — fork, never swap

**There is no live swap, and the reason is a gate rather than plumbing.** The scorer compiles once during run init (`initialization/loop_start.py::populate_session_scoring`) from `campaign.json::scoring` and is never re-read. To change it: author a `per_cell` formula over the names below, edit `campaign.json::scoring`, and `resume --fork-on-divergence` — the sibling starts at the divergence point and every round it banks is scored under one formula.

**A `per_cell` formula is what θ is fit on** (`domain/scoring.py::Scorer`), so it is the only route by which a latency, cost or reliability term reaches the election at all — a round is won on θ, and a term outside it moves the display alone. It is charged where it happened rather than meaned over the panel: at round scope one 2000-second cell hides behind a fast one, and which prompt provoked the slowdown cannot be recovered. The cell's own correctness stays `per_sample` and stays what `is_hit` thresholds, or a correct-but-slow answer would render MISS and send L1 to repair an answer that was already right.

Swapping it between rounds would make the composite **incomparable to its own past** inside one cycle, silently. `escalation/state.py::_improved` — the L2/L3 stall gate — asks whether the cycle's best advanced since a layer fired, and answers on `best_composite_fitness` whenever the θ ruler is unavailable. Redefine the composite mid-cycle and that comparison reads a change of scale as progress or as stall, with nothing to error on. No command kind changes the composite mid-cycle, and adding one would reintroduce exactly this.

### Available names

**A `per_cell` formula reads `scoring/formula/compiler.py::objective_namespace`** — the declared channels (`fitness`, `latency`, `cost`, `tokens`, `ground_truth_rank`, `target_prompt_chars`, the L4 lift family), the three row-health facts (`errored`, `degraded`, `cached`), and whatever else that dataset's own trace carries. **A MASK reads `evaluators.py::_REGISTRY`** plus the per-cell channel means `metrics.py::_MEANED_CHANNELS` folds in, which is what lets one formula be written once and read in both places. Read both there: a table here goes stale in the one direction that costs the operator a name they never learn exists. Registry entries are gated by `applies(schema)`, so each is present only when the matching node is active.

Helpers are `scoring/formula/compiler.py::SAFE_BUILTINS`. Output clamped to `[0, 1]`; undefined names raise `NameError` — fail loud is the contract, which is why the name list must come from the registry rather than from prose.

**`target_prompt_chars` is a fact about the CANDIDATE, stamped on every one of its cells** — the characters of its prompt template on the node it renders onto, before the sample is interpolated, few-shot block included. A length penalty's normaliser is therefore a **constant written into the formula**, e.g. `fitness - 0.05 * target_prompt_chars / <origin chars>`, the literal being the origin's own reading on its round-0 rows (`evidence --metric 'expr:target_prompt_chars'` on the campaign subject serves it). Never a campaign-bound name: the scorer id hashes the formula text alone, so a reference that varies by campaign would pool two scales under one id on the δ ruler.

The default composite renders in operator surfaces as `composite_fitness=0.6042  (Δ+0.1030 vs reference 0.5012)` per candidate — anchored on the row's matched reference, the same floor the accuracy Δ beside it uses — with the full formula text always in `log.md`.

## Beta hosting state

Single-operator (auth-off) and hosted-beta (OIDC) share the same on-disk shape. The beta adds three operator-visible surfaces under `projects/{tenant_id}/`.

**Per-user quotas (`user.json`)** — one tenant per user, missing file ⇒ defaults, hand-editable, checked on every `mint-campaign` and `start-run`:

```json
{ "spend_budget_usd_total": null, "token_budget_total": null, "max_concurrent_cycles": 2, "max_campaigns_per_day": 1000 }
```

**What each knob bounds, and what bounds resource use generally** — owned by [`access-model.md`](access-model.md) § What bounds resource use.

**Campaign ownership + lifecycle (`campaign.json`).** Each manifest carries `owner_user_id` (cross-user reads return **404, not 403** — existence leakage is itself a violation) and `lifecycle_status: active | archived | deleted` (+ `_changed_at`, `_reason`).

**`archive`** is the flag and nothing else — the tree stays in `campaigns/`, hidden from the default listing and restorable by `unarchive`, because a campaign with two possible homes made every enumerator carry a second parent. **`delete`** is physical and destructive with no recovery: it removes the tree outright, or with `--keep-results` strips the heavy tiers and spares the keepsake (manifest + reports + the shallow langfuse loop trace), flagging the manifest `deleted`. Either is refused only while one of the campaign's cycles is LIVE; the active pointer is released and the verb proceeds.

The cross-campaign measurement store is never touched, so siblings still cache-hit — and by the same token a delete STRANDS every row no surviving campaign can replay, with no verb that reclaims them. **Build that reclaim before a bulk delete, never after it:** the orphans are only measurable while the campaigns that named them are still on disk, so deleting first destroys the evidence and the motivation in one gesture. What it must do is [`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) § Blocked — named blocker.

```bash
python -m promptpotter archive   <campaign_id> [--reason TEXT]
python -m promptpotter delete    <campaign_id> [--reason TEXT]
python -m promptpotter unarchive <campaign_id>
```

Each is idempotent, and each — from the terminal exactly as from the web — dispatches through `CommandDispatcher`, which writes a `CommandRecord` to the **workspace** ledger (`.workspace/events.jsonl`) before marking the manifest. The workspace ledger is the audit home because `delete` removes the campaign's own ledger, so it cannot record its own disappearance.

## The storage taxonomy — Connector / Loop / Dataset

There is **one** storage vocabulary, the operator's mental model. Every byte in a campaign tree lands in exactly one of six leaves — mutually exclusive and exhaustive, summing to the on-disk total. The top-level axis is **Connector vs Loop vs Dataset**; **Loop** breaks into four. Classifier: `store/layout.py::classify`; the report: `application/maintenance/storage_report.py`.

| Leaf | Parent | Contents |
|---|---|---|
| **Dataset** | — | `langfuse/datasets/` — the ground-truth mirror (input-data copy; usually the biggest chunk) |
| **Connector** | — | `.runtime/cache/**` + the per-sample `cells`/`overlap_results` arrays carved from the public `rounds/round_*.json` |
| **State** | Loop | the round checkouts — non-array remainder of `rounds/round_*.json` (what a resume reads — each round's close, the cycle seed — rides the ledger, so it lands in **History**) |
| **Trace** | Loop | telemetry — `.runtime/streams/`, `prompts/`, `langfuse/{traces,observations,scores}/` |
| **History** | Loop | the durable event spine — `.runtime/ledger.jsonl` |
| **Reports** | Loop | readable output — the campaign manifest plus every top-level cycle surface, DERIVED from `CycleLayout` by `store/layout.py::_REPORT_NAMES`, never hand-listed: a surface missing from it is deleted by `--keep-results` |

**The keepsake is not a leaf.** What `delete --keep-results` spares (Reports + the langfuse loop trace) is a cross-cutting subset, surfaced as a one-line UI note — never a summed figure, so the partition stays MECE.

**Ledger writers store once.** A cycle's `init` record never embeds the dataset's rows; round 0's display record drops `round_result`. The bulk of a mature ledger is the arm-walk records (`sample_scored` and its neighbours), which are the live per-sample stream and is meant to be there.

**Jobs (`.promptpotter/jobs/{job_id}.json`, machine-global).** A job records ADMISSION only: its `stage` — `queued | starting | running`, the cycle's own phase words (`jobs/registry.py::JobStage`) — its caps, a refusal, and `released_at` once it hands the slot back; a release is an act, never a fourth stage. What the machine alone knows lives here (the slot, the queue order, the reservation); how its cycle ended is the cycle's ledger. Loaded strictly — a file of an older shape is deleted, not tolerated.

**Identity** is the fifth I/O kind ([`../architecture.md`](../architecture.md) § Identity): OIDC verification at the API trust boundary populates `IdentityContext`, and tokens never appear past the middleware ([`../adr/0002-identity-foundation.md`](../adr/0002-identity-foundation.md) — review-enforced, no standing test). Stage 0 substitutes `default_identity()`.
