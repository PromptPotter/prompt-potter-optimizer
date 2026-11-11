# Persistence, state and recovery

Work lives in `.promptpotter/`: one campaign per `campaigns/{campaign_id}/`, every cycle (root, forks, diags) flat under its `cycles/`; a campaign is paused, resumed, rewound or forked, never finished.

## Four entities on disk

The on-disk shape of [`../architecture.md`](../architecture.md) § Four entities.

| Entity | On disk | Id |
|---|---|---|
| Workspace | `projects/{tenant}/` | the tenant |
| Dataset | tenant upload `projects/{tenant}/datasets/{slug}/` first, then shipped template `promptpotter/assets/datasets/{name}/` | its content; the slug is a label |
| Campaign | `campaigns/{campaign_id}/campaign.json` | `{dataset}__{6 hex}`, fresh per `new`; a nested name's `/` is written `.` |
| Cycle | `campaigns/{campaign_id}/cycles/{cycle_id}/` | `cycle_{content_hash[:12]}` (+ `_fork_`); a path resolves as `(campaign_id, cycle_id)` |

### The hierarchy address

Every served entry (`GET /hierarchy`, [`webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § The served hierarchy) is keyed by an address the server mints and never renames:

```
campaign := [inside "~"] campaign_id                 bbeh__7c2e9a
course   := campaign "::" cycle_id                   bbeh__7c2e9a::cycle_7f3a
round    := course "/r" round_n                      bbeh__7c2e9a::cycle_7f3a/r2
candidate:= round "/" arm_key                        bbeh__7c2e9a::cycle_7f3a/r2/<ArmKey>
step     := course "/@" offset                       bbeh__7c2e9a::cycle_7f3a/@418
inside   := course ("~" course)*                     the L4 sandbox chain, the `?descend=` wire byte-exact
```

A step's offset is the line index in its course's own ledger. A fork is stored as a sibling cycle, served as a child course.

## Active session pointer

`.workspace/active_session.json` is the latest launch: the cycle the latest run held (`new`, a browser Start, a fork, a resume, from any door), written where the run takes its producer lock, so a launch refused before its run holds moves nothing. It is what a verb naming no cycle acts on (`cycle_addressing.py::latest_launch`); `--campaign` / `--cycle` override it for one command. What a follower shows (`/sessions/active`: `/stage/`, an unpinned webapp) is `followed_cycle`: a cycle a run holds, the pointer's first, else the pointer.

## Verbs

`python -m promptpotter <verb> --help`. What `--help` does not say:

- **`compact-archive` is the only verb that can destroy a measurement**; its `purge-cold --apply` is irreversible and `origin`/`parent` answers are never eligible.
- `reset`, `land-data`, `close-admission` are the data-root verbs — [`../decisions.md`](../decisions.md) § 0011.
- Nothing the loop decides reads a diagnostic (`seed-screen`, `probe-reasoning`, `noise-floor`). A paid diagnostic or a held-out pass holds a machine slot and an account reservation, refused `409 cycle_busy` / `409 machine_busy` rather than queued.

## Layout

The SURVIVOR and CACHE files — their paths and the shape each must keep across a release — are [`../specs/data-root.generated.json`](../specs/data-root.generated.json), generated from `infrastructure/store/data_root.py::KINDS`, whose row names every file's fate; `reset` and `land-data` are that table applied ([`../decisions.md`](../decisions.md) § 0011). What the generated file cannot say is the RUN data a reshape wipes, and which file a reader should open:

```
projects/{tenant_id}/
  .workspace/                         # run data: `reset` drops it with the campaigns
    active_session.json
    events.jsonl                      # workspace ledger — commands with no cycle, uploads, refusals
    commands.jsonl                    # the account's command log
  drafts/{draft_id}/                  # an open check-in, until Start or Discard
  campaigns/{campaign_id}/
    campaign.json  log.md
    cycles/{cycle_id}/                # root + forks + diags, ALL FLAT
      dashboard.json  index.json  export.json  optimized.md
      log.md  review.md  readout.log
      rounds/round_NNNN.json
      .runtime/
        ledger.jsonl                  # append-only spine
        producer.lock                 # what "attached" means
        pass.lock                     # held while a paid pass, not a loop, holds the cycle
        job.log                       # a detached launch's stderr
```

**A reshape of run data's on-disk form wipes it at the landing, counted first** (`ls .promptpotter/projects/*/campaigns`); the archive and every survivor row stay untouched. An archived campaign stays in `campaigns/`, hidden by `lifecycle_status`.

## File reference

Each file's contract is its model; this says only what the type cannot.

| File | What the type does not say |
|---|---|
| `campaign.json` | One editor. The origin's locked criterion is a record on the root ledger, never written back. |
| `dashboard.json` | A fold of the ledger; a read serves the fold, never the file. |
| `index.json` | One writer, no reader — readers fold the ledger. |
| `rounds/round_NNNN.json` | The round's last statement, for the operator; a round resumes from its close on the ledger, never from its file. |
| `log.md`, `review.md` | A projection of the ledger nothing reads back, rendered at each round close and once more at its drainer's close, so they name the stop. The per-node I/O is served at `GET …/rounds/{n}/audit`; no file holds it. |
| `.runtime/ledger.jsonl` | The only surface saying which optimizer node RAN (`llm_call` records); an `arm` record carries the individual's whole lineage. |

**The cycle runs on ledger records read over the fork chain**: the bank partition is appended at mint and a fork reads its parent's; `resolved_run`'s `read` (the search SPACE, `PipelineRead.declaration`) last wins, its `experiment` (the CELLS) first wins; a backend revision drift is served, never refused.

## Diagnosing a live or stuck run

**Ledger tail → `cycles` → process table by command line → only then mtimes.**

1. **Ledger tail** (`.runtime/ledger.jsonl`) — the chronology; what the loop was asked is the account's `.workspace/commands.jsonl`, each `command`'s `command_ack`: `accepted` (armed), `applied` (taken), `rejected` (no loop will), `voided` (its run ended untaken), `cancelled` (its act was interrupted).
2. **`python -m promptpotter cycles`** — derived `run_phase`, the producer reading, the pause cause.
3. **Process table, by command line** — which process holds the cycle.
4. **Mtimes** — only to date what the above explained.

A command is taken at the next **per-sample** checkpoint, so a pause asked mid-candidate looks like a freeze for seconds.

### Phase and liveness

`run_phase` is derived from the ledger and the producer lock, never written; `dashboard.json::declared_phase` is the runner's own record and says `running` forever after a `kill -9`. **Attached is the producer lock and nothing else**; `live` / `idle` / `wedged` is graded off the LEDGER's mtime, never `dashboard.json`'s (debounced). The reaper stamps `producer_vanished` only on a `running` cycle whose lock nobody holds.

### No control file; the budget rides the ledger

`.runtime/` holds no control file. The standing budget is the last `standing_budget` record on the cycle's ledger; its money axes are the campaign's, the last `campaign_ceiling` record on the ROOT's ledger.

### Where the error text is

A failed cell's `ErrorCategory` and message land in the latest `rounds/round_NNNN.json` and `readout.log`. A death with no terminal record is machine-sleep or session-end class. Only a hard kill leaves containers behind; the next run on that backend sweeps them.

## Recovery: resume, rewind, fork

| Workflow | Command | Effect |
|---|---|---|
| Resume | `resume` | Continue the active cycle from its latest closed round. |
| Rewind | `resume --from N` | Same `cycle_id`; rounds after N displaced; resume at N+1. Admissible only if round N closed. |
| Operator rewind | `resume --rewind N` | Fork a sibling lifting rounds `0..N-1`, continue at N under the fork's overrides. |
| Fork on divergence | `resume --fork-on-divergence` | Mint a sibling cycle rooted at the divergent round and continue. |

Until an L1 round closes, a resume re-enters round 0 and asks the origin gate again; an origin the gate halted, resumed still unhealthy, passes on that resume. Refusals, from every way in: `409 cycle_busy`; `409 config_drift` (the dataset no longer resolves to the minted origin, unless policy-only); `409 ledger_unreadable` (remedy `delete-campaign <campaign>`, which keeps the caches). **Which edits need a fork** is a knob's `Scope`: `POLICY` resumes in place; `DATA` and `IDENTITY` run divergence detection and halt rather than drift — revert, or fork. **A panel fork off a candidate that edits nothing inherits that candidate's measured C0** and buys no origin re-measure; a steer or seed re-measures it.

### The primitive

A fork is a new cycle whose first record names its parent, the offset and the rounds it LIFTS (`0` = clean offshoot). Nothing is copied: the parent's prefix is walked. A fork inherits no standing budget (its money is the campaign ceiling).

**A cut has a DIRECTION**: a diag or steered fork is an `offshoot`; `scoring_divergence`, `operator_rewind` and an L2/L3 rebase **supersede**, retiring the parent at the cut unless a producer holds it; a correction's measured answer is the only source of `equivalent` ([`../../promptpotter/infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree). **Every resume first repairs** cells recorded without a measurement, on a branch cut first and graded second so the parent stays byte-identical; a hole is plugged with a real measurement, never an archive row.

### Human in the loop

HITL is the fork primitive: the webapp **Steer & fork** and the terminal's `--steer` / `--rewind` fork a sibling; `keep_rounds` picks the act, the shape an applied mask takes ([`mask-projection.md`](mask-projection.md) § Applying one). The button, `pause` and the first Ctrl+C all fire `pause`. **The arm gate** (`optimization.arm_gate`: `off` · `review` · `author`) holds a round before any arm is measured; `submit-arms` answers it, banked so a resume re-holds only where no answer stands. An inner cycle stops when its owner does (cause `enclosing`).

## Will a config change re-score? — the measurement cache

The measurement key is `node_configs` (overlay-merged `pipeline_params`, model included) plus the sample's content, never its dataset or position; a change at node *N* re-measures every sample whose pipeline ran past *N*. A running campaign runs its minted DECLARATION: editing `dataset.yaml` changes no existing campaign. Two campaigns on one dataset+config share a root `cycle_id`, so key a surface by `(campaign_id, cycle_id)`. **On L4 the identity inputs are read live** — land config fixes before an origin is measured. **Concurrent walks buy a cell once**: claim by OS file lock, share the row when it returns; `force_fresh` claims nothing. **A cell holding several rows replays its first deliberate one** (a live row before a deprecated one, grade A before B, then the oldest), so a `force_fresh` diagnostic rescore files beside the run's answer and never displaces it.

## Changing the composite formula — fork, never swap

The scorer compiles once at run init: edit `campaign.json::scoring`, then `resume --fork-on-divergence`; a mid-cycle swap makes the composite incomparable to its own past. A `per_cell` formula is what θ is fit on, the only route a latency, cost or reliability term reaches the election; keep correctness `per_sample`. `target_prompt_chars` is a fact about the CANDIDATE, so a length penalty's normaliser is a constant in the formula — the scorer id hashes formula text alone.

## Lifecycle — archive, delete

`archive-campaign` is a flag, restorable by `unarchive-campaign`; `delete-campaign` removes the tree, or with `--keep-results` spares the keepsake. Both refuse only while a cycle is LIVE, record on the **workspace** ledger first, and never touch the measurement store. A surface missing from the data-root table is deleted by `--keep-results`.
