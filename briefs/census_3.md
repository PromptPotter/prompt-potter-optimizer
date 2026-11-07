# Wave 2 — census 3 (read-only, origin/batch/5 8a263c2b8, 2026-11-07)

Every grep below ran as `git grep -n <pattern> origin/batch/5 -- <paths>` unless stated. The extra
paths `paper scripts tests webapp docs .claude deploy-linux .env.example pyproject.toml README.md
examples notebooks dev .github CHANGELOG.md` were swept for every fact. batch/5 holds main plus
batches 4 and 5 (K–T) and counts as landed. w2u (docs-only, roadmap milestone headings) is in flight
and ignored. Arc letters skip **U** (w2u) and **X** (the w2x fix passes).

Digest reach was measured on a `git archive origin/batch/5 promptpotter` export with census 2's
probe (`scratchpad/census3/probe_reach.py`: `nodes.optimizer_prompt_shapers` replaced by a recorder
of the same walk, every `optimizers.MANIFESTS` treatment installed, plus
`runner/inner/connector.py::measurement_modules()`). On batch/5 the recorder files every treatment
under one key, so the reach below is the union over treatments: 26 modules hashed whole, 1088 units
reached. A second probe (`scratchpad/census3/digest_probe.py`) computes what
`scripts/offline_run.py::digests()` computes. It ran on the export, then on a copy carrying every
production edit of V, W, Y, Z, AA (option 3, then again with a parked word), AB
(`scratchpad/census3/apply_edits.py`). **All six digests are byte-equal**:
`estimator 784922e722ef7a3a`, `capo e105a07bf147ca5e`, `capo-pobb 2992a36aa41aab5d`,
`gepa 933eef981a877549`, `levi 9e08a41bf5396428`, `potter 26598fc48b15541e`. AC and AD are webapp-only,
and AE is `main.py`, which the walk does not reach.

The modules hashed whole that matter here:
- potter's dispatch modules, the capo/gepa/levi `members` and `prompts`, `pools.members`,
  `variation.members`, `paper_templates`;
- `domain.pipeline_parsing`, `domain.ruler`, `shared.answer_text`, `shared.composite`,
  `shared.plugin_registry`;
- the estimator's `bench.difficulty`, `intelligence.rasch`, `scoring.metrics`, `scoring.selection`,
  `domain.l4.proxies`.

The reached units that matter here:
- `domain.phases`: `CampaignPhase`, `RunPhase`, `STOP_REASON_INFO`, `StopCategory`, `StopLoop`,
  `StopOutcome`, `StopReason`, `StopReasonInfo`, `stop_reason_outcome`. Not `RunState`,
  `RUN_PHASE_INFO` or `RunAdmission`.
- `shared.errors`: 24 units (`FileKind`, `NoApiKeyError`, `HostAllowanceExhaustedError` among them).
- `runner.output::_fork_summary_from_index`, `scoring.closed_rounds::_with_rows`.
- `jobs.coupon` (10 units). `jobs.quota` is not reached.

Unreached, among the modules these arcs edit: `scoring.search_point_scorer`,
`scoring.sample_measurement`, `initialization.session`, `initialization.wiring`, `evidence.read`,
`jobs.quota`, `commands.launching`, `jobs.launcher.run_job`, `config.paths`, `main`; all of
`infrastructure` is plumbing, which stops the walk.

**Tests run.** Two pytest runs on a `git archive origin/batch/5` export (`promptpotter tests scripts
research pyproject.toml paper/bbeh-comparison examples`), each as
`PROMPTPOTTER_HOME=<scratch> .venv/Scripts/python.exe -X utf8 -m pytest -p no:cacheprovider
tests/test_archive.py tests/test_identity.py tests/test_ruler.py tests/test_scoring.py
tests/test_security.py tests/test_spend.py`. These six files are every test file that builds a
`Session` (`loop_session(`, `seat(`, `Session(`), and they hold 28 tests.
- With the archive switch forced on (W's question): 1 real failure,
  `test_spend.py::test_a_run_bills_each_cell_once_whatever_its_process_lookahead_or_resume` at
  `:1048`. Two `test_identity` failures were the export missing `research/`; both pass once it is
  exported.
- With every production edit above plus W's test edits (`scratchpad/census3/apply_test_edits.py`):
  **28 passed, 0 failed**.

Line numbers in `docs/specs/code-debt-cleanup.md` (the ledger) are batch/5's. The batch-5 fix pass
edits the ledger before these arcs run, so grep each row's bold title.

---

## Chains (pairwise-disjoint files; only the ledger is shared, in disjoint hunks)

| Chain | Arcs, in order | Hand-edited files | Regenerated |
|---|---|---|---|
| 1 — the session, the scorer, evidence | **V** `BackendClient.cell_hop` → **W** `Session.backend_id` → **Y** `_cycle_facts` re-read | 11 | none |
| 2 — jobs, phases, the run's files | **Z** the concurrent-cycles rule → **AA** `start_refusal` + the declined pause → **AB** the job log in its cycle → **AE** the shutdown notice | 10 | `types.generated.ts` only if AA takes option 1 or 2 |
| 3 — webapp names and styles | **AC** `standingIdx` + `.ov-interval-rows` → **AD** the pipeline renderer's styles in one file | 8 | none |

**Every arc's files are disjoint from every other arc's**, not only across chains. The chains order
by kind: deletions first, then the consolidations. So any link may run beside any other, two or three
at a time.

**The ledger hunks** per chain (batch/5 lines):
- chain 1:
  - 297-300: W deletes;
  - 395-396: V deletes;
  - 1230-1233: Y deletes.
- chain 2:
  - 179-182: AA deletes;
  - 250-255: AB deletes;
  - 1306-1309: Z deletes;
  - 1550-1552: AA deletes;
  - 1597-1602: AE deletes;
  - 1667-1668: AB deletes (the parent line and its one sub-bullet).
- chain 3:
  - 931 and 937: AC deletes two bullets of § The candidates surface;
  - 1115-1118: AD deletes.

The nearest cross-chain pair is chain 2's 250-255 and chain 1's 297-300: 41 untouched lines lie
between them. Chain 1's 1230-1233 and chain 2's 1306-1309 have 72 between them.

**One run-data kind moves (AB).** `jobs/logs/*.log` (Fate.RUN) becomes `cycles/{id}/.runtime/job.log`.
A data root that still holds the old directory refuses `land-data` ("a file no row names"). The desk
holds 10 (`ls .promptpotter/jobs/logs | wc -l`). No other arc reshapes run data or a survivor.

---

## Chain 1

### V — `BackendClient.cell_hop`, the last forwarder beside `.connector`

**Row:** "**`BackendClient.cell_hop` is the last forwarder beside `.connector`**" (395-396). Deleted.

**Owns (2 hand-edited):** `promptpotter/infrastructure/backend.py`,
`promptpotter/application/scoring/sample_measurement.py`, and ledger hunk 395-396.

**Greps:**
- `cell_hop` in `promptpotter tests scripts paper docs .claude webapp/lib webapp/components`.
- `inner_fn` in `promptpotter tests`, for the heartbeat's signature.
- `CycleHop` in `backend.py`, for the import the forwarder alone uses.
- `forwarder\|client\.connector\|BackendClient` in `*.md` and `.claude`: no doc names the forwarder.

**Holders:**
- `backend.py:173-175` `BackendClient.cell_hop`: deleted, with the blank line after it.
- `backend.py:65`: the `TYPE_CHECKING` import of `CycleHop` goes; the forwarder was its one user.
- `sample_measurement.py:607` (inside `_send_cell`'s heartbeat): `inner_fn=lambda: client.cell_hop(sample, wire_params)`
  reads the declaration. Take `cell_hop = client.connector.cell_hop` before the call, and pass
  `inner_fn=None if cell_hop is None else functools.partial(cell_hop, sample, wire_params)`. A
  `partial` rather than a lambda, because mypy does not carry the `None` narrowing into a lambda body.
  `heartbeat.py:45` already takes `inner_fn: ... | None = None`, and `_safe_read` reads `None` as no
  read, so a connector with no `cell_hop` now passes no closure at all.
- Not holders:
  - `connectors/protocol.py:169` (`Connector.cell_hop`, the declaration);
  - `runner/inner/connector.py:134, 149` (the L4 connector's declared hop);
  - `runner/inner/spawn.py:341` (`inner_cell_hop`);
  - `run_records.py:626` (a comment naming `Connector.cell_hop`).

**Digest:** `sample_measurement` is unreached and `infrastructure` is plumbing; equal (probe above).

**Served deltas, named:** none. Every beat's `inner` reads the same hop.

**Equals-main checks:**
- Full gate.
- `--digests` byte-equal.
- `--against <main home>`: decisions and requests unmoved.
- `git grep -n "def cell_hop" -- promptpotter/infrastructure` is empty.

**Estimate:** +3 / −6; the ledger −2.

**Risks:** an L4 inner cell's heartbeat names its inner hop only through this path. The offline
run's L4 leg reads it. Salvage overlap: m4-2 (`backend.py`, `sample_measurement.py`).

### W — `Session.backend_id`, deleted

**Row:** "**`Session.backend_id` is a test-only archive switch**" (297-300). Deleted.

M moved the three name readers to the connector (`origin.py`, `spawn_context.py`, `new.py`). That
leaves one reader, `search_point_scorer.py`, which reads only whether the id is empty. Production
sets it at one site, `wiring.py:274`, to `connector.name`, which is never empty. So the field is
deletable, and the archive is simply always on.

Census 2 assumed the delete needed a named test-side no-archive door. **It does not.** With the switch
forced on, exactly one assertion fails (see Tests run). `test_spend.py::_round` compares its rows
with each cell's archive address still on them. That address is
`f"{file_key}.{uuid4().hex[:12]}"` (`measurement_archive.py:397`), so it differs on every filing.
Its sibling helper `_walk` already strips it (`:838`, `cell.filed(dataclasses.replace(cell.facts,
answer=None))`). So the row's "a test asks for no archive explicitly" becomes `_round` reading rows
the way `_walk` does. No new door is built.

**Owns (8 hand-edited):**
- `promptpotter/application/initialization/{session.py, wiring.py}`;
- `promptpotter/application/scoring/search_point_scorer.py`;
- `promptpotter/infrastructure/store/measurement_archive.py` (a docstring);
- `tests/{factories.py, test_archive.py, test_scoring.py, test_spend.py}`;
- ledger hunk 297-300.

**Greps:**
- `backend_id` in the full path set: the holders below; then `/backends/{backend_id}` and
  `BackendHealthResponse.backend_id`, which are another id (batch-5's fix pass takes the latter).
- `Session(` in `promptpotter`: one constructor, `wiring.py:272`.
- `loop_session(\|seat(\|Session(` in `tests`: six files, all in the runs above.
- `replace(session` / `backend_id=` in `promptpotter paper scripts examples`: no other writer.

**Holders:**
- `session.py:100`: `backend_id: str` deleted.
- `wiring.py:274`: `backend_id=connector.name,` deleted.
- `search_point_scorer.py`:
  - `:216` (`_ArchiveRecorder._file`): `if not cells or not self.session.backend_id:` becomes
    `if not cells:`.
  - `:305` (`open_walk`): `backend_id = session.backend_id` deleted.
  - `:311`: `replaying = bool(backend_id) and not force_fresh` becomes `replaying = not force_fresh`.
- `measurement_archive.py:282`: the docstring "Keyed by content, never ``backend_id``" names a field
  that no longer exists. It becomes "Keyed by content, never by the connector".
- `tests/factories.py:413, 425` (`loop_session`) and `:471, 496` (`seat`): the parameter and its pass
  go.
- `tests/test_archive.py:205`: `backend_id="b",` deleted.
- `tests/test_scoring.py`:
  - `:1151, 1213` (`_peer_cycle`'s parameter and pass): deleted;
  - `:1503, 1716, 1800, 1848`: the kwarg is deleted.
- `tests/test_spend.py`:
  - `:804` (`_walk`) and `:1250`: the kwarg is deleted.
  - `:913` (`_round`): `"rows": [list(walk.results) for walk in walks]` becomes
    `[[cell.filed(dataclasses.replace(cell.facts, answer=None)) for cell in walk.results] for walk in walks]`,
    `_walk`'s own reading at `:838`.
- Orchestrator-only: `roadmap.md:164` (M4b) names `Session.backend_id`; trim it once W lands.
- Not holders:
  - `backend_listing.py:27-56` and `read_params.py:295-306` (the `/backends/{backend_id}` route,
    batch 5's fix pass);
  - `webapp/lib/derivations/__tests__/connector-state.test.ts:6` (a served fixture).

**Digest:** `search_point_scorer`, `session` and `wiring` are unreached; equal (probe above).

**Served deltas, named:** none. Production already files and replays on every walk. Every test
session built by `factories.py` now does the same, on its own tmp archive.

**Equals-main checks:**
- Full gate.
- `--digests` byte-equal.
- `--against`: decisions and requests unmoved. The offline run builds its sessions through
  `wiring.py`, which already set the id.
- `git grep -n "backend_id" -- promptpotter/application/initialization/session.py promptpotter/application/scoring`
  is empty.

**Estimate:** +4 / −17; the ledger −4.

**Risks:**
- A test that scores one point twice in one store now replays its first answers. In the runs above,
  that moved no assertion but `:1048`.
- Factory sessions write archive files. Each test's tree is deleted at teardown
  (`tmp_path_retention_policy = "failed"`).
- Salvage overlap: m4-2 (`session.py`, the tests); ea-8, m2-2, m3 and m4-2 (the test files); en-9 and
  m17 (`test_spend.py`).

### Y — `evidence/read.py::_cycle_facts` takes the campaign `_at` located

**Row:** the Batch E § A bullet "`evidence/read.py::_cycle_facts` loads again the manifest `_at`
already holds" (1230-1233). Deleted.

**Owns (1 hand-edited):** `promptpotter/application/evidence/read.py`, and ledger hunk 1230-1233.

**Greps:**
- `_cycle_facts\|a subject resolves through its campaign's directory\|at.campaign\|class _Located`
  in `promptpotter tests`.

**Holders:**
- `read.py:571-575` (`_read_subject`), the one caller: it passes `at.campaign` beside `at.stores`.
- `read.py:803`: the signature becomes `_cycle_facts(stores, campaign: Campaign, hop, cycle_dir)`,
  wrapped to the line limit. `Campaign` is already imported (`_Located.campaign`, `:524`).
- `read.py:813-814`: the `load_campaign` and its `assert` are deleted. `campaign` feeds
  `cycle_config(stores, campaign, hop)` at `:815` as before.
- The head's cycle is in the same campaign (`hop.campaign_id == spec.campaign_id`), so
  `at.campaign` is the manifest the second read loaded.
- Not holders: `:401`, `:609`, `:621`, which already read `at.campaign`.

**Digest:** `evidence.read` is unreached; equal.

**Served deltas, named:** none on a landed tree. A manifest deleted between `_at` and this read now
reads as the manifest `_at` held, where it was a bare `AssertionError` (a 500).

**Equals-main checks:**
- Full gate.
- `--digests` equal.
- `git grep -n "a subject resolves through its campaign's directory" -- promptpotter` is empty.

**Estimate:** +3 / −3; the ledger −4.

**Risks:** none known. Salvage overlap: ea-8, m4-2 (`evidence/read.py`).

---

## Chain 2

### Z — `set_concurrent_cycles` refuses by the one rule, and the false access-model claim

**Row:** the Batch E § G bullet "`quota.py::set_concurrent_cycles` gates `is_host` only" (1306-1309).
Deleted. It also fixes a dead doc claim the row's rule makes false.

**Owns (2 hand-edited):** `promptpotter/application/jobs/quota.py`,
`docs/operations/access-model.md`, and ledger hunk 1306-1309.

**Greps:**
- `set_concurrent_cycles\|concurrent_cycles_writable\|max_concurrent_cycles_writable` in
  `promptpotter tests webapp/lib webapp/components docs scripts`.
- `concurrency_set_by_host\|set-concurrent-cycles\|SET_CONCURRENT_CYCLES` in the full path set.
- `capability` in `commands/dispatcher.py`: `:268-270` requires the row's capability before any
  applier runs.
- `CAMPAIGN_BUDGET_CAP` in `quota.py`: `:80` (import), `:970`.

**Holders:**
- `quota.py:975`: `if not is_host(stores.identity):` becomes
  `if not concurrent_cycles_writable(stores):`. The rule at `:969-970` is
  `is_host(...) and has_capability(..., CAMPAIGN_BUDGET_CAP)`. The refusal sentence and code
  (`concurrency_set_by_host`) stay.
- `access-model.md:59`: "(host, or `set-concurrent-cycles` on an account's own key)" is false. The
  verb writes the caller's own `user.json` and only the host account holding the budget capability
  may send it, whoever's key pays. It becomes "(hand-set in `user.json` by whoever runs the box;
  `set-concurrent-cycles` for the host account with the budget capability)".
- Readers, unedited:
  - `quota.py:454-457, 476` (`QuotaStatus.max_concurrent_cycles_writable`, the served half);
  - `AccountUsageTab.tsx:239` (S's note);
  - `command_kinds.py:227-233` (the row and its comment, which are accurate).

**Digest:** `jobs.quota` is unreached; docs are free; equal.

**Served deltas, named:** none reachable. The dispatcher refuses a caller without
`CAMPAIGN_BUDGET_CAP` before the setter runs, and the setter's one caller is the dispatcher
(`limits_and_queue.py:83`).

**Equals-main checks:**
- Full gate (`doc-claims` reads the cited paths).
- `--digests` equal.
- `git grep -n "if not is_host(stores.identity)" -- promptpotter/application/jobs/quota.py` is empty.

**Estimate:** ±0, one line edited in place in each file; the ledger −4.

**Risks:** none known. Salvage overlap: m14a-2 (`quota.py`); ea-8, m2-2 (`access-model.md`).

### AA — `RunState.launch_refusal` becomes `start_refusal`, and the declined pause reads a sentence

**Rows:**
- "**`launch_refusal` names two things**" (179-182). Deleted.
- "**A declined pause refuses with "."**" (1550-1552). Deleted.

**Owns (2 hand-edited, +1 regenerated under options 1 and 2):** `promptpotter/domain/phases.py`,
`promptpotter/application/commands/launching.py`, `webapp/lib/api/types.generated.ts` (options 1 and 2
only), and ledger hunks 179-182 and 1550-1552.

**Greps:**
- `launch_refusal\|launchRefusal` in the full path set. The served `DraftBuild.launch_refusal`
  (`draft_build.py:109`, `IngestConversation.tsx:223`, `cadence.ts:17`) and
  `quota.py::launch_refusal` stay.
- `-w "start_refusal\|resume_refusal\|run_refusal"`: no collision.
- `pause_refusal\|gate_refusal\|arms_refusal`, the family the new name joins.
- `parked\|no_action\|RUN_PHASE_INFO` in `webapp promptpotter scripts tests docs`.
- `RunState(` in `promptpotter tests`: only `runtime_flags.py:105-130` builds one.
- `stopping at its next checkpoint\|producer gone\|waiting for a machine slot`: `RUN_PHASE_INFO` is
  mirrored in `types.generated.ts:5349-5358` only, not in the openapi.

**Holders:**
- `phases.py:277`: the `RunState.launch_refusal` property becomes `start_refusal`, after the
  `start-run` verb, beside `pause_refusal`, `gate_refusal` and `arms_refusal`.
- `phases.py:435` and `launching.py:141`: the two readers.
- The declined pause:
  - `phases.py:362, 364`: `RUNNING` and `SCORING` park `""`.
  - `phases.py:440-442` (`RunAdmission.of`): this branch capitalizes `""` into ".".
- **Reachability, measured by reading `runtime_flags.py::read_run_state` (`:98-130`):**
  - `RUNNING` is derived only for an attached producer (`:130`).
  - `SCORING` is derived only for a held one (`:122-123`).
  - `CLAIMED` derives `QUEUED` or `STARTING` (`:104-105`), whose action is `None`.
  - So `pause_refusal` is never non-empty in `RUNNING` or `SCORING` but for an inner run, which has
    its own branch (`:438-439`).
  - The "." is reachable only through a hand-built `RunState`. No test builds one.

**Operator decision: the declined-pause wording.** Two wording options, as asked, and a third shape:
- **Option 1, a state word like its siblings** ("stopping at its next checkpoint", "writing its last
  files"):
  - `RUNNING` parks `"starting its run"`, served as "Starting its run.";
  - `SCORING` parks `"starting its pass"`, served as "Starting its pass.".
  - It names the one case a pause is refused, a launch that has not taken its run.
  - It moves 2 lines of the RUN_PHASE_INFO mirror.
- **Option 2, say the refusal**:
  - `RUNNING` parks `"no run in flight to pause yet"`;
  - `SCORING` parks `"no pass in flight to pause yet"`.
  - It also moves 2 mirror lines.
- **Option 3, no new word (recommended):** a declined pause answers its own refusal sentence, so
  `:442` becomes
  `offers, refusal = None, (declined if info.action == "pause" else f"{info.parked.capitalize()}.")`.
  - The sentences are already precise: "This launch has not started its run yet — cancel it from the
    queue instead." and "No run of this cycle is in flight to pause."
  - `parked` stays the word of a phase whose start or resume waits on its producer.
  - Nothing is regenerated.
  - The row's re-test becomes "no `RunAdmission.refusal` is `"."`".

**Name decision:** `start_refusal`. **Recommended: yes**, since the verb is `start-run` and the
family is named by verb.

**Digest:** `RunState`, `RUN_PHASE_INFO` and `RunAdmission` are not reached units of
`domain.phases`. `commands.launching` is unreached. Equal under option 3 and with a parked word
(both probed).

**Served deltas, named:**
- Option 3: none reachable. In a hand-built state, `RunAdmission.refusal` reads the pause sentence,
  not ".".
- Options 1 and 2: the same, plus `RUN_PHASE_INFO.running.parked` / `.scoring.parked` in the TS
  mirror.

**Equals-main checks:**
- Full gate (`--web` under options 1 and 2).
- `--digests` equal.
- `git grep -n "launch_refusal" -- promptpotter/domain` is empty.

**Estimate:** ±0 (3 lines renamed in place); +2 / −1 under option 3, or 2 strings plus 2 regenerated
lines under options 1 and 2; the ledger −7.

**Risks:** none known. Salvage overlap: ea-8 (`phases.py`); ea-8, en-9, m2-2, m4-2 (`launching.py`).

### AB — a job's log lives in its cycle, so its campaign's delete takes it

**Rows:**
- "**A job's log outlives its campaign**" (250-255). Deleted.
- The § Seen removing the migrations sub-bullet "`jobs/logs/{job_id}.log` … outlives its job"
  (1667-1668), with its parent line. Deleted; it is the same fact.

**Owns (5 hand-edited):**
- `promptpotter/application/jobs/launcher/run_job.py`;
- `promptpotter/infrastructure/store/{layout.py, data_root.py}`;
- `promptpotter/config/paths.py`;
- `docs/operations/persistence-and-state.md`;
- ledger hunks 250-255 and 1667-1668.

**Greps:**
- `JOB_LOGS_DIR\|jobs/logs\|logs/{job\|job log\|job's log` in the full path set: no test, no doc
  outside the ledger and `roadmap.md:71, 167`.
- `log_path\|"logs"` in `promptpotter`.
- `JobSpec\|hop: CycleHop` in `run_job.py`: `JobSpec.hop` is required (`:64`), so every spawned job
  has a cycle.
- `def delete_campaign\|rmtree_robust(\|def _strip_to_keepsake\|def delete_cycle`, for the delete
  paths.
- `LaunchClaimRecord`, for option B below.
- `classify(\|def refusal` in `maintenance/landing.py`: a file no row names refuses the landing
  (`:52-53`).
- `_shown` in `cycle_files.py`: a dot directory is not served (`:55-57`).
- `LEAF_INFO\|Loop telemetry`: the `Trace` leaf word is mirrored in the generated pair.

**Holders (option A, recommended):**
- `layout.py:254`, beside `pass_lock`: `CycleLayout.job_log -> self.runtime / "job.log"`. Every
  launch of the cycle appends to it; the producer lock allows one at a time.
- `run_job.py:91`: `log_path = CycleLayout(stores.campaigns.cycle_dir(spec.hop)).job_log`.
  `CycleLayout` is already imported (`:33`). `_start` keeps its `mkdir(parents=True)` and
  `open_append` (R's 0600 opener).
- `run_job.py:26`: the `JOB_LOGS_DIR` import goes.
- `paths.py:78`: `JOB_LOGS_DIR` is deleted.
- `data_root.py:22`: its import goes. `:251` (`Kind("job log", _INSTALL, "jobs/logs/*.log",
  Fate.RUN)`) is deleted.
- `data_root.py:373`, in `_cycle_rows`: `CampaignRow(f"{where} job log", at(layout.job_log),
  Leaf.TRACE)`. It is not a keepsake, so `delete --keep-results` strips it.
- `persistence-and-state.md:84`: `.runtime/` gains `job.log   # a detached launch's stderr, one
  per cycle`.
- By construction, every delete now takes the log, with no new code:
  - `CampaignStore.delete_campaign` (`store.py:464`, `rmtree_robust`);
  - its keepsake strip (`:136-144`);
  - `delete-cycle` (`store.py:743`);
  - the inner-sandbox delete.
- Unedited:
  - `run_job.py:102` and `:127` name `log_path` in a message and a log line;
  - `LEAF_INFO[TRACE]` ("Loop telemetry: locks, the Langfuse id map.") names examples, and a stderr
    log is loop telemetry, so it is not stale.

**Option B (not recommended):** keep `jobs/logs/` and unlink each log on delete.
- `delete_campaign` folds the `LaunchClaimRecord.job_id`s (`store.py:660`) over the
  `campaign_cycle_ledgers` that `_campaign_metered_bank` already reads.
- `cycle_cleanup.py::delete_cycle` does the same for one cycle.
- The jobs directory reaches the store as a parameter beside `inner_sandbox_root`.
- That is a second off-tree delete, and a keepsake strip that must remember the logs too.

**Digest:** `run_job` and `config.paths` are unreached; `infrastructure` is plumbing; equal (probe
above).

**Served deltas, named:**
- A cycle's storage report counts its log under `Trace`. Install bytes no longer hold it.
- The file tree does not list it: `.runtime/` is hidden by `_shown`.
- A campaign, keepsake or cycle delete removes it.
- The adoption error's text (`:102`) names the new path.

**Equals-main checks:**
- Full gate.
- `--digests` equal.
- `--against`: unmoved; the offline run spawns no job.
- `land-data --dry-run` on a desk with no `jobs/logs/`: proven, with no unknown file.
- `git grep -n "JOB_LOGS_DIR" -- promptpotter` is empty.

**Estimate:** +6 / −3; the ledger −8.

**Risks:**
- **The box.** Its `jobs/logs/*.log` becomes files no row names, so `update.sh`'s `land-data` refuses
  until they go. If AB ships in the release that already carries w2b's run-data `--reset`, the reset
  drops them. Otherwise the deploy step is `ls <DATA_DIR>/.promptpotter/jobs/logs | wc -l`, then
  `rm -r` that directory. That is a box change, the operator's.
- Drafts get the row too (`_cycle_rows("draft", …)`). No draft spawns a job, so it is a row with no
  writer, like a draft's pass lock.
- Salvage overlap: ea-8 (`layout.py`, `data_root.py`, `persistence-and-state.md`); m17, m2-2
  (`data_root.py`).

**Operator decisions:**
- **Where the log lives.** **Recommended: A**, the cycle's `.runtime/job.log`. Its lifetime is the
  tree's, every delete takes it, and nothing new is built. B keeps the path and adds an off-tree delete
  to two verbs.
- **The box's old logs.** **Recommended:** ship AB inside the reset release. Otherwise, a counted
  `rm -r jobs/logs` at its deploy.
- **AB pre-empts an item of U2 (`roadmap.md:71`, "the jobs logs") and M6 (`:167`, "the job
  logs").** **Recommended: yes.** It is one path; U2 and M6 keep the rest. The orchestrator trims both
  rows.

### AE — the shutdown notice names only the runs this process stops

**Row:** "**The server's shutdown notice counts runs it does not host**" (1597-1602). Deleted.

**Owns (1 hand-edited):** `promptpotter/main.py`, and ledger hunk 1597-1602.

**Greps:**
- `_telegram_shutdown_notice\|def list_running\|list_running(` in `promptpotter tests`.
- `KillMode\|_DETACHED =` in `deploy-linux promptpotter`.
- `def this_producer\|producer_id\|def adopt` in `application/jobs`.
- `-i "shutdown notice\|stopped with\|nothing running"` in the full path set:
  `deploy-linux/README.md:299` ("a stop with nothing running"), `CHANGELOG.md:370` (history).

**Holders:**
- `main.py:80-91`: `running = registry.list_running()` names every slot holder on the machine.
- **The row understates it.** The box unit runs `KillMode=process` (`install-service.sh:118`,
  `deploy-linux/README.md:194`), and a spawned run starts detached (`run_job.py:54-56`) and adopts
  its job (`registry.py:246-254`, `producer_id = this_producer(...)` in the child). So the server's
  own spawned runs continue through the stop too, not only CLI runs, and "Resume from the campaign
  once it is back up" is false for every run named today.
- What the stop does end are the jobs whose producer is this process.
- The edit:
  - `own = this_producer(registry.jobs_dir)` (`jobs/interlock.py:68`);
  - `running = [j for j in registry.list_running() if j.producer_id == own]`;
  - the import goes beside `main.py:31`.
  - `this_producer` mints this process's producer file if none was taken, as every registry read
    through `_orphaned` already may. `atexit` retires it.

**Operator decision: the wording.**
- **Recommended:** the notice names only its own runs, wording kept:
  - "⚠ … stopped with N run(s) in flight: … Resume from the campaign once it is back up.";
  - else "… stopped (nothing of its own running).".
- The alternative adds "M run(s) continue in their own process." That is noise on every deploy, and
  the row asks for owned runs only.
- **AE pre-empts U2's and M6's "the shutdown notice" (`roadmap.md:71, 167`).** **Recommended: yes.**

**Digest:** `main` is unreached; equal.

**Served deltas, named:** the Telegram shutdown notice. Its count and names drop every detached and
CLI run.

**Equals-main checks:**
- Full gate.
- `--digests` equal.
- `git grep -n "running = registry.list_running()" -- promptpotter/main.py` is empty.

**Estimate:** +3 / −1; the ledger −6.

**Risks:**
- No test holds the notice, and the charter admits none (it is visible in use).
- On the desk (Windows, `CREATE_NEW_PROCESS_GROUP`), spawned runs also outlive the server, so the
  same filter is right there.
- Salvage overlap: ea-8, m14a-2, m17 (`main.py`).

---

## Chain 3

### AC — `parentIdx` becomes `standingIdx`, and `.ov-forest` becomes `.ov-interval-rows`

**Rows:** two bullets of § The candidates surface (review 2026-11-01):
- "`FitnessChart::parentIdx` names the standing selection, not a parent." (931). Deleted.
- "`.ov-forest` is styled in `compare-evidence.css` for `OuterSignalPanel`, and collides with
  `candidates/Forest`." (937). Deleted.

**Owns (4 hand-edited):**
- `webapp/components/candidates/FitnessChart.tsx`;
- `webapp/app/styles/domains/compare-evidence.css`;
- `webapp/components/compare/EvidenceCharts.tsx`;
- `webapp/components/dashboard/scoring/OuterSignalPanel.tsx`;
- ledger lines 931 and 937.

**Greps:**
- `parentIdx\|barCaps\|opts.parent` and `ov-forest` repo-wide: no other holder, test or e2e.
- `-w "standingIdx\|standingIndex"` and `interval-rows\|lift-rows\|forest-plot` in webapp: no
  collision.
- `forest` in `webapp/app/styles`, for the names already taken: `.family-cladogram-forest`,
  `.forest-card`, `.cand-forest-toggle`.

**Holders:**
- `FitnessChart.tsx`, renamed in place:
  - `:392` (`parentIdx` = `views.findIndex(... v.node.facts.stands)`) becomes `standingIdx`, as do
    `:398`, `:402`, `:530` (×2) and `:560`.
  - The bar-caps plugin option `parent` becomes `standing` at `:41` (the chart.js augmentation),
    `:53` (the plugin type), `:83` (`opts.parent`) and `:552`.
- `.ov-forest` becomes `.ov-interval-rows` at `compare-evidence.css:66`, `EvidenceCharts.tsx:176` and
  `OuterSignalPanel.tsx:123`. Both hosts draw a column of interval rows (`LiftRow`, `MergedRow`).
- Not holders: `dendrogram.ts`'s and the served `parent` fields, which are real parents.

**Digest:** webapp only; equal.

**Served deltas, named:** none; the render is unchanged.

**Equals-main checks:**
- `gate.py --web`.
- `git grep -n "parentIdx\|ov-forest" -- webapp` is empty.

**Estimate:** ±0 (14 lines renamed in place); the ledger −2.

**Risks:** none known. No salvage overlap.

**Operator decision:** the two names. **Recommended:** `standingIdx` (the code reads
`facts.stands`) and `.ov-interval-rows`. "Forest" stays the lineage forest's word.

### AD — the pipeline renderer's styles in one file

**Row:** "**The pipeline renderer's styles live in `app/styles/domains/chat.css`**" (1115-1118).
Deleted.

**Owns (4 hand-edited, one deleted):** `webapp/app/styles/domains/{chat.css, dashboard-pipeline.css,
dashboard-pipeline-nodes.css}` (the last deleted), `webapp/app/styles/index.css`, and ledger hunk
1115-1118.

**Greps:**
- `\.connector\|wf-\|pipeline-` per file in `webapp/app/styles/domains`:
  - `chat.css` 125, all at `:3-204`;
  - `dashboard-pipeline-nodes.css` 6;
  - every file between barrel slots 15 and 28 holds 0 (node-surface, files, searchpoint,
    candidates-forest, hard-samples, measurements, remote, verify, login, onboarding,
    node-surface-frame).
- `wf-hero\|pipeline-zoom` and `pipeline-stack` in `webapp/components`:
  - `PipelineFlow.tsx` 17 and `PipelineStack.tsx` 2, the renderer;
  - `ChatPane.tsx` 1, chat placing it.

**Holders:**
- `chat.css:3-203`: the renderer's block (`.pipeline-zoom*`, `.pipeline-stack`, `.wf-hero*`,
  `.connector*`, their two `@media` blocks) moves.
- What stays in `chat.css`: `:1-2` (`.chat-content`) and `:204` (`.chat-content > .wf-hero`, chat's
  placement of the hero).
- `dashboard-pipeline.css` (27 lines, barrel slot 15) already holds the renderer's card and the
  `@keyframes wf-running-pulse` that `chat.css:45` animates with. It takes the block after its own
  lines.
- `dashboard-pipeline-nodes.css` (21 lines, slot 28): `.pipeline-stack-note` and the restated
  `.wf-hero-node.llm` rules go to the end of `dashboard-pipeline.css`. They stay after every block
  rule, which is the order their comment (`:1`) asks for. The file goes.
- `index.css:28`: its import goes. `:15` and `:18` stay.
- `foundation/responsive.css`: its 14 renderer lines stay (the last slot).
- **Cascade, measured by the greps above:** no file between slots 15 and 28 names a renderer
  selector, so moving the block from slot 18 to 15, and the restatement from 28 to 15 (still after
  the block), reorders no same-selector rule.
- The row's re-test then reads 1 (`:204`'s placement), not 0. The row is deleted, so no re-test
  stays.
- Not holders: `chat.css:277` `.setup-step` (an ingest style; § Open — from the structure pass keeps
  its own clause).

**Digest:** webapp only; equal.

**Served deltas, named:** none, if the cascade holds. A rule from a generic selector in slots 16–27 is
the only way the moved rules could lose; none names these elements by grep.

**Equals-main checks:**
- `gate.py --web` (lint and vitest) and the cold Playwright tier.
- Before/after phone screenshots at 375 px of the dashboard pipeline (an LLM node idle, hovered,
  selected, running, scoring), the nested stack and the chat hero, with their full paths.
- `git ls-files webapp/app/styles/domains/dashboard-pipeline-nodes.css` is empty.

**Estimate:** about +220 / −223, all moved (201 block lines, 19 folded); the ledger −4.

**Risks:**
- Render drift through a generic selector the grep cannot see. The screenshots are the proof.
- No salvage overlap.

---

## Rows left out, and why

- **Now picked, their census-2 blockers gone:**
  - `Session.backend_id` (W): one assertion compared filing ids; no door is needed.
  - The declined pause (AA): T landed; the wording is drafted.
  - `jobs/logs` (AB): the operator decided, 2026-11-07.
- **Unchanged since census 2:**
  - `embedded_run.__all__` wrappers and the two typed REST routes: M6's one dispatcher.
  - `useLeafDatasetName`, `ids.ts::rootCycleId`, `DefaultClaim.renamed`, `blocklist_audit` /
    `grants_audit`, `slug_exists`, the candidates-surface formatters: census 1's blockers.
  - `ActiveSessionResponse`: a served reshape.
  - `TokenUsageRecord.cached` vs `key_source is None`: a ledger shape, a wipe.
  - DSPy pricing split: moves metered spend.
  - `index_terms` two calls: moves request order.
  - Three TermNorm defaults / `DEFAULT_CONNECTOR`: M18.
  - Unregistered `backend_type` at fifteen sites: more than 10 files.
  - `Session.langfuse`: shares `tracing/bridge.py` with `OBS_ENABLED`.
  - `FAMILIES` null pane: a design fork.
  - `filesRead` URL: `/files` descend.
  - `.prompt-snippet-input`: a render change.
  - The CLI's `ERROR:` prefix: Q3.
  - Wave B forwarders: not dead.
- **→ re-key arc** (each moves a treatment digest):
  - The fork-summary kind (`runner/output.py`): `_fork_summary_from_index` is a reached unit. It
    also needs a mint-record kind.
  - `closed_rounds.py`'s swallowed checkout: `graceful` sits in `_with_rows`, a reached unit.
  - `CapoInit.refuse_origin`: `capo.members` is hashed whole.
  - Excluded by the brief: `OBS_ENABLED`; the stale-data outcome enum; `PipelineSchema`
    name/version; the unknown-slot refusal; the two word tables; `CatchUpPool`; the `FileKind`
    remedies; the errors' `.provider`.
- **A served-answer or decision change, not a cleanup:**
  - `evidence/read.py::campaigns_on_dataset` by hash: it changes what an evidence read pools.
  - `domain/scoring.py::turn_scalars`: a per-step term namespace renames criterion terms.
  - A per-cell criterion naming a term no cell carries: a new check-in refusal.
  - `config/settings.py::_git_head`: a new landing refusal.
- **A new mechanism first:**
  - The torn `grant.json` survey (`install_spend.py`): the survey-serves-a-bad-row rule, M17.
  - `quota.py::sends_on` raising `NoApiKeyError`: several sites; `NoApiKeyError` is reached.
  - `ReadResult` literals outside `useRead`: a constructor beside `useRead`, Q2/U2/RS.
  - `LaunchState.ledger` Optional: the run-init order, U2's "five modules in the wrong order".
- **Rides a medium arc's rows:**
  - `tests/test_archive.py`'s zero-cell origin-gate test and the rest of § The origin gate: U2's
    "Round 0 has two doors" absorbs them.
  - `RunStatus.of`'s `mark: str`: the exported union re-keys, with its webapp readers.
  - `CheckinLoadingWindow`'s own `setInterval`: Q2, the cadences.
- **A survivor field:** `wallet_store.py::CouponGrant.granted_at`, written and never read. It is
  `grant.json`'s, so its delete is a landing migration (§ 0011), and the On-the-box bullet holds a
  re-grant question, not an action.
- **Low weight, no arc yet:**
  - `lib/theme.ts` mixes chart setup with the theme: 11 importers to re-point.
  - `STORAGE_KEYS`' unprefixed `side-panel:*`: a rename drops every stored panel state once.
  - `.setup-step` in `chat.css`.
  - `CandidateRow`'s re-derived "retired" and `useActivePointer`'s `|| null`: vague, unverified.
- **Excluded by the brief:**
  - A, B, D1, D2, E–T;
  - every `scripts/gate.py`, `last_green.py`, `processes.py` and `build_*.py` row (the WinError retry
    row, the worktree push, `Check.after`, the `--staged` checks, `optimizer-schemas`, the TS
    docstring generator);
  - `BackendHealthResponse.backend_id` (batch 5's fix pass);
  - the M13/M14 names (w2u).

## Shared-file and order notes

- **The ledger** is the only file two arcs touch, in the disjoint hunks above.
- **No arc shares a file with another**, within a chain or across. The chains order deletions (V, W,
  Y, Z) before consolidations (AA, AB, AE, AC, AD). Any link may join any batch once the arc before
  it in its chain is proven.
- **The generated pair** is touched only by AA under option 1 or 2 (`types.generated.ts`, two lines).
  Under option 3 no arc regenerates it.
- **Orchestrator-only edits.** No arc owns these; each is a docs-only commit:
  - `roadmap.md:164` (M4b): drop `Session.backend_id` once W lands.
  - `roadmap.md:71` (U2): drop "the shutdown notice", "the jobs logs" and, in its Owns, "the
    `main.py` notice" once AB and AE land.
  - `roadmap.md:167` (M6): drop "the shutdown notice, the job logs" likewise.
  - U2's "the jobs logs" and M6's "the job logs" are two spellings of one item; both go.
  - w2u edits the same table's milestone names, so rebase these trims on w2u.
- **Smells seen in passing.** One line each, for the orchestrator to file:
  - `RunPhaseInfo.parked` is read for 2 of 8 phases (`PAUSED` in `run-phase.ts:43` and
    `phases.py:442`; `TERMINAL` at `:442`). `QUEUED`'s, `STARTING`'s, `GATE`'s and `DETACHED`'s words
    are served in the TS mirror and read by nothing: `DETACHED` is never attached, so its start is
    never declined.
  - `persistence-and-state.md`'s layout omits `jobs/ended/`, which `data_root.py:250` rows (AB adds
    the job log only).
  - `interlock.py::this_producer` mints a producer file on first ask, so a read-only question (AE's,
    every `_orphaned`) can write. `atexit` retires it.
- **The box:** AB's old `jobs/logs/` must be gone before its `land-data`. That holds by itself if AB
  rides w2b's reset release; otherwise it is a counted `rm -r`, the operator's.
- **Salvage overlaps**, for the brief writer to check each tag before the arc (tags by path match in
  `scratchpad/sv_*.txt`):
  - m4-2: V, W, Y, AA;
  - ea-8: W, Y, Z, AA, AB, AE;
  - m2-2: W, Z, AA, AB;
  - m3: W;
  - en-9: W, AA;
  - m17: W, AB, AE;
  - m14a-2: Z, AE.
- **Batch suggestion:**
  - batch 6: V, Z, AC (the deletions, one per chain);
  - batch 7: W, AA, AD;
  - batch 8: Y, AB, AE.
  - Since no files overlap, two links of one chain may also share a batch.
