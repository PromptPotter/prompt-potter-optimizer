# Persistence, State, and Recovery

Your work lives in `.promptpotter/`: `campaigns/{campaign_id}/` is one campaign per directory, and every cycle (root + forks + diags) sits flat under its `cycles/`.

## Four entities — where each one lands on disk

**Workspace → Dataset → Campaign → Cycle** — owned by [`../architecture.md`](../architecture.md) § Four entities (outermost → innermost). This page adds only the on-disk shape.

- **Workspace** — `projects/{tenant}/`. Every directory is named for what it holds, and the root partitions by lifecycle, which is how "what survives a delete?" is answered by looking.
- **Dataset** — resolved tenant-first: a tenant upload (`projects/{tenant}/datasets/{slug}/`, the `new <file>` ingest path) wins over a repo benchmark (`datasets/{name}/`). An ingested slug is first-class to both `new <slug>` and `resume`, not just the mint that made it.
- **Campaign** — `campaign_id = {dataset}__{rand6_hex}`, minted fresh per `new`. `campaign.json` carries `root_content_hash` (resume's config-drift check), `treatment` (the optimizer it runs — [`../architecture.md`](../architecture.md) § Three identities) and, for a controlled arm, `arm` (the head-to-head it runs under — § The controlled comparison there); none is the id.
- **Cycle** — `cycle_{content_hash[:12]}` (+ `_fork_`/`_diag_` on branches). Path resolution is always `(campaign_id, cycle_id)`.

**There is no Session tier** — owned by [`../architecture.md`](../architecture.md) § A campaign has one root cycle. `active_session.json` is a pointer at your latest launch and holds nothing a cycle needs; there is no `sessions/` tree.

## Active session pointer

`projects/{tenant_id}/.workspace/active_session.json` (`{campaign_id, cycle_id}`) is your latest launch — the workspace root selects the file, so the tenant is the path, not a payload field (`store/session_pointer.py::_active_pointer_path`).

**It names ONE cycle and runs can be many.** A CLI `new`, a browser Start and a fork each overwrite it, so it is the terminal's default target and what an unpinned webapp follows — never "what is running". That is each cycle's own `run_phase` on `GET /cycles`, which the webapp's jobs dock and sidebar marks read; a view the operator pinned moves only when the pointer lands on a new cycle of that same campaign.

- **`new`** mints a fresh campaign + root cycle and overwrites the pointer. Re-running `new` on an unchanged declaration reuses the content-addressed root-cycle id and origin score (cache-served), then diverges from round 1.
- **`resume`** reads the pointer and picks up that cycle. No re-`new` needed.
- **fork** mints a new cycle in the same campaign and retargets the pointer.
- **`--campaign <id>` / `--cycle <id>`** override the pointer for one command, on `resume` as on every run-control verb. Everything a launch needs is the campaign's manifest and the cycle's ledger; nothing is picked separately. **`--tenant <id>`** (default `"default"`) selects the partition under `projects/`.

Every subcommand runs as `python -m promptpotter [--tenant <id>] <subcommand> [options]`. **Loop-mint:** `new`, `resume`. **Lifecycle:** `archive`, `delete`, `unarchive`, `reset`. **Manifest-edit:** `rename` (display name only; `campaign_id` still addresses it), `replace-dataset`. **Run-control:** `pause` (stops a running cycle at its next checkpoint, resumable), `set-limits` (raises or lowers a live or paused cycle's `--max-usd` / `--max-tokens` / `--max-rounds` ceiling, `--max-rounds none` lifting the round cap — how a budget- or round-halted cycle is continued), `cancel-queued` (withdraws a launch still waiting for a machine slot; `pause` refuses one, since no loop exists yet to take it), `skip-searchpoint`, `step-cycle`, `origin-gate` (answers a cycle holding at the round-0 gate — the only answer a run launched without a TTY has in the terminal), `bench` (grades the origin and the selection on the held-out bench set, for the cycle holding the campaign's line — `CampaignConfig.bench_trigger` defaults to `manual`, so no launch sends that pass unasked; refused while the cycle has a run in flight). **Diagnostic:** `verify`, `ab`, `noise-floor`, `seed-screen`, `decision-bank`, `probe-reasoning`. **Read:** `evidence`, `cycles`, `machine-status`. **Maintenance** — the three that REWRITE stored artifacts rather than reading them, all dry-run by default and all refusing while a producer could still be appending: `reindex`, `restamp`, `compact-archive`.

Reads happen by opening the on-disk artifact tree. The read VERBS are the three whose reading is in no file: `evidence`, a comparison ACROSS subjects — a campaign, one branch or one searchpoint, repeated `--subject` and at any L4 depth, and the campaigns' bench headlines side by side; `cycles`, every cycle's `run_phase`, producer and pause reason, derived at each read (`--inside <campaign>::<cycle>` lists an L4 inner run's, and is the only verb that takes it); and `machine-status`, occupancy and the queue, derived across every job on the box.

**`compact-archive` is the only verb that can destroy a measurement.** `compact` moves the fields nothing reads — `hit`, `scored`, and `pipeline_data`'s `reasoning_trace` / `total_time` / `turns` / `step_phases` (`domain/scoring.py::UNREAD_PIPELINE_KEYS`; never a ranking, since a row cannot tell a moved ranking from one that returned nothing and a recall evaluator would grade it a real miss) — out of `panel` answers (a candidate's own walk) into `measurements/cold/{config_key}.jsonl.gz`, stamping each answer it moved from so a compacted row is never mistaken for one that never carried the field. `restore` puts them back row for row, not byte for byte (compact separators, the restored key last in its object); `purge-cold` deletes the cold store, and only that step is irreversible. `origin` and `parent` answers are never eligible — they serve the overwhelming majority of cache replays — and a row carrying `pipeline_data.mean_round_delta` keeps its trace, because the L4 narrative panel needs both.

**The diagnostics are fenced — nothing the loop decides reads one, and nothing may.** The loop does fire one: a round reading 100% runs `verify` on its winner (`application/diagnostics/verify.py::verify_on_saturation`), banked and served like an operator's, and the loop reads none of it. **An operator's paid diagnostic or `bench` is admitted against the account and refused `409 producer_live` on a cycle a run is billing** — one door for every way in, `application/jobs/quota.py::paid_verb`.

- **`verify`** re-scores ONE candidate on search cells it has never met, moving no round and no election — **the canonical way to deepen a winner you want to believe.** It is addressed as `evidence` addresses a searchpoint (`candidate:<campaign>/<cycle>/<candidate_id>`), banks the pass on that cycle's ledger as facts, and reads the fresh cells ALONE beside the round's own, with the lift over C0 paired on every cell both scored (`application/diagnostics/verify.py::read_verify`). `--strategy hard` buys the ruler's highest-δ unseen cells. Depth comes from more samples, never from re-asking a cell: a re-ask measures how noisy the model is, which the loop cannot act on, and against the recursive L4 backend it replays the first answer outright.
- **`ab [--campaign <id>]`** re-derives a campaign's recorded decisions under the current engine/scorer and reports where the change stops carrying over ([`mask-projection.md`](mask-projection.md)). Zero LLM calls. It ablates code, not knobs: PoBB's ε and lock-in floor replay off each decision's recorded `inputs_ref`.
- **`noise-floor`** re-scores a campaign's cached origin `--k` times with `force_fresh`, reading the backend's run-to-run noise.
- **`seed-screen`** scores a seeded bank draw of ANY dataset against that dataset's own origin over repeated passes, and rejects any bank whose constant-answer floor EXCEEDS the origin by more than the measurement's own error bar — such a bank pays a candidate for collapsing to a single label, the degeneracy the instrument exists to catch. Each pass also reports median and mean per-call latency, wire cost, and the share of answers that went to a single label, so screening a candidate *model* needs no second instrument. The gap between the two latency readings says the route is retrying; the answer share is the twin of the floor — the floor says what a constant answer would SCORE, the share says how nearly this model IS one. Its concurrency is `--parallel N`, never the browser's ⇉ control — a look-ahead depth is spent by the round that scored under it, and a screen (N independent draws) has no round to spend one.
- **`decision-bank <dataset>`** grades an optimizer-prompt variant on the decisions a dataset's campaigns already recorded. Each closed round whose proposals the ledger holds is one decision: the cycle's state is rebuilt off its closes as it stood before that round, both arms propose from it, and each proposal is scored on the cells the round's parent was measured on. The reading is the paired difference in lift over the recorded parent, per decision — the best proposal's (an arm with nothing measured holds the parent, 0.0) and the mean proposal's. `--base` / `--variant` are YAML files of optimizer-prompt overrides, the channel an L4 inner cell runs under; with no `--variant` both arms are the base under two seeds, which is the instrument's noise floor. **Without `--max-usd` it sends nothing** and prints what a run would send: decisions, optimizer calls, target cells, how many replay, and the dollar bound.

## Layout

```
.promptpotter/
  projects/{tenant_id}/
    .workspace/
      active_session.json              # { campaign_id, cycle_id }
      events.jsonl                     # workspace ledger — commands with no cycle to address
    head_to_heads/{id}.json             # a declared head-to-head: instrument + per-arm budget
    campaigns/{campaign_id}/            # {dataset}__{rand6_hex}, fresh per `new`
      campaign.json                    # manifest, typed and frozen (`domain/campaign.py::Campaign`):
                                       #   dataset, backend_url, config snapshot, declaration hashes — no run state
      result.json                      # the campaign's result as facts: the cycle holding its line, that line's cost
      log.md                           # campaign digest — root + forks + rounds
      cycles/{cycle_id}/               # root + forks + diags, ALL FLAT
        dashboard.json                 # the BANKED live state (`LiveDashboardState`) — a fold of the ledger
        index.json                     # the cycle's own facts, folded off its ledger — written for you, read by nothing
        export.json                    # the winner + its provenance, for a program that is not us
        pipeline.resolved.yaml         # the declaration this cycle RUNS — backend under dataset overlay
        experiment.resolved.yaml       # the CELLS it measures — the connector's panel, every name it
                                       #   only pointed at resolved. Write-once where the line above
                                       #   is rewritten each resume
        bank_partition.json            # which bank rows the search draws and which it holds out
        optimized.md                   # which of those values the optimizer MOVES, and whether the
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
                                        #   L4 sandbox, but WITHIN this tenant: `build_stores` roots it at
                                        #   `shared_root / tenant_id`. Peer of campaigns/
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
                                        #   nothing (`seed_screen.py` argues it). Only `runs/` is served.
      decision-bank-{dataset}-{ts}.json #   decision-bank's paired reading and every decision under it;
                                        #   it spans campaigns, so it names no single source either
    traces/mlruns/                      # the MLflow sink (regenerable, settings-gated)
    backends/{backend_id}/backend.json  # backend registration + synced API responses
    datasets/  benchmark-rows/  task-context/   # dataset tier — definition, materialized rows, decomposed context
```

**Why this shape.** Telemetry is *temporal* — each cycle owns its `dashboard.json`, so `tail cycles/{cycle_id}/dashboard.json` follows exactly the cycle you're watching. Audit is *structural* — frozen records keyed by the cycle that produced them. Cycles sit flat because a fork tree keyed by `parent_cycle_id` scales where nested fork-of-fork directories don't. `measurements/` is a cross-cycle peer of `campaigns/`, so a fresh `new` on an unchanged declaration cache-hits every origin sample yet still gets its own `campaign_id` and trajectory; `optimizer_reuse/` is its peer on the optimizer's leg and `judge_reuse/` on scoring's.

**`store/layout.py::SHARED_CACHE_DIRS` is the whole of the paid tier** — that tuple is what `reset` preserves and what the workspace storage report counts as shared, so a cache is added to it or it is silently destroyed by the first. Everything else in the root is campaign state, regenerable, or config. There is deliberately no recycle bin: an archived campaign stays in `campaigns/` and is hidden by `campaign.json::lifecycle_status`, so a campaign has ONE home and no enumerator has a second parent to remember. The `langfuse/` mirror is observability only — resume/rewind read solely from the cycle ledger and `measurements/`.

## File reference

| File | Lives at | Content |
|------|----------|---------|
| `campaign.json` | campaign dir | The manifest, one typed model (`domain/campaign.py::Campaign`): dataset, label, `root_cycle_id`, declaration hashes, `backend_url`, lifecycle intent, and the frozen `CampaignConfig` snapshot (single owner — no per-cycle copies). Its one editor is `CampaignStore.update_campaign(**CampaignEdit)` (`Campaign.edited`), which admits only the fields a campaign may change after mint. The criterion the origin locked is NOT written back into it — it is a `ScoringLockedRecord` on the root's ledger, read through `pipeline_resolve.py::frozen_config`. Run state is per-cycle and the ledger's. |
| `dashboard.json` | the cycle's dir | The banked live state (`LiveDashboardState`, strict): round, origin, best, candidates, counters, spend. One stream per cycle, a fold of that cycle's ledger. What a READ adds — `run_phase`, the producer reading, the fork remainder, the armed ceiling — is `ServedDashboard` (`application/served_dashboard.py`), built per request and never on disk. An absent, unreadable or foreign-build file is the ledger folded in its place (`projection.py::fold_at`), never salvaged; only a cycle in check-in, whose ledger has declared nothing to fold from, serves `WarmingDashboard`. |
| `log.md` (campaign) | campaign dir | Campaign digest. |
| `hard_samples.json` | per cycle | The cycle's graded cells on its δ ruler (`domain/cells.py::HardSamples`), rewritten at every round's close. The one such file: campaign scope reads one cycle's (`application/scoring/measurement_log.py::campaign_scope_cycle`), dataset scope is folded per request. |
| `head_to_heads/{id}.json` | workspace | One declared head-to-head (`domain/campaign.py::HeadToHeadRecord`): the instrument every arm is graded under and the budget each may spend. Written once, by its first arm's mint (`new … --arm {id}:{key}`, or `mint-campaign`'s `arm`), which declares it off its own; a later arm adopts its split and budget and, on any other instrument, is refused before anything is minted. Its arms are the campaigns whose `campaign.json::arm` names it — the record lists none, so a mint never rewrites it. |
| `result.json` | campaign dir | What the campaign's line COST (`domain/campaign.py::CampaignResult`): the cycle holding the line and the line's cost — every ledger on it folded, one clock per launch. Rewritten at each launch end, for the cycle holding the line — the root, or where supersede cuts handed it on — so a rebase that ends the run on a fork keeps the headline; an offshoot runs beside the line and grades nothing. The bench passes the headline is read off are the ledgers' (`bench:graded` — the origin's pass, sent once per line and reused by every later launch, and the selection's once graded), read through `runner/bench.py::read_bench` by the head-to-head, the campaign list and the export. Its own file, not a key of `campaign.json`, because the manifest is frozen and this is rewritten. |
| `index.json` | per cycle | `domain/cycle_listing.py::CycleIndex`, written out — the cycle's mint, lineage, ending, result and standing rounds. **Every field is folded off the ledger** by `projections/cycle_index.py::read_cycle_index`, the one reading every listing, lineage walk, guard and render takes; the file has one writer (`write_cycle_index`) and no reader, so deleting it loses nothing and `restamp` rewrites it. A branch's KIND is not stored — `layout.py::sibling_kind` parses it from the id. |
| `export.json` | per cycle | The winning prompt by field name, the node config it ran under, and the provenance a consumer needs to trust the number (fitness under its named formula, n, lift + CI, θ, the rows' hash, the optimizer manifest) — all the optimizer's own reading, beside `bench`, the deployment estimate on rows it never read, stamped with the `scorer_id` it was read under. Written from the same call that appends `cycle_final`; absent when no round ever closed. Contract: `domain/export.py`. |
| `pipeline.resolved.yaml` | per cycle | The declaration this cycle RUNS — the live backend's, under the dataset overlay, as `wiring::_resolve_pipeline_schema` merged it. Written at `init_cycle` and REWRITTEN on every resume, because what the operator is owed is the space the next round will search. It exists because a campaign's committed dataset file deliberately snapshots values and not the backend's `param_keys` (`draft_campaign::merge_pipeline_overlay`), so the served read had every node's settings and none of its axes. Absent until a cycle starts, and the dataset file answers then — which is honest, since no backend has spoken to that campaign yet. |
| `experiment.resolved.yaml` | per cycle | The panel this cycle MEASURED — the connector's `experiment_file` with everything it only NAMES resolved to what it named, so a Harbor campaign carries the task roster its rounds actually ran rather than a pointer into a registry that can be re-pinned under the same version. Beside the declaration because the two answer one question about different halves: that file is the search SPACE, this is the set of CELLS. **Write-once, the opposite cadence to its neighbour** — a declaration owes the operator what the next round will search, a roster owes what every round already measured, and re-pinning it mid-campaign would change what was measured without changing the campaign's name. A later resolution that disagrees is logged, never written. Read in preference to a live re-resolve wherever a campaign's identity is recomputed (`pipeline_resolve.py::resolve_pipeline_for_campaign`). Absent for a connector that owns no panel. |
| `bank_partition.json` | per cycle | The partition `CampaignConfig.dataset_split` declares, as ids: the search pool every optimizer draw reads, the bench set the headline is scored on, the demo pool. Rewritten at every run init, since it is a pure function of the bank and the frozen declaration — ids that move between two runs mean the bank changed. `split: null` holds nothing out. Contract: `domain/bench.py`. |
| `optimized.md` | per cycle | Which of the resolved values the optimizer MOVES, and the channel each reaches the model by — the reading of the declaration beside it that the declaration cannot give, since it names a key and never whether the model will ever see the value. Markdown: its only reader is a person. Same cadence as the declaration. |
| `log.md` / `review.md` (cycle) | per cycle | Per-cycle digests. Derived views — safe to delete and recompute. |
| `readout.log` | per cycle | The live run readout (per-sample HIT/MISS, round summaries, SP tables), ANSI-stripped — the headless tail when you're not watching `dashboard.json::current_round`. **Every launch writes it, whatever started it** — terminal, browser, embedded host, an L4 inner cell — and the browser opens it from the cycle's Files tree. **One file per cycle, appended per launch**: each launch opens with a `Readout:` line, so why the last one stopped sits directly above it. A fork mid-run moves the readout to the fork, and the parent's file ends on the line naming it. The repo-root gitignored `logs/latest-readout-path.txt` holds only the newest TERMINAL launch's readout PATH; a fork inside that launch is named by the last line of the file it points at. |
| `rounds/round_NNNN.json` | per cycle | Serialized `RoundResult` — a CHECKOUT of the round's last `round_closed` record, its rows read back from `measurements/`, written for the operator: no code reads it back, and a fork carries none of its parent's. Its sole writer is `application/scoring/closed_rounds.py::RoundFileProjection`, a ledger subscriber: a close or a restated optimizer state rewrites its round's file, entering a round removes that round's and every later one. |
| `.runtime/ledger.jsonl` | per cycle | Append-only fact stream. Every decision a node takes rides a `ResumeCheckpointRecord` stamped with that node and a `CheckpointKind` — potter's escalation firings are its `l2_escalation_trigger` / `l3_escalation_trigger` — and no separate signals stream exists. **It is also the only surface that says which optimizer node actually RAN**: every `llm_call` record carries `prompt_chars`, and potter's, which alone composes through dispatch panels, adds what each cost it — `injection_chars` / `injection_dropped` / `injection_silent` (`optimizers/potter/dispatch/facade.py`). The round file cannot answer either — its `optimizer_state.prompt_hashes` names every node on every round by construction. **A `candidate_minted` record is an individual's provenance, whole, and a mint is an EDGE SET**: it embeds the individual's `lineage` (`domain/opt_search_point.py::IndividualLineage`), and an individual the ledger already holds is minted again only where a proposal reaches it from a parent no earlier mint names (`runner/round.py::announce_population`) — so a reader folds an individual's in-edges as the union over its mints. |
| `.runtime/streams/round_NNNN_{member}.jsonl` | per cycle | Per-sample race standings, one file per eliminator `member`. |
| `.runtime/cache/rounds/` | per cycle | Per-node optimizer I/O. |

**The records a cycle's facts are.** `cycle_minted` (first record of every ledger; a fork's names its parent, the parent-ledger offset its history begins at, and its `ForkSpec`) · `run_phase` `terminal` with a `stop_reason` (the ending — the runner's own, or the reaper's / a supersede's declared for a cycle whose runner is gone) · `cycle_final` (the result block + `interrupted_round`) · `cycle_superseded` · `fork_graded` (a correction's measured direction) · `intervention` · `spawned` · `run_limits` (the standing ceiling AND the job's reservation beside it) · a bench pass (`PhaseRecord` `bench:graded`, folded by `scan_bench_passes`; `result.json` stores none). A later `running` declaration retires a standing ending and supersede, which is what makes a resume of a finished cycle unfinished again with no file to edit.

Material facts land on disk in human-readable form. Entry points never write campaign artifacts directly — every write rides the per-cycle ledger through two projections (live telemetry + audit). The allowlist is a structural invariant that fails loud; no standing test, see [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md).

**A round is resumed from its close, never from its file.** `resume` and every fork read the prior rounds off the ledger: each round's last `round_closed` record is the round, whole, its rows read back from `measurements/` by the addresses the close carries (`application/scoring/closed_rounds.py::closed_rounds`), and what a round's proposer generated is its `round_proposed` record. Three records decide what STANDS — entering round N displaces N and every later round, a `rewound` entry (a `--from`, a branch minted to continue at N) displaces what was generated for them too, and an `optimizer_state` record restates a closed round's state. Editing a round file therefore changes nothing a resume reads; to continue from a different point, fork with a seed. The close's `optimizer_state` envelope is `{manifest, population, prompt_hashes, payload}`: `population` is the bench's — the individuals the run carries into the next round — and a resume or fork restores it onto `Cycle.population` beside the optimizer's own `payload` (`bench/cycle.py::Cycle.replay_priors`).

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

`RunPhase` (`domain/phases.py`) composes two orthogonal facts — lifecycle (active or finished) and control + liveness — and `RUN_PHASE_INFO` beside it is what each phase reads as on every surface. Derivation is a first-match ladder (`runtime_flags.py::read_run_state`), which is why a check-in cycle never reads as detached:

| Phase | Means | Written by | Derived from |
|---|---|---|---|
| `checkin` | Still authoring its origin — pre-loop, resumable, holds no machine slot | the check-in mint (`cycle_minted` `checkin: true`), until Start appends `checkin_closed` | the mint record, with no `checkin_closed` after it |
| `queued` | A launch holds the cycle, waiting for a machine slot | `claim_cycle` (`launch_claim` `stage: queued`, the job's own stage) | the last `launch_claim`, while its claimant's lock is held and neither a `run_phase` record nor that job's `launch_released` follows |
| `starting` | Past the queue, no process holds the cycle yet | `claim_cycle` (`launch_claim` `stage: starting`) | same |
| `running` | A producer holds the cycle and drives it | the runner (`run_phase` `running`) | producer lock held |
| `gate` | Holding at the round-0 origin gate for an operator decision | the runner (`run_phase` `gate`) | declaration + lock held |
| `paused` | Left cleanly, **active and resumable** | the runner's stop (`run_phase` `paused`, with `cause` + `detail`) — or, before the loop reaches it, an `accepted` `pause-cycle` | the declaration, else the inbox |
| `terminal` | Finished; the reason is its `StopReason` | whoever ends it (`run_phase` `terminal`) | the last declaration |
| `detached` | Active lifecycle, nobody holds it | nobody | no lock, no live claim |

`queued` and `starting` read `producer: claimed`, which every guard treats as attached: a second launch, a delete, a paid verb and a `step-cycle` are refused from the moment the launch is accepted, not from the moment a process exists. A launch that ends without a run appends `launch_released` with what refused it; one killed in the window releases by dying, since its claim stands only while its lock does. The launch stage has ONE vocabulary — `RunPhase` — and one writer of it onto the ledger (`launcher/admission.py::claim_cycle`, from `Job.stage`).

**Which verb a cycle admits now is served, never inferred from the phase** — `RunAdmission` (`domain/phases.py`), on the dashboard, `GET /cycles` and the `cycles` verb, and the dispatcher refuses on the same value. An L4 inner cycle (`RunState.inner`) answers every run verb but the look-ahead with that verb's own sentence, raised by the command as `inner_cycle_unaddressed`.

**Every paused exit names its cause** (`PauseCause`): `command` (a `pause-cycle` the loop took — `detail` names who), `enclosing` (an L4 inner cell under its outer's pause), `interrupt`, `cancelled` (the host cancelled the task), `step` (the launch reached its round allowance), `bound` (a declared bound cut the panel). One seam writes it (`run_observers.py::declare_run_stop`), never each raise site; the dashboard, `GET /cycles` and the `cycles` verb serve it as `pause` (`PauseReading`).

### Liveness — the lock says attached, the ledger grades it

**Whether a producer is attached is its lock on the cycle (`infrastructure/producer_lock.py`) and nothing else.** What GRADES an attached one — `live`, `idle`, `wedged` (`ProducerState`, served as the producer reading beside `run_phase`) — is the LEDGER's mtime and its last non-heartbeat append; a heartbeat is an append. No projection's file time enters it: `dashboard.json` is debounced, so its mtime lags the run it describes.

**`detached` ≠ `paused` ≠ wedged.** `paused` is a clean, deliberate, resumable exit; `detached` means nobody holds the cycle; **wedged** is a producer attached and no longer *progressing*, which is a producer state and not a phase — nothing takes the cycle from under it.

**No silence is reaped.** The liveness reaper (`application/jobs/reaper.py`) stamps `terminal` with `producer_vanished` only the cycle whose last word was `running` and whose lock nobody holds — a long await, a slept machine and a wedge all still hold theirs. **A long await must still heartbeat** — owned by [`application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md) § Conventions; what it buys here is that a quiet producer is not served `wedged` to an operator deciding whether to kill it.

### Commands ride the ledger

`.runtime/` holds no control file and no lifecycle file. A command for a running loop is delivered by the ledger it is recorded on: the dispatcher acks it `accepted`, which arms it in the cycle's inbox (`ledger_scan.py::Controls` — the standing pause, the skips, the look-ahead, the gate decisions); the loop's `RunControl` acks it `applied` at the checkpoint that takes it, which spends it. A new launch's `running` declaration and any stop clear what stood, so a command cannot outlive the run it was sent to — and one sent where no loop will take it (a pause on a cycle nobody holds, a gate decision before the gate holds) is refused at dispatch. An `auto` look-ahead is a mode, so its ack leaves it standing.

**The standing ceiling is ONE store**: the last `run_limits` record on the cycle's own ledger, written whole. A launch reads it and so does a run in flight (`runtime_flags.py::standing_run_limits`, an incremental fold), so `set-limits` from another process binds at the cost of one stat. The round bound is on the same record — `rounds.max_rounds` (the cap) and `pause_at_round` (a `step-cycle`'s allowance) — read at each round boundary (`runner/loop.py::_round_bounds`).

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

**Until an L1 round closes, a resume re-enters round 0.** Every launch re-measures the origin — cached cells replay free, cells never sent or errored are sent — then closes round 0 from that measurement and holds at the origin gate on its verdict. A round-0 file a stopped run left has passed no gate, so no resume reaches round 1 past a partial origin. **One live run per cycle:** a launch targeting a cycle that already has an unfinished job is refused `409 cycle_busy`, naming that job. **A resume of a campaign whose dataset no longer resolves to the origin it was minted on is refused `409 config_drift`** from every way in (`application/jobs/mint.py::refuse_drifted_resume`), unless the edit is policy-only or the cycle's own seed steers the pipeline; the terminal then offers a fresh `new`.

### The primitive

A fork is a new cycle whose FIRST ledger record (`cycle_minted`) names `parent_cycle_id`, the `forked_at_offset` on the parent's ledger its history begins at, and its `ForkSpec`. Its KIND is stored nowhere, because the id already answers it — `layout.py::root_cycle_id` / `::sibling_kind` know exactly two separators (`_fork_`, `_diag_`). Forks land **flat** under `cycles/`; the tree is reconstructed from the mint records, never from directory nesting and never from `index.json`. The parent's ledger gets a `ResumeCheckpointRecord(kind=FORK_CUT)` naming the child's `cycle_id` and the cut round.

A fork owns its own `dashboard.json` and a ledger carrying **own appends only** — the parent's prefix is walked, not copied. **Nothing is copied across at the cut**: the mint is followed by one `round_entered(rewound)` at the round the branch continues from (0 for an offshoot), so every reading of its rounds keeps the parent's before that and nothing after, and the δ rulers are read over the chain the fork CONTINUES (`ledger.py::continued_chain`; an offshoot starts over and inherits none). The standing ceiling is scanned on the cycle's OWN ledger, so a fork never inherits a cap — what it may spend is its seed's declaration, bounded by what its parent has left (`served_dashboard.py::fork_remainder`). **`mint_kind`** is the webapp sidebar label for what minted a cycle (`domain/run_records.py::MINT_KIND_FOR_TRIGGER`, which refuses an unbadged trigger at import); the raw kind is not served beside it, because the browser parses the id for the family tail anyway.

Every cut serializes ONE typed `ForkSpec` to `FORK_CUT.data.fork` + the fork's `cycle_minted.fork`, and its callers differ only in what they fill: **scoring divergence** (trigger/reason/issued_by only) and an **operator-steered fork** (`seed: CycleSeed` + `from_candidate_id`). The primitive does not know which fired — a new caller adds a `ForkTrigger` member and nothing else.

**`from_round` is provenance; `mint_fork(fork_from_round=…)` is mechanics.** The arg says how many parent rounds this cut LIFTS (`0` = a clean offshoot lifting none); the spec field says which round it was CUT FROM. A rebase makes them equal, so the seam back-fills the spec when its author left it unset — but only then. Only a steered cut names `from_candidate_id`, so only it can be labelled by the candidate it came from.

**Three checks for a new fork driver.** If any fails, the primitive has reached its scope and the feature wants its own layer: the driver must be **trigger-agnostic** (a new `ForkTrigger` member and a filled `ForkSpec`, no edits to `mint_fork`'s body); its override must be **OSP-carriable** (a different pipeline shape or scoring formula is a layer above); and it must cause **no data fracture** (no parallel persistence directory, no duplicate of something already in `measurements/`, `rounds/` or the ledger). Library measurements are deliberately not on the tree — content-addressed by `JobSearchPoint.content_hash`, two forks see identical hashes and read the same `measurements/` row, which is why a second fork's origin costs zero LLM calls.

### Rewind — `resume --from N`

Use when the active cycle went somewhere you don't want. `cycle_id` stays; rounds after N are deleted, state is restored from round N, and the run resumes at N+1. The measurement archive is preserved — per-sample results replay without backend calls.

**Partial rounds.** Ctrl+C mid-round (a resumable pause, `StopReason.PAUSED`) leaves ledger events but no `round_closed` record; the public `rounds/round_NNNN.json` stays absent (the audit cache carries the partial with `"interrupted": true`) and the cycle stays non-terminal and resumable. `--from M` is admissible only if round `M` has a closing event — so after a pause mid-round-1, `--from 1` refuses and `--from 0` resumes cleanly.

### Fork — `resume --fork-on-divergence`

Use when a **data-affecting** edit (scoring formula, `pipeline_overlay`, `exclude_nodes`, `dataset_name`) makes resume's replayer find recorded decisions no longer hold. The optimizer halts rather than drift; either revert, or commit with `--fork-on-divergence`. It mints a new `cycle_id` **in the same campaign**, rooted at the divergence point, keeps the pre-divergence rounds by reading them where the parent's ledger closed them, records `parent_cycle_id`, and re-runs the divergent round under the current scorer. The shared archive is not duplicated — both cycles read the same measurements through their own scoring ledger. **Why rewind isn't enough:** rewind restarts under the *same* policy and would re-hit the same divergence.

**A cut has a DIRECTION** — which side the run continues on, written at the cut and served as `fork_direction`. The trigger usually implies it (`FORK_DIRECTION`, derived, so every fork already on disk answers it): a diag / steered fork is an `offshoot`, the child hanging off a line that keeps running; a `scoring_divergence`, `operator_rewind` or L2/L3 rebase **supersedes**, the child being the continuation the pointer moves to and the *parent* what was left behind. Same shape on disk, opposite reading — which is why nothing is deleted on a supersede. A correction is the one cut taken before its consequence is known, so it records the answer it later measured on `ForkSpec.direction`, which outranks the derived default; that is the only way `equivalent` arises. **How a cut READS once served** — the timeline renumber, which side wears `superseded_by`, which cycle speaks for the campaign — is owned by [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § The lineage tree. What THIS layer must get right is that the direction, and how far it reaches, are on disk before any reader asks.

**A cut retires only as far as the branch actually got** — owned by [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § The lineage tree; the write side hands the branch exactly the candidates it retires (`repair.py::_rebank_on_branch`), and the reach is read back off the last round the branch's own ledger minted a candidate for, so the two sides cannot drift.

**A supersede retires the parent, on disk, at the cut** — `mint_fork` appends `cycle_superseded` and, where no ending already stands, declares the parent terminal with `StopReason.REBASED` (`CampaignStore.mark_superseded`). The parent stops writing *by design*, and an undeclared deliberate stop would read as a crash: `detached`, then `producer_vanished` at the reaper's next sweep. Resumability is untouched — a later launch declaring `running` retires both records. An **`equivalent`** cut moves the pointer the same way and retires nothing.

**After a REPAIR both sides carry the same `candidate_id`** — owned by [`infrastructure/CLAUDE.md`](../../promptpotter/infrastructure/CLAUDE.md) § The lineage tree (*identity outranks direction*); the withdrawn measurement is the one the round was actually steered by. A retired candidate wears **no crown**: it was elected over rows the cut replaced, so its `reading.election` wears no `selected` and no `crown` until the branch re-elects.

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

**One verb, two acts, and `keep_rounds` is which.** Unset it is the steer above: a clean offshoot from the origin, rounds numbered from 1, re-scoring the edited searchpoint. Set, the same command mints an `operator_rewind` — rounds `0..N-1` are LIFTED and the branch continues at N under the seed's `config_overrides`. That is the shape an applied scoring mask takes ([`mask-projection.md`](mask-projection.md) § Applying one). It refuses a seed carrying `origin_prompt_fields`: the lifted round 0 already IS the origin. The terminal's equivalent gesture is `resume --rewind N`.

**Pause run.** The webapp button and the CLI `pause` verb fire the same `pause-cycle` command through the same `CommandDispatcher`, which records it on the cycle's ledger (§ Commands ride the ledger). The loop takes it at its next checkpoint and the worker exits cleanly, but the cycle stays **non-terminal and resumable**, declared `paused` with `StopReason.PAUSED` and cause `command` — so *who asked* is on disk. `python -m promptpotter pause` targets the active cycle (`--campaign` / `--cycle` to name another, `--reason` to annotate). Ctrl+C at the producing terminal is the same command (`cli/commands/launch.py::_terminal_inputs`) and the fastest route, since it also cancels the run instead of waiting for a checkpoint.

**An inner cycle stops when its owner does.** Its `RunControl` carries the cycles it measures for (`enclosing`, outermost first) and folds each one's inbox beside its own, so a pause asked of the outer stops the inner at its next checkpoint, declared `paused` with cause `enclosing` — where one outer *sample* is an entire inner run.

**Make a slow round finish sooner — the look-ahead control.** The remote's **⇉** control runs the round with several calls in flight instead of one — its candidates walk together and decide where a serial round would — cutting its scoring wall clock roughly in proportion. Suggest it whenever someone asks why a round is taking so long; it is the only speed lever needing no config change and no restart. **Every clause of it** — who may press, what one press buys, why the overshot sample is discarded — is owned by [`access-model.md`](access-model.md) § host-admin ↔ user. What this layer must hold is the on-disk half: the operator's *request* is the accepted `set-sample-lookahead` command in the cycle's inbox and what the loop actually ran at is the served dashboard's `sample_lookahead`, never the request served as that.

## CLI flags — `new` and `resume`

`new <name>` mints a fresh campaign + root cycle from an authored `datasets/<name>/` and runs from round 0. `new <file>` (a CSV — `Path.is_file()`) parses the file into a durable check-in campaign, runs the AI origin check-in (the same `checkin` node the web ingest uses), auto-confirms high-confidence findings, and — once the readiness gate passes — flips the check-in to `active` and runs the loop inline. If a gap survives the resolver, `new` prints the open fields + questions and exits non-zero — nothing is minted on a guessed default; confirm with `--set` and re-run. After a successful file run the committed slug is first-class to `new <slug>` / `resume`.

**The flag set is `presentation/cli/commands/verbs.py`** — every row a page like this could carry was its `help=` string one `--help` away, and the two drifted. Read the flags there; this page owns what they do to the tree, above.

### Interrupt handling

- **First Ctrl+C** — records `pause-cycle`, banks completed work, declares the cycle `paused` (resumable), exits **130**. A sent call is cancelled only where cancelling stops its bill (`Connector.cancel_stops_billing`); elsewhere it lands.
- **Second Ctrl+C** — force-quits immediately.

An interrupt mid-round leaves ledger events but no closing `round_closed` record — see **Partial rounds** under Rewind for which `--from N` offsets are then admissible. Nothing is left running to hunt for: a cancelled cell is torn down by the process it belongs to, and only a hard kill leaves anything, which § Where the error text is covers.

## Will a config change re-score? — the measurement cache

The single most-asked operating question: *"I edited a connector tunable — will the next run actually re-measure, or replay the old score?"* Four facts answer it. (`configure_and_apply_pipeline` applies `exclude_nodes` + `pipeline_overlay` and returns the `pipeline_params` that flow unchanged through both `new` and `resume`; a `None` result means the backend runs its full pipeline.)

1. **The measurement key includes the connector config, model included.** Per-sample results pool in `measurements/` keyed by `node_configs` — the effective per-node config derived from the overlay-merged `session.pipeline_params` — and by the sample's content (`Sample.key`), never its dataset or its position: a sample two datasets share is measured once. On a config change at node *N*, the prefix match breaks at *N*: **every sample whose pipeline ran past *N* is re-measured**; only samples that short-circuited upstream replay. So changing `entity_profiling.model` from `120b` to `20b` genuinely re-scores the LLM-path samples.

2. **A running/resumed campaign uses its FROZEN `CampaignConfig` snapshot.** `campaign.json` owns the config; editing `datasets/{name}/campaign.yaml` or `pipeline.yaml` does **not** change an existing campaign. To apply a connector-config change, mint a fresh `new` — it reads the edited dataset configs, gets a fresh `campaign_id`, and re-scores a new origin. For an *in-place* re-explore after a data-affecting edit, that is `--fork-on-divergence`.

3. **The cycle id is config-aware, and agrees with the measurement key.** `cycle_id` / `Campaign.root_content_hash` are built by `build_origin_cycle_id` from the SAME overlay-merged params the measurement key hashes, so two origins differing only by model get **distinct** `cycle_id`s.

   **Invariant — always key a surface by `(campaign_id, cycle_id)`, never `cycle_id` alone.** A direct consequence of the content-addressed id: two campaigns on the same dataset+config share the **same** root `cycle_id` and differ only in their random `campaign_id`. Every persistence path already resolves as the pair, and the webapp keys its unit map by it, so two same-dataset campaigns render distinctly. Any **new** read/write surface MUST carry the campaign id too; a lookup by bare `cycle_id` would cross-wire siblings.

4. **On L4 the identity inputs are NOT frozen — editing one mid-campaign re-measures the origin.** `application/runner/inner/connector.py::_identity_config` fingerprints the inner cells' **treatment** (`SelectedOptimizer.treatment()` — each node's prompt body, its resolved response schema, which is prompt text riding every call as `response_format`, and its config; every member's knobs; and **the source deciding what each node is handed**, the runtime's `source_digest` — potter's hashed set also carries its per-node layouts and its library), **the estimator's own source** (`_measurement_source_digest`), and the inner benchmark's `pipeline.yaml` node configs *and* `campaign.yaml` — the worker model and the scoring formula included, since either changes what every cell measures. Both source digests are normalized through the AST, so a comment costs nothing while an expression voids the origin. Fact 2 does not cover these — they are read live, not snapshotted — so an edit lands on the *running* campaign: the banked outer origin stops joining and the next round pays to score it again. **Land config fixes before an origin is measured, never between its rounds.**

   **Deliberately NOT in it, because a corpus that cannot survive them cannot accumulate:** the manifest's non-inner nodes (`checkin`, descriptions, `available_models`), `APP_VERSION`, and the `inner_tasks.yaml` roster — each seat is its own sample's `source_pin`, so adding or editing one re-measures that seat alone. The version constant voided every banked cell on every release while saying nothing about whether the measurement had changed.

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

Swapping it between rounds would make the composite **incomparable to its own past** inside one cycle, silently. `EscalationFSM._improved` — the L2/L3 stall gate — asks whether the cycle's best advanced since a layer fired, and answers on `best_composite_fitness` whenever the θ ruler is unavailable. Redefine the composite mid-cycle and that comparison reads a change of scale as progress or as stall, with nothing to error on. `POST /commands/change-scoring-composite` is declared in [`../specs/api-openapi.yaml`](../specs/api-openapi.yaml) and carries `x-status: declared-not-wired`; wiring it as specified would reintroduce exactly this.

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
| **Connector** | — | `.runtime/cache/**` + the per-sample `results`/`all_candidate_results` arrays carved from the public `rounds/round_*.json` |
| **State** | Loop | the round checkouts — non-array remainder of `rounds/round_*.json` (what a resume reads — each round's close, the cycle seed — rides the ledger, so it lands in **History**) |
| **Trace** | Loop | telemetry — `.runtime/streams/`, `prompts/`, `langfuse/{traces,observations,scores}/` |
| **History** | Loop | the durable event spine — `.runtime/ledger.jsonl` |
| **Reports** | Loop | readable output — the campaign manifest plus every top-level cycle surface, DERIVED from `CycleLayout` by `layout.py::_REPORT_NAMES`, never hand-listed: a surface missing from it is deleted by `--keep-results` |

**The keepsake is not a leaf.** What `delete --keep-results` spares (Reports + the langfuse loop trace) is a cross-cutting subset, surfaced as a one-line UI note — never a summed figure, so the partition stays MECE.

**Ledger writers store once.** A cycle's `init` record carries a dataset *reference* (`dataset_size`), never an embedded copy of the rows; round 0's display record drops `round_result`. The bulk of a mature ledger is the arm-walk records (`sample_scored` and its neighbours), which are the live per-sample stream and is meant to be there.

**Jobs (`.promptpotter/jobs/{job_id}.json`, machine-global).** A job records ADMISSION only: its `stage` — `queued | starting | running`, the cycle's own phase words (`jobs/registry.py::JobStage`) — its caps, a refusal, and `released_at` once it hands the slot back; a release is an act, never a fourth stage. What the machine alone knows lives here (the slot, the queue order, the reservation); how its cycle ended is the cycle's ledger. Loaded strictly — a file of an older shape is deleted, not tolerated.

**Identity** is the fifth I/O kind ([`../architecture.md`](../architecture.md) § Identity): OIDC verification at the API trust boundary populates `IdentityContext`, and tokens never appear past the middleware ([`../adr/0002-identity-foundation.md`](../adr/0002-identity-foundation.md) — review-enforced, no standing test). Stage 0 substitutes `default_identity()`.
