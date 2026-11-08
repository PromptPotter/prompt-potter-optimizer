# Code-Debt Cleanup — Backlog

**Only work that is blocked or multi-arc** — including every shape a pass noticed off its task (root
`CLAUDE.md` `<situational-awareness>`). Anything one edit closes is fixed in the pass that found it; never the root of a patch just shipped (root `CLAUDE.md` `<root-fix>`). An item ships by
being deleted. Each entry: `file::symbol` — the defect — the action — **Rides with:** the chain that
reaches it ([`roadmap.md`](roadmap.md) § The cleanup arcs (plan review, 2026-11-07)), or **Held:** why
none can yet (a digest, a decision, a mechanism, a served change, a survivor) — **Re-test:** the
command or question that says it still stands. Run the re-test before acting.

## URGENT — shapes that hide the backbone from a reshape

**Rides with:** any pass in the file named.

- **Left by the upload check-in's 422** (`25be69eac` cut rows on every draft read; fixed 2026-11-02 by
  gating `startability.py::draft_samples` on `origin_readiness.py::columns_confirmed`):
  - **"Run the resolver now" lives three times**: webapp `useIngestFlow` (`advance`, `land`,
    `submitContext`), CLI `new.py::_hold_checkin`, `DraftFile.resolution_pending`. Action: the served flag
    alone. **Rides with:** the host assistant ([`host-assistant.md`](host-assistant.md)).
  **Re-test:** `git grep -n "void runCheckin" -- webapp/lib/hooks/useIngestFlow.ts` is one line, under `resolution_pending`.
  - **A Start's check-in stops at `queued` / `starting`**: the mint closes the draft, so its read
    404s, and no served answer names the campaign a draft's Start minted, so no poll can follow it
    there. Action: one read of a Start off its job (`CampaignLaunch.draft_id`, its stage, then
    `Job.hop`). **Re-test:** Start a queued check-in; the view lands on the minted campaign unreloaded.
- **A backend wire skew is discovered as a 100% degraded origin, not refused at launch** — the box's
  TermNorm read `answer_field` while PromptPotter sent `answer_fields` (2026-10-31); the origin paid
  for every cell to learn it. Action: the connector's health/probe serves the wire contract it
  speaks, and `new` refuses a node config that contract rejects before any cell. **Rides with:**
  the connector probe. **Re-test:** a launch against a backend one wire rename behind fails at
  the probe, naming the key.
- **TermNorm words a warning's step into its message** (`research_pipeline.py` `f"{step_name} failed: {exc}"`
  over a step that raises `"llm_only: …"`), though `StepWarning` carries the step as a field: a served
  health sentence still reads `llm_only failed: llm_only: …`. Action: the message is the fault alone.
  **Rides with:** the next TermNorm change. **Re-test:** `git grep -n "failed: {" -- backend-api/api`
  in the TermNorm checkout.
- **A skipped automatic verify is a log line**: `diagnostics/verify.py::verify_on_saturation` catches
  `DiagnosticError` / `BankedSearchPointError` (no round scored the winner, its banked point does not
  rebuild, no closed round 0, a pause before any cell, no scoreable fresh cell) into `logger.info`, and
  `verify_candidate` re-opens the session from disk mid-loop. No verify record holds a skip: most raise
  before `VerifyEnterView`, and propagating would stop a run over an optional pass. Action: the skip is
  a refusal banked on the one pass that § One record shape for every scoring pass builds, served in its
  can-run status. **Rides with:** that arc's first builder. **Re-test:**
  `git grep -n "verify skipped" -- promptpotter/application/diagnostics/verify.py`.

- **PoBB re-decides a round a pause cut, and decides it differently** — capo-pobb paused at a fresh
  cell past round 2's banked walk and resumed: the resume banks two eliminator decisions beside the
  first launch's, and one `p_best` reads 0.096135 where the straight run read 0.100021, on the same
  paired breakdown (the same on `batch/2~3`, before the walk bank). A resumed comparison votes on a
  number no straight run computed. Action: an interrupted round's eliminator decisions retract on
  re-entry, and its reading is the one the round opened on. **Rides with:** the eliminator.
  **Re-test:** drop `capo-pobb` from `scripts/offline_run.py::BOUNDARY_RESUMED`; its `resumed/` leg
  reads UNMOVED.

## NEXT ARC

**Rides with:** the next arc's first builder wave.

### The n8n launch over MCP (stream rehearsal, 2026-11-06)

A BANKING77 campaign on an n8n harness, minted from `promptpotter mcp` on a desk last landed before
`fca59552b`. Each refusal below stopped it; none was patched in code.

- **A connector that reports no price cannot run under the default dollar cap** —
  `domain/campaign_config.py::_DEFAULT_BUDGET` gives every campaign a dollar ceiling, and an n8n cell is
  unpriced (`connectors/n8n.py::_pipeline_declaration` serves no `spend_bound`; n8n reports no usage),
  so `spend_book.py::SpendBook.refuse_unpriced` halts the first send `NO_RATE` — after the mint and run
  init, leaving a halted campaign. Any connector that serves no spend bound shares it. Action: a
  connector declares how its sends are bounded and reported, connector-agnostic, and a campaign's budget
  is checked against that declaration before admission: an unenforceable dollar axis is refused at the
  mint, never halted at round 0. **Rides with:** CONN (the declaration), M6 (refused before admission).
  **Re-test:** mint any `backend_type: n8n` dataset that does not declare `budget.usd: null`; it halts
  `No rate under the spend cap` at round 0.
- **A connector's endpoint is one per install** — `config/settings.py` `TERMNORM_URL`,
  `N8N_HARNESS_URL`: no dataset can name which endpoint (which TermNorm, which n8n workflow) it
  measures. Running a second workflow edits `.env`, re-keys every banked cell of the first
  (`n8n.py::INSTRUMENT_KEY` hashes the URL), and leaves the first dataset measuring the wrong workflow
  until `.env` is edited back. Action: operator config holds named endpoints per connector (address and
  credential), and a dataset names one; the credential never leaves operator config
  ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § The credential rides the
  connector). **Rides with:** M18. **Re-test:** two n8n datasets on two workflows both run, no `.env`
  edit between them.
- **Nothing lands the data root off the box** — `maintenance/landing.py` runs from `land-data` alone,
  which `deploy-linux/update.sh` calls. A checkout that pulls a reshape keeps running on files the
  build refuses: every launch here was refused `job_unreadable` (released jobs that
  `migrations/job_ending.py` maps) and then `ledger_unreadable` (`.workspace/events.jsonl` without
  `command.act_digest`). Action: the landing runs wherever the code meets a data root it has not
  landed, one mechanism with `update.sh`'s. **Rides with:** LAND. **Re-test:** check out a commit that
  moves a `Fate.RUN` shape; `new <dataset>` refuses nothing.
- **`--reset` couples two decisions** — `landing.py::Landing.refusal`: unloadable run data and files no
  row names are both "in the way — `--reset` drops both", so a run-data reshape cannot land without
  deleting every unknown file. This desk's unknown files are authored: `block_packs/_sources/` (harvest
  notes and `build_packs.py`) and `default/banks/`. Action: the landing drops unloadable `Fate.RUN`
  data by itself; unknown files refuse on their own; `_sources` and its builder move into the checkout.
  **Rides with:** LAND. **Re-test:** `land-data --dry-run` on a desk holding both.
- **An unreadable file's remedy sends the operator to hand-deletion** — `shared/errors.py` `JOB` ("No
  migration exists: delete it once no run it names is live"), `LedgerUnreadableError` ("No migration
  exists: no verb repairs it"), `MANIFEST` (names `reset`), though `job_ending` maps the job and the
  landing drops run data. Action: one remedy, the landing's. **Rides with:** LAND. **Re-test:**
  `git grep -n "No migration exists" -- promptpotter/shared/errors.py`.
- **A launch refused before admission reaches a host as a task** —
  `host_surface.py::HostSession._open` opens the task before the dispatcher accepts the act, so a
  refusal (an unreadable ledger) answers `working`, then reads `failed` on `tasks/get` 0.1 s later, while
  the CLI exits 1 on the same refusal. The handle it opens names no campaign
  (`host_surface.py::HostSession._status`: `mint-campaign: running`), so a host whose call accepts
  no task (Claude Code's `tools/call` declares no Tasks extension) holds an id no tool it has can
  read, and finds its run through `hierarchy`. Action: a task opens for an admitted act and names the
  address it acts on; a refusal before admission is the call's answer on every door. **Rides
  with:** M6 (the one launch answer). **Re-test:** `mint-campaign` over `promptpotter mcp` against
  an unreadable workspace ledger answers `isError`, never a task; on a readable one the handle
  names its campaign and cycle.
- **One fact, two words: `backend_type` and `connector`** — the definition key
  `pipeline.backend_type`, `dataset_access.py::backend_type_of_dataset`,
  `pipeline_resolve.py::draft_backend_type` and `DraftPipeline.backend_type` name what
  `Campaign.connector`, `Environment.connector` and `CampaignPipelineResponse.connector` name. Action:
  one word, `connector`, the definition key included; tenant definitions re-written by the landing.
  **Rides with:** CONN. **Re-test:**
  `git grep -nw backend_type -- promptpotter` is empty.
- **A pipeline's `name` and `version` are parsed and read by nothing** —
  `pipeline_parsing.py::parse_pipeline_response` fills `PipelineSchema.name` and `.version` off a
  declaration (`defaults.yaml` `version: v0.1`, `n8n.py::_pipeline_declaration`, `dspy_module.py::dataset_pipeline`),
  and no reader takes either but a debug log. Action: drop both fields and the keys that feed them.
  **Rides with:** CONN.
  **Re-test:** `git grep -nE "\"(name|version)\":" -- promptpotter/connectors` is empty.

### The stream (episode 07, 2026-11-06)

What the `banking77-n8n__7b4528` run's ledger shows behind the symptoms the operator named on air.

- **A pause by interrupt is served as a bare "Paused"** — the `run_phase` record holds
  `cause: "interrupt"` with no command, yet the readout prints `■ PAUSED · Paused` and no surface names
  the cause. On air, two minutes went to "what was the pause reason?". Action: a stop's cause is
  narrated and served on every door. **Rides with:** M10. **Re-test:** interrupt a running `new`; the
  readout and `hierarchy` name the interrupt.
- **The first verify after a pause starts 38 s late, and no verify is ever `accepted`** — the
  `verify-candidate` command came at 16:37:04Z, its scoring job started at 16:37:42Z, and it was
  `applied` at 16:41:44Z with no `accepted` ack in between. A later verify entered its phase in 1 s.
  For 38 s, no door shows the click landed. Action: the command acks at admission. Attribute the 38 s
  by phase before any fix. **Rides with:** M13. **Re-test:** on a paused cycle, time `command` → the
  `verify` phase's `enter` in the ledger.
- **A resume takes up to 72 s to be applied** — `start-run` on a paused cycle read `running` after 6 s
  and 14 s, and `applied` after 14 s and 72 s. Each resume prints a fresh `✓ Initialized` and the
  coupling warnings. Action: attribute the wait by phase, then cut it at its root. **Rides with:** M7.
  **Re-test:** time `start-run` → its `applied` ack on a paused cycle.

### Wave 1's census (2026-11-06)

- **A fork summary's kind is parsed from its id** — `runner/output.py` reads fork versus diag off the
  cycle id's grammar, not the mint record that says which it is. Action: read the kind the mint
  recorded. **Rides with:** M11. **Re-test:** `git grep -nE "_fork_|_diag_" -- promptpotter/application/runner/output.py`.
- **Drilled into an inner run, the masthead names the outer dataset** — `RunMasthead.tsx` reads the
  dataset off the outer campaign, while chat and cells read the leaf's. Action: the one viewed-dataset
  derivation everywhere. **Rides with:** M16b. **Re-test:** open an L4 inner run; the masthead and
  chat name one dataset.
- **Lineage spines key on `facts.individual` and on the origin** — the compare pane's spine builder
  (moving into `lib/derivations/` with M16a) resolves a subject by individual id across the whole
  tree, sandboxes included
  (`hierarchy_subtree.py::_answering` picks one row per id), and merges a campaign's subjects into
  one spine, so a subject on a diverging branch drops out of the drawing. Action: resolve by address,
  one spine per line. **Rides with:** M16b. **Re-test:** compare two branches of one campaign; both
  draw.
- **A refusal still has three more shapes besides the one Refusal value**:
  - `jobs/quota.py::LaunchRefusal` is served with `reason` and `sentence`.
  - A job persists its refusal as `str(exc)` (`jobs/registry.py`, `domain/job.py`), and MCP `tasks/get`
    reads that back as a code plus text.
  - REST's envelope calls the code `error`, and the CLI's `--json` spreads `details` beside it.

  Action: every door and every persisted job carries the Refusal value. **Rides with:** REFUSE.
  **Re-test:** `git grep -n "class LaunchRefusal" -- promptpotter` is empty.
- **A taken name is read two ways** — `slug_exists` (the directory exists) against the definition's
  holders (`dataset.json` exists), across `stores.py`, `draft_patch.py`, `ingest.py` and
  `mint_and_start.py`. Action: one read of a tenant name's hold. **Rides with:** M6. **Re-test:**
  `git grep -n "slug_exists" -- promptpotter` names one definition and its callers.
- **The unknown-slot refusal is spelled twice** — `bench/task_context.py` and the potter injection
  registry each validate a template's slots. Action: one template-slot validation. **Rides with:** REFUSE.
  **Re-test:** one function words an unknown slot.
- **The scripts' writer and the store's are two**: `store/io.py::_atomic_replace` and
  `scripts/processes.py::replace_changed` each state one bounded retry on Windows' WinError 5; the
  stdlib-only interpreter the scripts run under is what varies, and any `promptpotter` import runs
  `require_core()` and `hold_private()` first, so the scripts' half cannot read the store's. Every
  script already writes through `replace_changed`. Action: one retry rule stated by one owner.
  **Rides with:** READS. **Re-test:** `git grep -n "def _atomic_replace\|def replace_changed" --
  promptpotter scripts`: 2 lines now, 1 at target.
- **A workspace and a hop are re-derived from a path by parent counting** —
  `jobs/reaper.py::_tenant_root_of` and `sweep_dead_cycles`, `campaign_store/store.py` (the cycle's
  hop beside `cycle_dir.parent.parent`) and `store/account_trail.py` (a campaign id from
  `parent.parent.name`). Action: read the address from `Subject` / `CycleHop`, never from the layout.
  **Rides with:** ADDR. **Re-test:** `git grep -nE "\.parents\[[0-9]\]|\.parent\.parent" -- promptpotter`
  is empty.
- **A refused L4 arm's identity is the floor's** — `potter/dispatch/layout.py::layout_levers` folds a
  breach into the floor and drops it from the resolved overrides, so `spawn.py::inner_cell_hop` and the
  envelope read the floor arm's campaign. Action: the identity reads the refusal. **Rides with:** M8.
  **Re-test:** a breaching and a floor arm get different inner campaign ids.
- **The optimizer prompt spells slots its own way** — it writes `[#N]` / `#N`
  (`potter/dispatch/injections/` `panels.py`, `catalogues.py`, `layer_state.py`), while every surface prints
  `domain/sample.py::sample_tag`'s `#007`. Changing it is a prompt-shaping edit. Action: the prompt
  renders its slots through `sample_tag`. **Rides with:** M8. **Re-test:**
  `git grep -nE "\[#\{|#\{" -- promptpotter/application/optimizers/potter` names no slot.
- **The landing does not run wherever code meets an unlanded root** — the boot landing is cut. Sites:
  `presentation/cli/campaign_runner.py` and `main.py` (no boot landing), the `_sources` registries
  (completed per verb, so a registry verb is refused on an unlanded root), the process interlock
  `jobs/interlock.py` (no guard against a live API running other code), and the readers that touch the data
  root without landing: `application/embedded_run.py` and `presentation/admin_bot.py`. Action: a per-campaign
  guard and an interlock against a live API running other code, then the landing at every entry that
  reads the root. **Rides with:** LAND. **Re-test:** boot a new build on an unlanded root; it lands or
  refuses typed, and no registry verb is refused.
- **A whole-file read is refused in four ways outside `read_file`** — `infrastructure/identity/session.py`
  reads raw and deletes a session file that does not validate, `store/layout.py::read_sandbox_owner`
  swallows an unusable owner into a log and answers `None`, `store/measurement_archive.py` reads archive
  files raw, and `projections/audit_trail.py` reads a round tolerant outside a survey. Action: each
  refuses once as a `FileKind` through `read_file`, or is named a cache read. **Rides with:** READS.
  **Re-test:** `git grep -nE "read_json_optional\(|read_json_tolerant\(" -- promptpotter` names only
  surveys and caches.

### Batch 10's fix pass (2026-11-07)

- **14 `ContextVar`s still stand beside `RunScope`**: `jobs/coupon.py` 1, `launcher/admission.py` 1,
  `llm/send_pacing.py` 4, `llm/spend_book.py` 3, `llm/telemetry.py` 3,
  `shared/measurement_context.py` 2. Action: each moves onto `RunScope` with its chain, or is named
  per-call state. **Rides with:** M12 (`spend_book`, coupon), M6 (admission), M10 (telemetry), M13
  (`measurement_context`), M8 (`send_pacing`). **Re-test:** `git grep -c "ContextVar(" -- promptpotter`
  sums to 15, `RunScope`'s own among them.
- **An L4 arm's model answers every inner node** — `runner/inner/spawn.py::_one_model` lays the first
  edit's model, on that edit's provider, on every llm node, as main did, so L4 cannot search a model
  per inner node. Decision: one model per arm, or an edit's model on its own node. **Held:** the
  operator's. **Re-test:** an arm naming a model on `l1_critique` alone answers it on every llm node.
- **The regenerator checks probe two ways** — `scripts/gate.py`'s `ts-types`, `vendor-marks` and
  `specs` compare a snapshot, `optimizer-schemas` reads the lines its regenerator printed. Action: one
  probe for every regenerator. **Rides with:** M1. **Re-test:** read the four checks' functions.
- **`scripts/build_ts_types.py` imports our package inside functions** — `_emit_lifecycle_filter`
  and the served-words emitter. Action: module-level imports, as its others. **Rides with:** M1.
  **Re-test:** `grep -nE "^ +from promptpotter" scripts/build_ts_types.py` is empty.
- **`tests/test_identity.py` reaches the spawn's private `_laid_arm`** (`_l4_arm`, `_l4_laid`, the
  depth step): the journey cannot lay an arm through `_open_seat`, since no inner campaign stands in
  it, and no depth-2 cell exists (the recursion's nodes carry no `prompt_info`, so
  `inner_instrument_config` refuses first, on main too). Action: the journey drives `_open_seat` over
  an offline inner campaign. **Rides with:** M8. **Re-test:** `grep -n "_laid_arm" tests/test_identity.py`
  is empty.
- **A fork seeded off a candidate is a babysit steer** — `SteerForkPanel.tsx` sends the candidate's
  whole resolved config as `seed.pipeline_overlay`. It carries `provider` and the pinned
  `route_order`, which `domain/pipeline_overlay.py::overlay_sets_model_outside_allowed` always
  counts, so every panel fork runs babysat and its mint stamps `human_intervened`, and a principal
  without `campaign.babysit` is refused 404 (main too). The payload already names the candidate
  (`round`, `candidate_id`). Action: fork init seeds from the named candidate's record, the panel
  sends only the operator's edit, and the verdict reads that edit. **Rides with:** M4. **Re-test:** a
  panel fork with no edit reads `steer_verdict` "clean".

### The connector and entry-point seam audits (two unbiased reads, 2026-11-06)

- **Resuming a campaign takes its connector from today's dataset file** —
  `jobs/launcher/mint_and_start.py` (`ExistingCycle.backend_type`) and `wiring.py::bind_cycle_session`
  read the definition again, while the spend sends read `Campaign.connector` ("FROZEN at mint"). A
  slug re-pointed to another connector runs or fails on an origin hash naming no connector: a state
  the system enters but cannot report. Action: `init_services` takes the Connector from its caller
  (the campaign's for a bound cycle); a mismatch with the definition is refused, naming both.
  **Rides with:** CONN. **Re-test:** re-point a minted slug's `backend_type`; `start-run` refuses.
- **The check-in's pipeline step is TermNorm's for every connector** —
  `webapp/components/ingest/PipelineSetupSection.tsx` hard-codes `llm_only` and "Research + Match";
  on an n8n definition, picking "LLM only" excludes every node. Action: serve the declaration's named
  `pipelines` map and render the select from it; delete `LLM_ONLY`. **Rides with:** M5. **Re-test:**
  `git grep -n "LLM_ONLY" -- webapp/components` is empty.
- **Two owners, two values, for what a definition leaves unsaid** — `assets/datasets/defaults.yaml`
  (install and research tiers) against `connectors/termnorm.py` `available_models` /
  `default_node_config` and `draft_campaign.py::UPLOAD_BUDGET_ROUNDS` /
  `Connector.default_optimization` (uploads); an upload opens the model axis the install policy
  closes. `defaults.yaml` names no connector, model list or budget; every definition names its own.
  Action: a connector's node template is laid under every definition naming it, in any tier;
  connector-neutral campaign defaults are one file every tier lays under; the four upload-seeding
  `Connector` fields become one optional upload-defaults declaration whose presence is
  `serves_uploads`. **Rides with:** CONN. **Re-test:** `git grep -nE "llm_only|param_keys" --
  promptpotter/assets/datasets/defaults.yaml` is empty and `connectors/termnorm.py` holds the one
  node template.
- **A campaign's connector has two sources** — `wiring.py` picks the session's connector from the
  definition's `backend_type`, while `mask/load.py`, `hierarchy.py`, `admission.py` and
  `embedded_run.py` read `Campaign.connector`; a resume after the definition is retargeted reads
  both. Action: one source, the campaign's, read by every site. **Rides with:** CONN. **Re-test:**
  retarget a minted campaign's definition; every reader names one connector or the resume refuses.
- **Three TermNorm defaults in core** — `connectors/__init__.py` `DEFAULT_CONNECTOR` and `shipped`
  (repeating the one `serves_uploads` declaration), and `Settings.TERMNORM_URL` defaulting to
  loopback, so a box with no TermNorm lists it configured and health reports it broken. Connector
  keys (`TERMNORM_*`, `N8N_HARNESS_*`, `DBLLMBENCH_*`) live in core `Settings`, and the deploy's
  `BACKEND_*` keys name the TermNorm sibling generically. Action: the upload connector derives from
  its declaration; no endpoint default; each connector declares its own config model. **Rides
  with:** CONN; the endpoint keys with M18's one-endpoint-per-install row. **Re-test:** `git grep -n "DEFAULT_CONNECTOR" -- promptpotter`.
- **An unregistered `backend_type` is a bare `KeyError` at fifteen `CONNECTORS.get` sites** — only
  `dataset_access.py::dataset_connector` refuses it typed (since 2026-11-06); `startability.py` and
  `backend_listing.py` tolerate it with `.table.get`. Lowercase is documented on `Connector.name`,
  enforced nowhere, while the dataset reader lowercases. Action: the one reader of the key resolves
  the Connector and refuses typed, naming the registered set; `_validate` refuses a non-lowercase
  name; callers take the Connector. **Rides with:** CONN. **Re-test:** a dataset naming
  `backend_type: nope` answers 422 on `new`, `start-run` and the pipeline read.
- **Core computes TermNorm's term index for every connector, pushed by two calls** —
  `Session.index_terms`, `loop_start.py` and `origin.py`. Action: one call where the session opens,
  handing the connector's session the bank. **Rides with:** CONN. **Re-test:**
  `git grep -n "index_terms" -- promptpotter`.
- **DSPy splits the model string for pricing twice** — `dspy_module.py` meters `inline_route(model)`
  in the cell while core's `BackendClient.priced_as` splits the same string for the bound; they
  disagree when a dataset sets `provider`. Action: the cell takes the pair core bound. **Rides
  with:** M12. **Re-test:** `git grep -n "inline_route" -- promptpotter/connectors/dspy_module.py` is empty.
- **TermNorm's live datasets (`llm_only`, `entity_profiling`, `llm_ranking`) are refused at parse**:
  `direct_prompt` must gain a `prompt_info`, edited in the TermNorm checkout (operator, 2026-11-06).
  **Rides with:** M18. **Re-test:** `python -m promptpotter new llm_only` parses its pipeline.
- **`ConnectorInspector.tsx::SECURITY_DOC_URL` links § Connection security for every connector**, an
  in-process one too, where no endpoint, token or header applies. Action: the link rides the served
  connector's wire kind. **Rides with:** CONN. **Re-test:**
  `git grep -n "SECURITY_DOC_URL" -- webapp/components`.
- **One act, two names** — 18 of 34 `CommandKind` rows have a `cli_verb` other than the kind, so served
  refusals and descriptions name `set-limits`, `--max-usd`, `resume`, `new` to a host that has
  `change-run-limits`, `start-run`, `mint-campaign` (`spend.py`, `jobs/quota.py`, `preflight.py`,
  `campaign_config.py` budget, `origin_gate.py`'s log). Tool descriptions are developer docstrings
  ("waits on a click", source paths, "the SSE tail"). Action: one short name per act, the kind and
  the CLI verb alike, except the composite `new` / `resume` (operator, 2026-11-07: short and
  intuitive):
  - `pause`, `origin-gate`, `head-to-head`, `set-limits`, `cancel-queued` (the kind takes the verb);
  - `archive-campaign`, `unarchive-campaign`, `delete-campaign` (the verb takes the kind: "archive"
    is the measurement archive, and `delete-cycle` stands beside it);
  - `rename-campaign` (both, for `set-campaign-label` / `rename`);
  - `verify-candidate` / `verify` and `grade-bench` / `bench` are M13's one held-out act.

  Served text names kinds; `StopReasonInfo.cli_command` becomes a typed next act; a gate check
  refuses `path::symbol` and CLI-verb backticks in a tool-describing docstring. A renamed kind
  rewrites the stored command records, so this lands with M2's wipe. **Rides with:** DOORS D7.
  **Re-test:** the `CommandKind` rows whose `cli_verb` is neither the kind, `new` nor `resume` are
  M13's two.
- **A gate hold claims `input_required` and sends no input request** — `host_surface.py::HostSession`
  sets the status; `tasks/result` blocks until the run ends. Action: the hold becomes the task's input
  request (the gate's `GateDecision`, the arms shape for `submit-arms`), answered as the existing
  command; otherwise never claim `input_required`. Untested against a live client. **Rides with:**
  M22. **Re-test:** a host calling `tasks/result` on a held origin gate receives an input request.
- **A refusal is answered four ways** — MCP `tools/call` drops `details` and `refusal_words`, MCP
  `tasks/result` raises a JSON-RPC error, REST serves `ErrorEnvelope`, the CLI a third shape and the
  only door catching `SendRefusedError` / `UngeneratedRequestError` (`RuntimeError`s, a REST 500).
  Action: one refusal value in `application/` each door only renders. **Rides with:** REFUSE.
  **Re-test:** the same refused `start-run` over MCP and REST carries the same `refusal_words`.
- **A queued launch dies with the asking process** — the queue wait runs in the caller
  (`jobs/launcher/launch.py`), so a CLI-derived kind exits and an MCP stdio session ends, while the
  door reported success. Action: the queued job on disk is promoted by whoever frees the slot.
  **Rides with:** M6/M2. **Re-test:** `resume --step-rounds 1` behind a running cycle; it starts
  when the slot frees.
- **Adapter-kept rules that belong under the dispatcher** — the leftover sweep runs before CLI
  launches and in the API's loop but never for MCP stdio or `embedded_run`; `embedded_run.__all__`
  hand-keeps four command wrappers (and `cli/commands/maintenance.py` imports from it). Action: the
  sweep moves under the one admission; the wrappers are deleted. **Rides with:** M6. **Re-test:**
  `git grep -n "def compact_archive" -- promptpotter/application/embedded_run.py` is empty.
- **The idempotency key promises a replay some kinds never give** — `start-checkin`,
  `set-user-settings`, `accept-terms`, `set-api-key` replay nothing. Action: the description names
  which kinds replay, until M2's log-keyed idempotency. **Rides with:** M2. **Re-test:** retry a
  `start-checkin` with its key after a lost answer.
- **The MCP door speaks stdio only, so a browser chat cannot reach it** —
  `promptpotter/presentation/mcp/server.py::serve_stdio` is the one transport. Claude Code and Claude
  Desktop spawn it locally. claude.ai's custom connectors, and every remote host, call over
  Streamable HTTP on HTTPS from the provider's cloud. Action: serve the same
  `promptpotter/presentation/mcp/server.py::host_server` over Streamable HTTP, mounted on the API
  behind its existing sign-in (the box at app.promptpotter.com). It is a second transport of the one
  MCP adapter, not a sixth entry point. **Rides with:** M20; its auth follows
  [`access-model.md`](../operations/access-model.md). **Re-test:** in claude.ai, add a custom
  connector at the box's MCP URL; `tools/list` answers.

### The excision pass (six layer audits and a plan review, 2026-11-05)

One read-only audit per layer, each tracing a real path end to end; then four reviewers read the plan
for transition code, coherence, the risky cuts and the layers no audit walked. Each action is the
largest cut that holds: the layer goes unless something in it varies, and no arc builds on a shape a
later arc deletes. **Rides with:** the arc each group names, by its "Was" name in
[`roadmap.md`](roadmap.md) § The cleanup arcs (plan review, 2026-11-07).

**Decided once, read by every arc below.**
- **Two carriers.** The run's `shared/run_scope.py::RunScope`, bound once where the
  cycle opens, carries the plan, the ledger and the sender. The member's node context yields a per-call
  context holding node, config, round and index only. A `ContextVar` that stays propagates within one
  task. U2 binds `RunScope`, S fills its sender, the potter arc deletes the bundle. **Re-test:**
  `git grep -c "ContextVar(" -- promptpotter`.
- **One subject.** The webapp's hash is not a `domain/cycle_paths.py::Subject` yet: M16b, which owns the
  workspace's keying, moves it onto the same grammar.
- **View state.** Whatever names the viewed thing (subject, candidate, at, view keys, round pick, sample
  set) lives in the hash; a module store holds only transient state.
- **One fold vocabulary**: a `Projection` on `read_model.py::LedgerIndex`. `LedgerFold.feed`, the tracing
  sinks' `on_*` and the `RunCallbacks` doors go (U3).
- **One word per thing.** "Arm" is the round's; a head-to-head's arms become `RunStart`s (U2).
  A host task is a command id (Q1).

**Batch 3: the run's start (U2) and the potter arc (U1b with EP).**
- **Five doors and three job lifecycles meet in one `HeldRun`.** `jobs/launcher/launch.py::_hold`
  switches over three request types; a head-to-head mints through a second door and plans and prices
  before admission, while a single launch admits first; `embedded_run.py::run_campaign` and
  `runner/inner/spawn.py::_open_seat` mint or resume their own way; `admission.py::embedded_share` and
  `admission.py::inner_share` copy one lifecycle beside `run_job.py::run_held_job`. The detached child
  rebuilds its plan through `run_job.py::open_held_cycle` and `mint.py::resolve_resume` (the drift
  check), while the inline path runs the mint's session: two sessions for one run. What varies, and
  stays: what starts (a mint, a cycle, a check-in, mint-or-continue), its admission (a slot that may
  queue only when one run is asked, no slot, the enclosing book, carried by a fork) and how it runs
  (detached, inline, a task); a plan holds a `Session` and cannot cross the process boundary. Action:
  `start(RunStart × N) → [Launched(job, held or none)]`, a `RunStart` holding those three fields; the
  running process builds its `HeldRun` by one drift-checked `open_cycle(hop)`, which binds `RunScope`; a
  fork is `open_cycle` under a carried admission inside `run()`, never through `start`. The two
  `run_records.py::RunWiringRecord` writes stay: they record two moments (declared and admitted budget).
  **Re-test:** `git grep -c "release_slot(\|job_registry.release(" -- promptpotter`.
- **Run init is five modules in the wrong order.** `runner/entry.py::run_optimization`'s preamble,
  `runner/run_prep.py::prepare_run`, `origin.py::establish_campaign_origin` and
  `initialization/loop_start.py::finalize_loop_state` write `session.state` from eight modules, and ORIGIN
  starts before INIT. Action: `open_cycle` in `runner/`, INIT then ORIGIN; it is § Config resolution
  item 5 made concrete. **Re-test:** `git grep -c "session.state.[a-z_]* =" -- promptpotter/application`.
- **Round 0 has two doors.** `origin.py::establish_campaign_origin` walks it before the cycle exists;
  `bench/node_context.py::rewalk_origin` walks it again for the gate and the repair. Seated from outside
  the walk and kept: `origin.py::try_inherit_fork_origin` (a fork's concluded origin), a standing origin,
  and an empty bank. `origin.py::lock_criterion` changes the scorer's id, which keys the difficulty
  ruler, `bench/difficulty.py::DifficultyView` and `indexes/sample.py::SampleIndex`. Action: round 0 is
  seated unmeasured, inherited or standing (an empty bank seats no cells), and walked only inside the round
  loop; its close locks the criterion, re-opens the scorer-keyed state and carries the origin's accuracy
  and formula, which INIT's exit stops serving; the bench pass and its set-aside run after the gate.
  Absorbs § The origin gate (batch-2 reviews, 2026-11-05). **Re-test:**
  `git grep -n "def establish_campaign_origin" -- promptpotter`.
- **Each entry point builds its own answer to a launch**, though the ack's `run_records.py::LaunchedEffect`
  records the job. Action: the dispatcher computes one wire answer (the applied model, else the launched
  jobs, else the record); `checkin.py::StartCheckinResponse` and `launcher/head_to_head.py::HeadToHeadStarted` stop
  being wire models. **Re-test:** `git grep -n "class StartCheckinResponse" -- promptpotter`.
- **`--diag` mints a sibling cycle** (`commands/launching.py::_diag_target`) and `potter/generation_only.py`
  drives its rounds a second way. Action: an unmeasured mode on `RunStart` that the round loop reads;
  both go. Shares `generation_only.py` with the potter arc. **Re-test:** `git grep -n "def mint_diag_sibling" -- promptpotter`.
- **The test builders replay production by hand**: `tests/factories.py::seat` builds a cycle as run init
  does, `tests/factories.py::close_round` replays the runner's append order, and two more mint "as the mint
  does". Action: they call `open_cycle` and the mint. **Re-test:** `git grep -c "def seat(" -- tests/factories.py`.
- **The potter arc (U1b with EP): potter's calls bypass the bench's call door**, and its prompt is a class
  U1b deletes, so the two land as one arc and one re-key. The peers ask through `node_context.py::Calls`;
  potter builds each call by hand (`dispatch/facade.py::build_bundle`, then `llm_call.py::run_optimizer_node`)
  and handles one fault four ways, one silently: the round's adapter step runs under `graceful`, so a
  failed critique is only logged. `escalation/firing.py::Layer` and `escalation/firing.py::TransitionResult`
  are a second member protocol; `dispatch/bundle.py::InjectionBundle` hand-copies run state for every
  renderer; `escalation/rules.py::_first_match` runs a fixed rule table. The fitness formula has a second,
  short spelling (`evaluators.py::resolve_cell_formula`) that only ever reads "fit", banked on four views
  and every arm record. Action: `Calls.ask(prompt, reply=Model)` with one fault policy, and
  `run_optimizer_node` goes; renderers read a potter-owned view of the node context and the ladder, its
  derived reads in a hashed potter module so a prompt change still re-keys, each signal's reads
  satisfiable wherever it renders; the rules three match functions that keep the rule name
  `escalation/rules.py::EscalationEvent` stamps; one formula spelling, the anchors off the records.
  Rewritten modules land at Wave C's paths. **Re-test:** `git grep -n "class InjectionBundle" -- promptpotter`.

**Batch 4: the ledger (U3, with ET) and spend (S).**
- **U3: the ledger read stack.** Three push paths (`ledger.py::CycleEventLog` subscribers,
  `read_model.py::appended_here` and `event_stream.py::CycleLedgerTail` re-reading bytes into its own feed),
  three fold protocols, every `CycleEventLog.open` re-counting the ledger, store accessors four hops deep,
  and `live_dashboard/projection.py::DashboardCheckout` writing a `dashboard.json` only
  `scripts/offline_run.py` reads. Action: `RunScope`'s ledger is the one producer door (`RunCallbacks` and
  the telemetry `emit_*` go); `LedgerIndex` the one live fold with the writing projections on it;
  accessors `append` and `last`. **Re-test:** `git grep -c "CycleEventLog.open(" -- promptpotter`.
- **ET, inside U3: tracing translates the ledger twice.** `tracing/bridge.py::TracingProjection` re-encodes
  records into a sink vocabulary, `tracing/langfuse_sink.py::LangfuseSink` re-encodes it again over
  `tracing/langfuse_client.py::LangfuseLogger`. Action:
  Langfuse and MLflow are each a writing projection on `LedgerIndex` over the typed records; the
  translator and the fan-out go. [`architecture.md`](../architecture.md) §0.5 names
  "Langfuse-shape events" as the tracing bucket's foundation; that line becomes the projections in the
  same commit (operator, 2026-11-05). **Re-test:** `git grep -n "class TracingProjection" -- promptpotter`.
- **U3: the ledger is narrated twice.** `views/readout.py::ReadoutProjection` (the readout log) and
  `domain/activity.py::ActivityFeed` (the chat) each word the same record kinds their own way, each with
  its own hold dedupe and arm labels, and the words already drift; announced arms are folded in five
  places. What varies is only verbosity and lifetime. Action: one narration table from record to `Said`
  with a lifetime: the readout log writes it as lines, the chat keeps the latest per lifetime, and the
  round tables render from fold state. **Re-test:** `git grep -c "AnnouncedArms" -- promptpotter`.
- **U3: one phase act is said three ways, and render text is banked on the ledger.**
  `run_records.py::PhaseRecord` carries a phase string, an untyped event string and a view whose kind
  repeats both; five folds switch on them. `InitEnterView` banks fields nothing reads and display text
  (a cut prompt preview, a short cycle id), and `view_models.py::ViewContext` is a mutable bag on
  `RunCallbacks` that a fork inherits. Action: the typed act is the record, phase and event derive from
  it, text is computed at render, and `ViewContext` goes. **Re-test:** `git grep -n "class ViewContext" -- promptpotter`.
- **S: spend is booked twice and folded many times.** `wallet_store.py::HeldCharge` and
  `wallet_store.py::BilledCharge` repeat `run_records.py::SpendHoldRecord` and `run_records.py::TokenUsageRecord`;
  `spend_book.py::SpendBook` counts through its ledger subscription and through admission.
  `spend_book.py::Admission` writes each home differently: a bill lands on the run's ledger, on the root's
  rewritten copy and on the account; a failed hold and a muted ledger reach the account only; a replay
  reaches the ledger only. One send reads about fifteen `ContextVar`s, and `jobs/coupon.py::keyed_client`
  rebuilds the client per call (it reads the key and grant files, re-folds the charges and hashes the
  key). Action: one charge value (hold id, key source, kind, model, provider, cost, rate, tokens, ledger,
  timestamp) that the ledger records embed; the account takes each send once, at the root, never a
  replay or a mirror; one fold parameterized by subject (the account, a campaign with its tombstones, a
  cycle's own lines) and meter; the sender (key, pacing, book, charges, reuse cache) built once at the
  account bind, on `RunScope`. `charges.jsonl` is a survivor: its migration maps each line one to one and
  keeps its timestamp. The ledger records' reshape wipes run data. **Re-test:**
  `git grep -n "class HeldCharge" -- promptpotter`.

**Batch 5 and after.**
- **U5: one viewed searchpoint.** Six hosts assemble a picked arm from several reads each, the Leader and
  the inspector show one pick at once, three shapes describe one program, and `SelectionContext.tsx::SelectionProvider`
  keys a pick beside the hash; the Leader shows the pick by default, so one pick renders twice. Action
  (operator, 2026-11-05): one searchpoint read and one rendering that hosts give identity only; the Leader
  always shows the leader; the pick lives in the hash and renders once, where
  `ScoringInspector.tsx::ScoringInspector` stands, a region A4 keeps. The webapp's selection half lands here, so
  every candidate component is rewritten once: `SelectionContext.tsx`, `useCandidatesModel`'s bag and the
  workspace's four-way address become the `Subject` and the served arm row. Compare channels and their
  picks are hash state too (`lib/state/compare-selection.ts` today; a reload drops both).
  **Re-test:** `git grep -n "searchpointCopyChoices(" -- webapp`.
- **D: the catch-up pool.** `validators.py::CatchUps` and `nodes.py::NoCatchUps` declare one protocol with
  one implementation, looped bench → race → bench. Action: the bench owns the pool and the race exposes
  only its priors. **Re-test:** `git grep -c "NoCatchUps" -- promptpotter`.
- **INVESTIGATE: each resume decision is spelled twice**, live and by a per-member replayer over untyped
  dicts (`potter/resume.py::_replay_round_winner`). Whether the live methods are pure over a seated round
  decides if the replay calls them. **Re-test:** `git grep -n "def _replay_round_winner" -- promptpotter`.
- **Two word tables over one enum, three times** (`evidence/head_to_head.py::GUARD_STATE_INFO` beside its labels,
  `paired_reading.py::READING_STATE_KIND` beside its info, `origin_panel.py::ROUND_ADVANCE_STALL` beside its
  info), and `library.py::LibrarySnippet` flattens a cited-or-authored snippet into nullable fields.
  Action: one table each, its words outside the marked decision units (`READING_STATE_KIND` and
  `ROUND_ADVANCE_STALL` key every treatment digest); the snippet keeps its union. Rides with the arc
  that next owns each file.

### The quantum-leap pass (2026-11-05)

Batch E's reviews found one shape in every arc: a fact held or re-derived at many sites, kept
consistent by each site remembering a convention, so review finds one violation at a time and every
fix adds a site. A leap gives the fact one owner and derives every other site by construction, held
by an exhaustive table or state-machine test. Each is a pattern a mature project runs on. **Rides
with:** the arcs that were the Q rows ([`roadmap.md`](roadmap.md) § The cleanup arcs (plan review,
2026-11-07): M2, FEED, REFUSE and M14b, M5).

**Decided once, read by every arc before a leap.**
- No arc before Q1 holds a command's state anywhere but the record `commands/dispatcher.py` writes
  and the MCP task memory batch E leaves standing (`host_surface.py::HostSession._tasks`).
- No arc before Q2 adds a cadence, a `revalidate` list or a cross-invalidation beyond batch E's; a new
  read polls on the descriptor it has, and A4's conversation opens no second `EventSource`.
- No arc before Q3 adds a `logger.warning` in `application/`: a new fault is a record kind with a reader.

- **Q1: one command log.** The fact: what was asked of the system and where it stands. Today it is
  split: `run_records.py::CommandRecord` and `run_records.py::CommandAckRecord` land on whichever
  ledger `commands/dispatcher.py::dispatch` picks (the cycle's, its root's for a delete, the draft's,
  the workspace's), a draft's records move with its ledger (`campaign_store/store.py::adopt_ledger`,
  from `jobs/mint.py::_mint_campaign`), a deleted cycle's go to the root's, the loop reads its
  standing commands off its own ledger (`runtime_flags.py::standing_controls`), the trail folds
  command lines out of every ledger and a tombstone's copies (`store/account_trail.py`), idempotency
  is a ledger scan (`commands/dispatcher.py::_find_idempotent_command`), and whether the filer is alive
  is asked beside all of it. A running act's status and stop live only in MCP memory
  (`host_surface.py::HostSession._tasks`, `HostSession._cancel_task`, `host_surface.py::cancel_acts`):
  REST serves no job read, and the webapp has no stop for verify or bench. A pass's stop address is
  built per door (`commands/measuring.py::pass_pause`, `cancel_acts`, the CLI's Ctrl+C in
  `cli/commands/launch.py`, `new.py` and `noise_floor.py`), and the answer a door returns
  (`command_kinds.py::CommandAcceptedBody`) does not name the address the dispatcher resolved. Sites:
  `git grep -l "CommandRecord\|CommandAckRecord\|standing_controls(" -- promptpotter`. Owner: one
  account-scoped append-only log; a command is one record stream in it (filed, applied, refused,
  voided, cancelled), each event written with its filing in one step, its status a fold of its own
  events, its subject the address it was filed on and the one it landed on (a draft's Start lands on
  the cycle the mint adopts, so the adopt step moves no record). The ledger keeps what HAPPENED
  (`RunPhaseRecord`, the effects); the log keeps what was ASKED. The pattern: event sourcing's
  per-aggregate stream with status a fold (EventStoreDB, Axon). Deletes: the two record kinds from
  the ledger, `ledger_scan.py`'s command fold, the trail's per-ledger walk and
  `run_records.py::TrailTombstoneRecord`'s copied commands, the idempotency scan, `HostSession._tasks`
  and `_cancel_task`, the per-door stop addresses; a pass with no cycle (`launcher/admission.py::paid_pass`, `hop=None`)
  is addressed by its command id, so its stop is one command at every entry point. Shares with U2:
  the adopt step and U2's one launch answer, which is a log read. Edges inside: the loop's pending
  read becomes a by-subject read of the log, so the log keeps its own per-subject offsets, a fold,
  never a file beside it; a command filed by a process that died before applying stays "filed", and
  the fold reads the filer's OS lock (§0.5) to derive "filed, filer gone". Invariant tests: the
  exhaustive `(status, event)` table yields one status or a refusal for every pair; for any
  interleaving of file, apply, refuse, cancel and void with any ledger move or delete, the fold after
  the move equals the fold before. Its acceptance table is the command-state table of
  `b79e18678:tests/test_security.py` § 5, "The command state machine" (branch `arc/ea-8`, cut from
  batch E), plus a
  `RunState.scoring` row: a sandbox's pause, and a line run selecting. Amends
  [`../architecture.md`](../architecture.md) § Control-remote and [`../decisions.md`](../decisions.md)
  § 0001 in its commit (operator, 2026-11-07): both place the record on the cycle's ledger. **Re-test:**
  `git grep -n "class CommandRecord" -- promptpotter/domain/run_records.py`;
  `git grep -n "def _cancel_task" -- promptpotter`.
- **Q2: one change feed — the server says what moved.** The fact: that a served subject moved. The
  webapp guesses it at many sites: a cadence per read (a descriptor's `cadence` on
  `read-cache.ts`'s beat, or the `intervalMs` its caller reads off another read; the constants in
  `cadence.ts`), a re-ask per write (`useCommand.ts::useCommand`'s `revalidate` and the
  `read-cache.ts::invalidateReads` calls beside it), the viewed dashboard's phase re-ask and slow beat
  (`api/reads.ts::onViewedPhaseChange`, `poll.tsx::viewedIntervalMs`), `useEvidence.ts`'s pulse and
  the listing's bill re-ask of the coupon (`api/reads.ts::hierarchyRead`'s `onChange`), focus,
  visibility and online revalidation in `read-cache.ts`, twice on a tab return, and its failed-read
  cadence (`cadence.ts::RECONNECT_POLL_MS`); the server guesses beside it with mtimes and clock edges
  (`runtime_flags.py::run_state_stamps`) and one store-wide version
  (`application/hierarchy.py::entries_version`). A settled read rests until its subject's version
  moves: EW built that resting on the listing's poll and cut it (operator, 2026-11-05), because only
  the server serves a per-subject version; its subject names are the stream's. Sites:
  `git grep -lE "intervalMs|cadence: |invalidateReads\(|revalidate" -- webapp/lib webapp/components`. The server
  writes every ledger and runs every command, so it knows. Owner: a version per subject, the length
  of the ledgers the subject folds, from the one producer door (U3's `scope.ledger.append`), plus the
  moves no append makes (a process gone, a beat stale), published by the sweep that detects them. The
  door and `commands/dispatcher.py` publish `{subject, version}` on one account SSE stream
  (`routers/reads.py::_events` is the shape; `useCycleEvents.ts::useCycleEvents`
  folds in); the cache subscribes once and re-asks exactly the moved subject; a read's descriptor is
  `{id, load}` and nothing more; `routers/reads.py::_conditional`'s ETag is the same version. Which
  reads an append moved is the projections that consumed the record (U3), derived, never a hand
  table: before U3 that table is the convention site this leap removes, so it waits on U3. The
  pattern: server-pushed invalidation with the fetch as truth (Phoenix LiveView, Firestore listeners;
  the client half is SWR / React Query's `invalidateQueries`). Deletes: the read intervals,
  `intervalMs`, `invalidateReads`, `revalidate`, the phase re-ask, the slow beat, the bill re-ask, the
  pulse, the focus, visibility and online hooks, the second channel, `run_state_stamps`' clock edges, the store-wide version. In
  U4, U5, W2, B and A4 nothing is added beyond a descriptor; U5's arm row and A4's conversation ride
  the one stream. One failure rule: the stream lost → reconnect, then re-ask every mounted read once.
  Edges inside: an event before, after or without the read it hints, or twice: harmless by
  construction, a hint only triggers a read, the body's version is what the cache keeps, a lower
  version never replaces a higher. Invariant tests: "hint, not truth" — for any sequence of events,
  reorderings, drops and duplicates, the cache's body equals the server's at the last read and never
  regresses in version; on the server, every record kind a projection consumes names a subject in a
  table derived from U3's projection roster, and every input `run_state_stamps` reads today is an
  append or a sweep publication. **Re-test:** `git grep -nE "intervalMs|cadence" -- webapp/lib/api/reads.ts`.
- **Q3: one fault channel — a fault is a value.** The fact: a step failed or was skipped, and why.
  Today it is a log line at many sites, each kept honest by remembering to serve it: engine decisions
  narrated into `logger.warning` (§ Config resolution item 10, moved here: `bench/resume_and_fork/repair.py`,
  `runner/origin_gate.py`, `runner/termination.py`, `escalation/firing.py`),
  `runner/origin_gate.py::run_origin_gate` logging a failed re-score and holding again,
  `origin_listing.py::list_origins` logging and skipping an origin whose dataset no longer resolves
  (so the list is silently short), `campaign_store/store.py::CampaignStore.bank_final` swallowing a
  failed export write under `shared/errors.py::graceful` (the export is the run's handed-on prompt).
  Sites: `git grep -c "logger\.\(warning\|error\|exception\)(" -- promptpotter/application`;
  `git grep -l "^import logging" -- promptpotter/application`. Owner: inside a run, a typed fault
  record through the one producer door, read by a projection (`run_records.py::RoundWarningKind` is the
  self-heal half already); inside a read, a field of the row it failed, served as its `Said`; `logging`
  leaves `application/` and `domain/`, banned at the gate as `gate.py::_IMPORTS_OPTIMIZER` bans an
  optimizer import. `infrastructure/`, `presentation/` and `main.py` keep theirs: a disk write that
  failed has no ledger to land on. The pattern: errors as values (Go, Rust's `Result`) and faults as
  data the next reader folds (Kubernetes `status.conditions`). Deletes: the logging imports and
  `graceful` in `application/`, the entries above. Edges inside: a fault kind nobody reads (U3's check
  fails a kind no projection reads); a row's fault field no surface renders (it is a `Said`, and W2
  renders every served `Said`). Invariant test: the gate lists every `except` in `application/` and
  `domain/` that neither raises nor hands its fault on (an append through the door, or a return whose
  type carries it), and the list is empty; every fault kind has one projection reader and one word
  row. **Re-test:** `git grep -l "^import logging" -- promptpotter/application`.
- **RS: one running set — which nodes a run reaches.** The fact: the nodes a run calls, which are its
  chain, its phase pipelines and the declared nodes off the chain, less the excluded ones. The rule:
  a which-nodes question reads the running set, and an order question reads the chain. Today most
  readers ask the chain (`pipeline_schema.py::PipelineSchema.active_steps`), so a phase or off-chain node
  is missing from:
  - run init (`pipeline_resolve.py::_validate_model_ownership`, `_validate_prompt_reach`,
    `_apply_starting_prompts`) and the Start gate (`origin_readiness.py::_check_node_models`): a phase
    LLM node with no model passes;
  - every reader of `PipelineSchema.prompt_nodes`;
  - potter's view (`dispatch/prompts.py::effective_optimizer_prompts`, `injections/wounds.py::_writable`,
    `layer_state.py::critique_axes`, `layer_state.py::_r_rebase_capability`);
  - `children.py::carried_writes`, which admits over one set and refuses over another;
  - the price and fence (`preflight.py::task_routes`, `application/free_tier.py::outside_free_tier`),
    `pipeline_resolve.py::_runs_with`, the scoring role questions (`scoring/evaluators.py`,
    `measurement_log.py`) and the CLI's `cli/commands/launch.py::pipeline_summary`.

  Others ask the un-narrowed chain where they should ask the narrowed one
  (`wiring.py::_warn_if_labels_have_no_ranker`, `_verify_required_observation_keys`,
  `jobs/mint.py::_mint_after_framing`). Some rules are spelled twice: "phase" names both
  `phase_pipelines` and the escalation buckets; the default-is-every-node rule sits in
  `pipeline_parsing.py` and `dataset_access.py::declared_pipelines`; "the one node" sits in
  `draft_build.py`'s `is_single_node`, `node_context.py::Target.prompt_node`, and the webapp's
  `PipelineFlow.tsx` and `nests[0]` picks.

  Sites: `git grep -n "active_steps" -- promptpotter`. Owner: one `running` set, derived once on the
  resolved schema. Every which-nodes reader reads it, and the phases are laid once, by the optimizer's
  manifest, escalation buckets included.

  Batch E's EN tried this rule in-layer and was cut back to main (operator, 2026-11-05). Its last review
  is the spec. Hold each door with a test that fails when its fix is reverted: run init through the
  mint, the campaign door, the dataset door under `exclude_nodes`, the origin reopen with a seed
  overlay and a narrowing, phases declared out of name order, and one draft edit that picks a Mode and
  narrows the node it adds and the node it drops. A node off the chain is drawn on its own rail row.

  It moves what L4 decides: potter's `l2_context` and `l3_plan` become L1 levers, and the next paid run
  misses the L4 cache once. The operator answers that at the arc's start. **Re-test:**
  `git grep -c "active_steps" -- promptpotter`.

**Weighed and left.** Liveness (a verification orphaned at two seams, `started_at === null` in the
browser): `runtime_flags.py::derive_run_state` is the owner §0 already names, the sites are U3's and
W2's. The run lifecycle as one state machine: the command side is Q1's fold, the run side
`derive_run_state` and `phases.py::RUN_PHASE_INFO`'s admission table, one owner each. L4 measurement
identity hashed off rendered bytes instead of the AST reach (`optimizers/nodes.py`'s `source_digest`):
the static reach is §0.5 load-bearing and honest before any call runs, and stops at plumbing and at
a refusal. Served words, typed records, the run scope, the subject, the read catalogue and
the typed file rows: W2, U3, U1a, EA and K3a each already are that leap. `??` defaults on served
facts: a lint over EW's one type door, not a mechanism.

### Config resolution and run scope (backbone hunt 2026-10-31, ranked by impact radius)

**Rides with:** one arc.

5. **`Session` / `LaunchState` is a staged builder modelled as a bag of Optionals** —
   `initialization/session.py`; ~62 guard or sentinel sites (`require_scorer()`, `launch_clock()`,
   `campaign_id=""`); `run_observers.declare_running` skips silently on a None ledger. Action: each init
   step returns the next typed stage. **Re-test:** `git grep -c "require_scorer\|require_partition" -- promptpotter`.
6. **Slot lifetime is a hand-rolled `except BaseException: release…` at ~20 sites** (admission,
   `run_job`, head-to-head, mint-and-start, check-in, embedded run). Action: the registry hands out a held
   slot as a context manager. **Re-test:** `git grep -c "release_slot(\|job_registry.release(" -- promptpotter`.
8. **A root's typed refusal is served as one generic caveat** — `pipeline_resolve.py::campaign_runs_with`
   and `line_config` answer `None` for any `_ROOT_UNRESOLVED`, and `application/hierarchy.py::_UNRESOLVED`
   serves the same sentence whichever refusal it was. Action: the refusal rides the row, its message
   served. **Re-test:** read `application/hierarchy.py::_UNRESOLVED`.
9. **A minted cycle's dataset layer is read off today's definition** — `pipeline_resolve.py::_campaign_merge`
   lays the dataset's `node.config` and declared nodes from `dataset.yaml` as it stands; `ResolvedRun`
   records the laid declaration but not the layer the run sent. Action: run init records the dataset
   layer; a read lays the recorded one. **Re-test:** `git grep -n "runnable_definition" -- promptpotter/application/pipeline_resolve.py`.

### The engine swept by shape (debt hunt 2026-11-02)

Grouped by the arc each would ride; workaround comments and dead top-level names came back clean.

**A. The pipeline carrier is typed.** **Rides with:** the next change to `pipeline_resolve.py::ParamLayers`.
- **`pipeline_params` is an untyped `dict[str, Any]`**: engine keys (`model`, `provider`,
  `route_order`, `max_tokens`, …) are string-read with mixed tolerance (`cfg["model"]`,
  `cfg.get("model")`, `str(cfg.get("model", ""))`); the reserved `steps` list shares the bag with the
  node dicts (`pipeline_overlay.py::node_config_items` skips it by `isinstance`, and `"steps"` is
  spelled again across connectors and domain, past `domain/CLAUDE.md`'s probe); the prompt is split
  off its config by `k != "prompt"` beside `search_point.py::strip_rendered_prompt`. Three merges
  (`campaign_config.py::merge_config_layers` / `merge_node_overlays`, `pipeline_overlay.py::apply_node_overlay`)
  fold one thing. An optimizer's llm nodes already read `search_point.py::NodeConfig`; the
  free tier's per-node bounds still land in `CampaignConfig.pipeline_overlay`
  (`application/free_tier.py::with_free_tier_layer`), read there by ~40 files, so they join `ParamLayers` as a
  layer of their own only with this sweep. Action: `PipelineParams(steps, nodes: {name: NodeConfig})`
  parsed once at the overlay seam, engine keys as fields, the backend's params one dict.
  **Re-test:** `git grep -c -E 'pipeline_params: (dict|Mapping)\[str, Any\]' -- promptpotter`;
  `git grep -n '"steps"\|k != "prompt"' -- promptpotter`.
- **`connectors/protocol.py::InProcessWorkload.experiment` is a `Mapping`** that
  `runner/inner/tasks.py::parse_resolved` re-parses at each reader (`dbllmbench.py` reads it with
  `.get("questions") or []`). Action: each connector declares its experiment model; the workload
  carries it parsed. **Re-test:** `git grep -n "parse_resolved(" -- promptpotter`.
- **`search_point.py::NodeConfig.prompt_edits` is a `dict[str, str]`** beside the
  `OptimizerPromptTemplate` that `pipeline_manifest.py::typed_config` validates the same edits as and
  drops; `running_prompt` lays the dict back with `model_copy`. The typed field cannot land in place:
  `opt_search_point.py` imports `search_point.py`, which holds the call-key rosters. And the template
  rides beside its config: `llm_call.py::run_optimizer_node` takes a filled `template` and an
  `LLMCallContext` whose `config` named it (`critique.py::distil_critique`,
  `firing.py::_run_transition`, `task_context.py::run_checkin`), so the two can disagree. Action:
  `PROMPT_FIELDS` and `OptimizerPromptTemplate` move to a leaf module both import, `prompt_edits`
  becomes a partial template, and the call context carries the running template its fill reads.
  **Re-test:** `git grep -n "prompt_edits: dict" -- promptpotter`; `git grep -n "template=filled" --
  promptpotter`.

**B. Run-data payloads are models.** **Rides with:** § Typed state "Run files read as dicts" and the
next run-data reshape.
- **`run_records.py::ResumeCheckpointRecord.inputs_ref` / `data` are `dict[str, Any]` on the ledger**,
  built by hand (`potter/race.py`, capo's `context`) and read back by string key with casts in each
  optimizer's replayer; a renamed key surfaces only at resume as `replay_error:`. Action: each
  `CheckpointKind` declares typed inputs/data. **Re-test:** `git grep -c -E 'inputs_ref\["' -- promptpotter`.
- **`RaceStandingRecord.paired_breakdown` is an untyped stat bag**: potter (`pobb/checks.py`) and capo
  write different keys, and `views/readout.py::on_race_standing` reads `.get("p_better", 1.0)` to sort
  and `.get("p_better", 0.0)` to print. Action: a typed per-prior model. **Re-test:** `git grep -n '"p_better"' -- promptpotter`.
- **The stale-data ladder's outcome has two owners**: `sample_measurement.py::execute_stale_data_protocol`
  stamps one of five exclusive bools on `MeasuredCell` that `render/sample.py` re-reads as an `elif`
  chain; the ladder is string-keyed, and a pause requested mid-ladder returns the degraded cell with
  no outcome marked. Action: one outcome enum on the cell, the pause exit among its values, the ladder a `match`.
  **Rides with:** M4.
  **Re-test:** `git grep -nE "switched_out|persistently_degraded|config_fundamental_skip" -- promptpotter/domain/scoring.py`.

**C. Served reads fold the ledger.** **Rides with:** § One record shape for every scoring pass.
- **`hard_samples.json` is a checkout read back as served data**: `runner/output.py` writes it under
  `graceful`, and `scoring/measurement_log.py::_cycle_view` serves the leaderboard order from it, empty
  when absent (a failed write served as "no scale") and with no `at` (a replay page orders by today's
  file); the dataset scope derives its view instead (`_dataset_view`). Action: the cycle view derives
  from the fold. **Re-test:** `git grep -n "read_hard_samples(" -- promptpotter`.
- **`runner/output.py`'s artifact writers run imperatively, outside the projection roster**:
  `runner/output.py::write_log_md`, `runner/output.py::write_review_md`, `runner/output.py::publish_export`
  and `runner/output.py::write_hard_samples_artifacts` are called only from `runner/round.py`,
  `runner/cycle_close.py` and `runner/grade_bench.py`. So `log.md`, `review.md` and the export lag
  any other act that moves the ledger, and nothing says so (architecture §0 Display: a ledger
  subscriber writes only the artifact it declares). Action: each a writing projection on the
  ledger, or the file goes where `readout.log` and `index.json` already carry it.
  **Re-test:** `git grep -n "write_log_md(\|write_review_md(\|publish_export(" -- promptpotter/application/runner`.
- **A running verification is orphaned at two seams** (`live_dashboard/projection.py::_handle_run_wiring`,
  `served_dashboard.py::_serve`) and `orphaned=` computed a third way in `_serve`; `_round_axis` and
  `_walk` each re-decide whether `current_round` closed, by different predicates. Action: derive it
  once, from ledger plus producer lock. **Re-test:** `git grep -n 'm.state == "running"' -- promptpotter`.

**D. One LLM-call seam.** **Rides with:** any pass in the files named.
- **`scoring/evaluators.py::Evaluator` is one class for two contracts** (`per_sample` async vs
  `per_round` sync, different kwargs) held apart by runtime guards (`_validate_evaluator`,
  `inspect.isawaitable`); its `Scope` collides with `campaign_config.Scope` and `data_root.Scope`.
  §0.5-bearing: state the case before acting. Action: two evaluator types.
  **Re-test:** `git grep -n "isawaitable\|iscoroutinefunction" -- promptpotter/application/scoring/evaluators.py`.

**E. Absent is not zero.** **Rides with:** any pass in the files named; the first item moves what the
loop decides, so it lands with an offline run.
- **Three bars for "is this θ lift real"**: `potter/escalation/state.py::_improved` reads
  `lift > current.se` (one arm's se), `potter/election.py::_lift` takes `hypot` of both arms', and
  `potter/dispatch/injections/panels.py::detectable_rows` takes `a.se * sqrt(2)` (the frontier's se
  for the parent's too). Both readings share cells, so none is the paired se. Action: one lift test on
  `AbilityReading`, read by all three, its se decided in M19 (the election's bound). It moves what
  the loop decides and shapes the optimizer prompt, so it lands with M19's offline run and re-key.
  **Re-test:** `git grep -n -E "lift > current.se|sqrt\(2" -- promptpotter/application/optimizers/potter`.
- **Money text has no sub-cent rule, so two spellings stand beside `domain/spend.py::usd_text`**: a
  cell's price at five places (`preflight.py::_RoundShort.costing`,
  `cli/commands/head_to_head_price.py::_human`), which `usd_text`'s four would print as `$0.0000` for
  a cheap model, and `panels.py`'s optimizer-prompt spelling, which re-keys the digest when moved.
  `usd_text` and webapp `fmtUsd` are one rule and change together. **Open (operator):** the rule
  itself — fixed places pad a round figure (`$0.50` reads `$0.5000` today), so significant digits
  under a dollar is one candidate. Then both sites join `usd_text`; the panel rides M8.
  **Held:** the rule. **Re-test:** `git grep -c -E '\$\{[^}]*:[,]?\.[0-9]f\}' -- promptpotter`.

**Singles.**
- **`infrastructure/identity/paths.py::DefaultClaim.renamed` is written and never read**: the marker
  `default_claim.py::maybe_claim_default` writes is read only for its `user_id`
  (`registered_user_id`). It is survivor data (its `store/data_root.py` row), so the delete is a landing migration that rewrites each marker without the
  field, run once and then deleted ([`../decisions.md`](../decisions.md) § 0011). **Rides with:** the
  next survivor-row reshape. **Re-test:** `git grep -n "\.renamed" -- promptpotter` finds no reader.

### The file layout (import-graph pass 2026-10-31)

**Rides with:** after the config/scope arc above; merges mostly cut duplicated import blocks (whole
tree ≈ −10% cross-file edges, ≈ −600 LOC), the LOC win is the root fixes inside them. Measure with an
import-graph script before and after; close each wave with `gate.py --only layering,help-imports,handler-tables,doc-claims,instruction-files`.

- **Wave A — presentation and infrastructure, no digest re-key:** CLI collapse
  (`cli/campaign_runner.py` + `parsers.py` + `commands/verbs.py` → `cli/main.py`; small read verbs →
  `commands/reads.py`, measure verbs → `commands/measure.py`; `handler-tables` names
  `campaign_runner._COMMANDS_PACKAGE`); `infrastructure/identity/` 12 →
  3 (`oidc`, `grants`, `accounts`); `tracing/` 7 → 4 with `TracingProjection` building its own sinks
  (drops `Session.langfuse`, a second door into tracing); `projections/live_dashboard/` → one module;
  LLM wire types into `llm/base.py`.
- **Wave B — the application backbone:** `application/runner/` 18 → 7 files (loop, round, output,
  entry, bench, mint, inner/spawn) deleting the ledger-scan forwarders (`_spent`, `read_line_spend`,
  `fresh_campaign_id`); loose top-level modules regrouped (cycle reads, hierarchy, origin + `loop_start`);
  the optimizer registry (`optimizers/__init__.py`'s 146 lines), `knobs.py`, `run_callbacks.py` into
  `optimizers/nodes.py` — breaks the hidden 16-module cycle (`TYPE_CHECKING` / in-function imports hide 8 cycles).
- **Wave C — the optimizers, one re-key batch:** `potter/l1/` → `l1.py`, `validators/` 7 → 2, peer
  optimizers' `prompts` + `state` into `members.py`, `pobb/` and `escalation/` flattened, and
  `runner/inner/tasks.InnerCells` moved into `domain/l4` (optimizer code imports a runner internal).
- **Rejected:** a concept-first vertical re-cut (§0's "One rejected re-cut").

### The webapp's vanilla pass (audit 2026-10-29)

- **Wording authored in the browser**: `lib/prompt-fields.ts::PROMPT_FIELD` still labels a served
  set client-side (arc U1b's file). `sample-trajectory.ts::MOVEMENT_CLASS` maps the served
  `SampleMovement` to an ink class, and `PipelineFlow`'s `tone: "accent" | "neutral"` is chosen by each
  host: both are ink with no words, so they wait until a served word row carries its `Tone`
  (`domain/said.py`) and do not ride the Worded re-key. `shared/clock.py::duration_words` twins
  `format.ts::fmtDuration` because the browser formats clocks that tick between polls; one served
  formatter would need a served tick. **Re-test:** `git grep -n "PROMPT_FIELD\|MOVEMENT_CLASS" -- webapp/lib`.
- **The seed-once latch** hand-built at each
  `git grep -n -i "seeded" -- webapp/components webapp/lib` site (one hook is the fix, and
  `lib/hooks/` is a new arc's); `STORAGE_KEYS` mixes hook and
  non-hook keys and `side-panel:*` has no `promptpotter.` prefix.
  **Re-test:** one latch.
- **Modules still off the `lib/CLAUDE.md` § Where a module lives rule**, blocked by the ingest
  rebuild's files, which import them: to `state/` — `hard-samples`, `lineage`,
  `poll`, `workspace`, `ingest-flow`, `scoring-mask`, `chat/thread`, `SelectionContext` (as
  `selection.tsx`), `hooks/useConnector`'s provider; to `derivations/` — `cell-means`,
  `prompt-fields`.
  Chrome still holding a surface: `shell/searchpoint/`, `shell/scoring/` (ingest imports it),
  `shell/node-surface/`, root `PairedLift` / `SummaryBlock`; a surface holding chrome:
  `dashboard/pipeline/` (`git grep -n "dashboard/pipeline" -- webapp/components/ingest`). Hard samples and compare each spread over
  several folders (`git ls-files webapp/components | grep -i -E "hard-?sample|compare"`).
  **Re-test:** `git ls-files ':(glob)webapp/lib/*.tsx'` lists none.
- **Adapters over a flat served shape**: `useCandidatesModel.ts` joins every source it imports;
  `.facts.` opened at `git grep -c "\.facts\." -- webapp/components webapp/lib`; liveness
  re-derived at each `git grep -n "phaseWalks(" -- webapp/components` site.
- **A view family that is chrome**: `components/shell/families.tsx::FAMILIES` holds a family with a
  null pane (the phone's campaign list is the sidebar), so naming it on a wide screen opens a chip
  that draws nothing. **Re-test:** every family has a pane.
- **The Vite build leaves three things standing**:
  - The derivations barrel pulls the network layer into every chunk that imports it.
  - Babel (React Compiler) is 87% of build time.
  - Chunks exceed 500 kB.

  **Rides with:** M1.
- **Several doors into one state**: candidate selection minted at each
  `git grep -n "selectedCandidateOf(" -- webapp` site, the sample set written through
  `setSelectionForSampleSet` from several surfaces, `FileTree::onSelect` drilled through its parents.

### The candidates surface (review 2026-11-01)

Seen while removing the canvas tooltip; the panel work is [`roadmap.md`](roadmap.md) § Arrangeable workspace.

- `useCandidatesModel` writes the module store during render (the per-cycle metric and overlap seeding).
- Two sources for one question: `wonOnTheta` reads `facts.elects_on`, `electedMetric` reads `dash.display_metric`.
- Three interval formatters: `format.ts::fmtInterval`, `VerifyReading::band`, `SearchpointDrillIn::band`.
- Two `cell_means` renderers: `SubjectSignals` and `channel-parts::CellMeans`.
- Three ways into Compare (the toolbar menu, `CompareOriginAction`, the forest).
- Teaching prose in `title=`: the `FitnessLegend` hint, the toolbar, the dendrogram `<title>`, `CompareOriginAction`, `RoundVerdict`.
- `TrajectorySeriesView::SeriesHoverPopup` is a hand-built popover beside `ui/Popover`.
- `EvidenceCharts` and `CostStrip` lost their hover readouts with the tooltip; a per-cell or per-round value they need opens in the DOM.

### The webapp swept by shape (debt hunt 2026-11-02)

Grouped by the arc each would ride. Came back clean: dialogs, clipboard, `EventSource`, `<table>`,
component `fetch(`, `setInterval`, `useEffect` resets, client sorts, unfiled `Record<…, string>` tables,
fixtures.

**A. One arm shape, served.**
- **A live arm and a filed arm arrive in two shapes** (`LiveCandidate` flat; `RoundView.arms[key].individual`),
  so derivations come in pairs (`candidateSearchPoint` / `liveCandidateSearchPoint`, `…Verdicts`,
  `…ObserveConfig`), `ArmRow.source: "history" | "inflight"` branches in `MeasurementRun` and
  `SteerForkPanel`, and `round-candidates.ts::roundCandidates` stitches `dash.rounds` with
  `current_round.candidates`. "Is round N filed" is asked three ways (`useRound.ts::isRoundUnfiled` by
  membership in `dash.rounds`; `roundCandidates` and `search-point.ts::roundObserveTarget` by
  `round_axis.completed`). Action: one served arm shape carrying a filed/live state.
  **Re-test:** `git grep -nE "liveCandidate(SearchPoint|Verdicts|ObserveConfig)|round_axis.completed.includes" -- webapp/lib webapp/components`.

**B. Served words own every wording.**
- **Glyph tables over served enums**: `forest-layout.ts::KIND_GLYPH` / `TRIGGER_GLYPH` /
  `DIRECTION_GLYPH`, `Criterion.tsx::TERM_GLYPHS`, `FitnessRankSummary::MOVE_MARK`, a literal "⑂" in
  `ForestRows`. Low weight. Kept out of the Worded re-key because several of the keyed enums (the
  forest's kinds and triggers) are not word tables yet. Action: the glyph rides the served word rows.
  **Re-test:** `git grep -nE "_GLYPHS?\b|MOVE_MARK" -- webapp/lib webapp/components`.
- **"Measured in N/M rounds" is counted in the browser** (`sample-set.ts::roundsCoveringSample`, read by
  `SampleSetControl`), and `fullyCovered` counts rounds with any selection. This is a count, not a
  word, so a served coverage field is a dashboard reshape outside the wording arc. Action: served
  per-sample coverage. **Re-test:** `git grep -n "roundsCoveringSample\|fullyCovered" -- webapp`.

**C. Lineage reads the served tree.**
- **A campaign's short label is parsed three ways**: `format.ts::shortId` (`{dataset}__{rand6}`),
  `ForestRows::shortOrigin` (`cycle_`), `ids.ts::shortFamilyTail`, used by `ComparePane::channelNames`,
  `EvidenceCharts::seriesLabel`, `SearchpointPanels`. Action: a served short label per subject and node.
  **Re-test:** `git grep -nE "shortId\(|shortOrigin\(|shortFamilyTail\(" -- webapp/components webapp/lib`.
- **`ids.ts::rootCycleId` regexes `_fork_` / `_diag_`** to give `useLineage::parentId` /
  `isInheritedSibling` what the served tree carries. Action: read parent and spawn off the tree.
  **Re-test:** `git grep -n "rootCycleId(" -- webapp/lib webapp/components`.
- **`SearchpointPanels.tsx::buildSpines` walks `parent_ids[0]`** beside the served `main_line`
  (`lineage-candidates.ts::mainLineOf` says never walk up). Action: spines from `main_line`.
  **Re-test:** `git grep -n "parent_ids\[0\]" -- webapp/components`.
- **`hooks/useLeafDatasetName.ts` reads a campaign pipeline for a `dataset_name`** the viewed subtree's
  entry already tags (`application/hierarchy.py::_campaign_tags`). Action: read the tag; delete the hook.
  **Re-test:** `git grep -n "useLeafDatasetName" -- webapp`.

**D. One selection and view store.** Extends § The webapp's vanilla pass "Several doors".
- **The picked candidate lives in two stores**, `workspace.tsx::viewedCandidateId` and
  `SelectionContext.candidate`: `useSelectNode.pick` writes both, `MeasurementRun.onSelectCandidate`,
  `ForestCard`'s ctx and `TimeRay` only the second. Action: one owner.
  **Re-test:** `git grep -nE "viewedCandidateId|setCandidate\(" -- webapp/lib webapp/components`.
- **`showForest` / `expandedLanes` sit in `state/candidates.ts` and `view-memory.tsx::CampaignView`**,
  written together by `hooks/useLineage.ts` and seeded during render by a latch; the hook's name
  collides with `lib/lineage.tsx`. Action: view memory alone owns them; rename the hook.
  **Re-test:** `git grep -nE "showForest|expandedLanes" -- webapp/lib`.
- **`workspace.tsx::WorkspaceState` exposes the viewed address four ways**: `viewedPath`, the root as
  unqualified `campaignId` / `cycleId`, `leafCampaignId` / `leafCycleId`, and callers running
  `pathRoot` / `pathLeaf(viewedPath)`. Action: one address value with named root and leaf.
  **Re-test:** `git grep -nE "leafCampaignId|leafCycleId|path(Root|Leaf)\(viewedPath" -- webapp/components webapp/lib`.

**E. Read discipline.**
- **`hooks/useNestedPipelines.ts` walks the nest client-side** with its own `MAX_DEPTH` and self-nest
  test, worded into `truncated`. Action: the pipeline read serves the whole nest and its depth.
  **Blocked by** K1 (`application/pipeline_resolve.py`). **Re-test:** `git grep -n "MAX_DEPTH" -- webapp/lib/hooks`.

**F. The node editor reads served row facts.**
- **Key-name tests stand in for row facts** (I9): `NodeSurface::onAuthor`, `node-config.ts::effortLadder` /
  `pickedRoute` / `permittedModels`, `NodeConfigEditor::axisMenu`, `pipeline-nodes.ts` test
  `reasoning_effort`, `provider`, `model`, `response_format`, `schema_family`. Action: the param schema
  serves each row's role. **Re-test:** `git grep -nE '\.key (===|!==) "(reasoning_effort|provider|model|response_format|schema_family)"' -- webapp/lib webapp/components`.
- **Editability is decided client-side**: `SteerForkPanel::canBabysit` tests
  `me.capabilities.includes("campaign.babysit")`; `permittedModels` re-walks what `configRows` reads;
  `NodeConfigEditor`'s `schema` is a source prop two hosts fill from different reads. Action: served
  per-row editability for the account; one schema source.
  **Re-test:** `git grep -nE "campaign.babysit|permittedModels\(" -- webapp`.
- **`JobsDock::passMark` / `passLabel` derive running/starting from `started_at === null`** (so does
  `AccountUsageTab`). Action: a served pass state. **Re-test:** `git grep -n "started_at === null" -- webapp/components`.
- Hand-built controls beside the primitives: `Criterion::DialRow` commits on pointerup/keyup/blur beside
  `CommitInput`; raw `<select>` in several surfaces with no `ui/` select. Low weight.
  **Re-test:** `git grep -nE "<select" -- webapp/components`.

**G. Dead and ratchets.**
- **Teaching prose in `title=` across many surfaces** beyond § The candidates surface's list. Action: a
  gate ratchet. **Re-test:** `git grep -cE 'title=("[^"]{60,}|\{\`[^\`]{60,})' -- webapp/components`.
- Minor: `CandidateRow` re-derives "retired" after `splitRetired`; `reads.ts::filesRead` builds the
  cycle URL beside `cyclePathUrl`; `useActivePointer` uses `|| null` on ids.

### What a cycle writes to disk (storage survey 2026-11-01)

- **A `store/data_root.py::CAMPAIGN_ROWS` row that nothing writes is never detected**: the storage
  survey reports files found, so a dead row stays declared forever (batch 9 closed the other half,
  an unrowed file, as the `unclassified` leaf). Action: a test walks a finished offline-run campaign
  and asserts every row's glob matches a file or names why it may be absent. **Re-test:** the test
  exists and fails when a row's glob is misspelled.

- **An archived answer's outputs are read off today's backend**: `scoring/measurement_log.py::open_cell`
  reads the live declaration to build `outputs_of`, because an `ArchiveEntry` records only
  `node_configs` and `PipelineData.wire()` is flat, with no per-node attribution. Action: a per-node
  output-key map filed with the answer; existing rows name no campaign to join, so a backfill reads
  today's declaration per dataset hash. Operator decides. **Rides with:** the next archive reshape.
  **Re-test:** `git grep -n "dataset_pipeline(" -- promptpotter/application/scoring/measurement_log.py`.

### On the box (2026-11-01)

- One email holds two accounts on the box: the claimed host (`identity/default_claimed.json`) and the
  Google sign-in that owns every webapp campaign. The host's coupon exemption and the CLI's identity
  read the first, the webapp the second (`default_claim.py::_is_host` compares a user id, never the
  declared `HOST_ADMIN_EMAIL`).
- No verb sets an account's coupon or starts it afresh: `grant.json` is hand-written, and a reset is
  moving `charges.jsonl` aside, which also drops the account's bill. A grant that counts charges from
  its own `granted_at` would make a re-grant the reset; today `wallet_store.py::CouponGrant.granted_at`
  is written and never read.
- **The box holds the app's environment twice**: the services load `/etc/promptpotter.env`
  (`ENV_FILE`, root `0600`), and the login user's CLI reads the checkout's `.env` through
  `config/paths.py::env_file_path`. The two drift, so a CLI verb on the box reads other keys than the
  app it inspects, and a key pasted into the checkout file reaches no service. One file must serve
  both. Either the CLI reads
  `ENV_FILE`, which needs read access for the login user, or the services read the checkout file,
  which SELinux denies under `$HOME`. That choice is the operator's.
- **A landing proves an install-shadowed manifest by its shape, not its reader**: `data_root.py`'s
  "optimizer manifest" and "check-in manifest" rows type the YAML (call keys included), but a knob or
  roster fault in an optimizer shadow, or a parse fault in the check-in (`optimizer_manifest.py::_parse`),
  passes `land-data --dry-run` and stops only the API boot (`wiring.py::complete_registries`), after the
  data root has landed: `update.sh` then reports the services down and rolls nothing back. The readers
  live in `application/`, which `infrastructure/store/` cannot import. Action: the landing proves each
  shadow present through its own reader, refused with its path as a survivor that does not load.
  **Re-test:** a shadow `checkin/pipeline.yaml` with `optimizer.param_keys: 5` under `land-data --dry-run`.
- **The first visits digest told a window nothing recorded**: `app_visits.py::due_digest` reads the last
  24 h whenever no digest is kept for today, so the deploy that began recording sent "last 24 h … Nobody"
  at 22:53. The window must start where recording did, which no record holds yet.
- **Left by the payer rule (2026-11-02)**:
  - `quota.py::sends_on` raises `NoApiKeyError` for a provider no key pays, so `change-run-limits`
    (`clamp_budget_change`) refuses a limit over a missing key. Action: `sends_on` is total (a no-key
    send's payer is `None`); `launch_refusal` refuses `NO_KEY`, and `coupon.py::keyed_client` is the one
    raise at send (today `provider_send` spells it again). **Re-test:** `git grep -n "NoApiKeyError(" -- promptpotter/application/jobs`.
  - **An in-process connector whose cells send through `keyed_client` declares a fixed payer**: the L4
    `promptpotter` connector keeps `Connector.cell_payer = "host"`, so an own-key L4 run is refused on a
    used-up allowance. Action: an in-process connector's cells are per-provider sends (the target
    nodes' providers, read through `pipeline_resolve.py::served_pipeline`), read by `sends_on`, the
    cell reservation and `application/free_tier.py::free_fence`. **Rides with:** the in-process `llm_only` connector
    (uploads that run without a backend, one node implementation shared with TermNorm's
    `_step_llm_only`), which needs the same shape and is what lets a BYOK account run an upload.

### One record shape for every scoring pass (2026-11-01, two independent reads agree)

**Rule: the ledger writes a pass the same way whatever verb sent it**; the verb and the row set are
fields. The per-cell records already hold it (`SampleScoredRecord` + `role`, for `RECORDED_ROLES`). The
records ABOUT a pass do not: origin, measure (`MeasureEnterView`), backfill (`RaceCatchUpRecord`),
overlap (`RoundCells.overlap`), repair (`RoundRestatedRecord`), verify (`VerifyEnterView`,
`VerifyGradedView`) and bench (`BenchEnterView`, `BenchGradedView`, `BenchScoredView`) each have their
own family, and `noise-floor` and `seed-screen` write nothing to the ledger.

**First arc: verify and the bench grade**, one act (an individual scored outside a round) built twice.
- Essential and kept: the row population (REPORT vs BENCH scope, never paired, `SCOPE_ROLES`), the
  subject (a candidate at a cycle vs the line), the comparator (fresh vs recorded vs origin-paired with
  `LiftCost`), the fold scope (own ledger vs the line), the bench's reserve and `reads_before`.
- Accidental and merged: `VerifyPass`/`BenchPass` (same facts, other names), the views, the phase, the
  fold (`ledger_scan.py::_Verify` vs `_Bench`), the renderers, the stop banking (only the bench banks a
  `PassStop`; a failed verify is inferred from a bare exit), the served can-run answer (only
  `BenchStatus.can_grade`), where `paid_pass` is taken, the spend bucket and source stamp, where the
  reading is joined, the chat bracket, the embedded entry (bench only).
- A skipped automatic verify (§ URGENT) lands as a refusal banked on the one pass.
- Target: one `VerificationPass` keyed by `VerificationKind`, one enter view and one graded view, one
  phase, one fold keyed `(kind, arm.key)`, a served can-run status for both. **Decided (U4): one verb
  with a row choice** — the comparator and subject differ, so the two readings stay.
- Names: `verify` reads as a correctness check; `bench` collides with `application/bench/` (the
  optimizer runtime); `bench` / `grade-bench` / "Grade on bench" disagree. "held-out" is the clear word.
- Ledger shape changes, so run data is wiped; archive and survivor rows untouched.

### Typed state — what the 2026-10-30 arc left (served words → enums + one exported table)

- **`domain/cycle_index.py::RunStatus.of` builds `mark: str` and asserts it a `StatusMark`**, so the
  literal holds at runtime only. Action: `StatusMark` typed off `RunPhase` and `StopOutcome`, whose
  values it repeats (a re-key of the exported union, so not one edit). **Re-test:** `git grep -n "mark: str" -- promptpotter/domain/cycle_index.py`.
- **A landing waits on every live producer on another commit, whether or not the ledger shape
  moved** (`jobs/registry.py::JobRegistry.close_for_landing`), so a busy box may never land. Action:
  key it on a digest of the `CycleRecord` schema the code writes, not the commit. **Re-test:** a
  landing that moves no record shape refuses while a run is live.
- **Run files read as dicts outside `store/` have no shape row**: `data_root.py::KINDS` proves
  `rounds/round_*.json`, the `diagnostics/` sidecar, `rates.json` (`llm/pricing.py`), the Langfuse
  id map `langfuse/state.json` (`tracing/langfuse_sink.py`) and both model-capability files (`llm/capabilities.py`) by
  presence alone, because their readers walk `dict`s. A reshape there passes `land-data` and is read
  around. Action: each reader takes its model through `store/read_model.py::read_file`, its row that
  model's `TypeAdapter`. **Rides with:** § The engine swept by shape D for the three `llm/` and
  `tracing/` readers.
  **Re-test:** `git grep -n "Fate.RUN),$" -- promptpotter/infrastructure/store/data_root.py`.
- **`cycle_addressing.py::ActiveSessionResponse` serves two nullable ids for `CycleHop | None`**, so
  the webapp re-tests a pair. Action: one nullable `hop`. **Rides with:** the next webapp pass.
  **Re-test:** `git grep -n "class ActiveSessionResponse" -A4 -- promptpotter`.
- **A ledger subscriber that raises after `Admission._write` lands a charge leaves it in `_unlanded`**
  (`llm/spend_book.py`), so the book counts that part twice until the run ends — conservative, never
  under. Action: the record lands before any subscriber runs. **Re-test:** the outlives journey in
  `tests/test_spend.py` with a raising projection.
- **Writers with no reader**: `identity/blocklist_audit.jsonl` and `grants_audit.jsonl`. Action: decide whether each earns a
  reader or goes. **Re-test:** `git grep -n "blocklist_audit" -- promptpotter`.
- **One act on an arm writes two records**: `runner/arm_gate.py` and `scoring/query_loop.py` append
  an `InterventionRecord` and a `LeftComparisonRecord` for one act. Action: one act record both
  reads derive from. **Re-test:** `git grep -n "LeftComparisonRecord(" -- promptpotter`.
- **A deleted fork takes its `LeftComparisonRecord` with it**, so its arm reads `controlled` again
  while the fork's spend stays banked. Action: the exit outlives the fork. **Re-test:** delete a
  fork off an arm, then read the arm's `evidence`.

### The member backbone

- **The dev loop still serializes on Vitest:** its pool fails a worker that takes past its hard-coded 90 s to start (`WORKER_START_TIMEOUT`),
  which a host loaded from outside the gate lock (a builder's bare `pytest` or `vitest`) still trips,
  though the gate runs it `alone` on one unisolated worker per project. **Re-test:** five builders
  plus one full gate with no reap and no gate waiting longer than its own run.
- **The example plugin's `resolved_schemas.json` is tracked, and no gate run writes it**:
  `scripts/build_optimizer_schemas.py` reads `optimizers.MANIFESTS`, which the plugin joins only in
  the `optimizer-plugin` check's scratch venv. **Re-test:** edit it by hand; a gate is red.
- **`run_records.py::RoundEnteredRecord.rewound` is a flag keeping two acts apart** — entering a round
  and displacing what stood past it — that `ledger_scan.py::_Standing.feed` splits again. Action:
  two records, or one whose displacement is its own field. **Rides with:** M11.
  **Re-test:** `git grep -n "rewound" -- promptpotter/domain/run_records.py`.

### The origin gate (batch-2 reviews, 2026-11-05)

**Rides with:** the next pass through `bench/resume_and_fork/repair.py` or `runner/origin_gate.py`.

- **A fork cut at round 0 keeps its origin's holes**: `bench/cycle.py::Cycle.reopen_origin` leaves
  round 0 with no close, so `resume_and_fork/repair.py::rewalk_lost_origin` returns at once and
  `round.py::emit_origin_round` closes it on the dead cells; under the strict gate the fork halts
  again with the backend fixed (one extra launch). Action: what re-walks an inherited origin is
  whether its rows were measured by another launch, not whether round 0 closed. **Re-test:**
  `--fork-on-divergence` at round 0 of a gate-halted campaign, backend fixed: the fork passes on
  health in one launch.
- **The first launch pays a still-refusing backend twice**: `application/origin.py::establish_campaign_origin`
  re-scores after a transient abort, sending each refused cell again (3 cells, 10 attempts each in
  the offline `gated/` leg). Action: one walk per launch, as a resume already has. **Re-test:** the
  `gated/` leg's first launch sends each cell once.
- **The bench pass runs on a launch the origin gate halts**, sending its cells to a dead backend
  (10 in `gated/`). Action: the gate decides before the bench spends. **Re-test:** the `gated/`
  leg's halted launches send no bench cell.
- **`capo/members.py::CapoInit.refuse_origin` reads a schema fact ("no prompt node") off answered
  rows**, so it needs a guard for an origin nothing answered. Action: refuse on the pipeline
  schema at selection. **Re-test:** `git grep -n "refuse_origin" -- promptpotter/application/optimizers/capo/members.py`.
- **`tests/test_archive.py::test_a_resume_takes_the_origin_the_gate_halted_past_it_and_every_later_launch_reads_the_pass`
  walks zero cells**: `run_round_loop(cycle, [], …)` re-walks the dead origin over no dataset and
  closes an empty round 0, so the halt and override pass on an empty origin, not a holed one.
  Action: hand it the origin's cells through the `offline` fixture. **Re-test:** the test's dataset
  is non-empty.

### Words and the sample tag (batch-2 reviews, 2026-11-05)

- **`config/settings.py::_git_head` answers `UNVERSIONED` silently** for a ref in neither the loose
  refs nor `packed-refs` (a reftable repo, corrupt refs). Action: the landing's refusal names the
  checkout it could not version. **Re-test:** a landing from a reftable clone names why.

### Batch E's census (2026-11-05)

What batch E's reviews found outside the arc under review, grouped by shape.

**A. A file read past its store's one reader.** **Rides with:** READS.
- `tenant_dataset_store.py::banks_sharing` fails the whole survey on one bad entry. The survey catches
  in `llm/heartbeat.py`, `campaign_store/store.py::CampaignStore.list_campaigns` and
  `user_store.py::read_accounts` are held by no test. Action: a survey serves a bad row with its fault,
  and a test holds each catch. **Re-test:** a tenant tree with one unreadable dataset lists the rest.
- `llm/pricing.py::CACHE_PATH` binds `rates.json` to the data root at import, while every other reader
  resolves the root per call: a home set after the import reads and writes the first root's rates
  (batch 6's review). Action: the path resolves per call, through the store's reader. **Re-test:**
  `git grep -n "^CACHE_PATH = user_data_root()" -- promptpotter/infrastructure/llm/pricing.py`.

**B. A refusal spelled per door.** **Rides with:** REFUSE.
- `provider_config.py::OIDCConfigError`, `prompt_block.py::PackRefusal` and
  `shared/errors.py::LedgerUnreadableError` stand beside the one refusal type. A taken slug is refused
  twice, as `LaunchError` and as `shared/errors.py::SlugTakenError`.
  `injections/registry.py::validate_template` raises `KeyError`. Action: one type, its kind a row.
  **Re-test:** `git grep -nE "class (OIDCConfigError|PackRefusal)\b" -- promptpotter`.
- The CLI rewrites refusals: `parsers.py::_budget_axis` and `verbs.py::_rescore_count_arg` word their
  own, and `cli/commands/new.py`, `ab.py` and `resume_command.py` print `ERROR:` beside `Error:`.
  Action: the CLI prints the refusal's served sentence. **Re-test:**
  `git grep -n '"ERROR:' -- promptpotter/presentation/cli`.

**C. One address resolved twice.** **Rides with:** M2.
- `cycle_addressing.py::resolve_campaign_id` is a second resolver over `resolve_subject`'s matcher,
  read by `cli/commands/ab.py` and `noise_floor.py` off the dispatcher. The CLI's cycle verbs resolve
  CLI-side. The matcher falls back to a substring, and the dispatcher walks `descend` twice.
  `run_records.py::BankedSubject` is a second address. Action: one resolution, at the dispatcher, by
  exact id or prefix. **Re-test:** `git grep -n "resolve_campaign_id(" -- promptpotter`.
- `evidence/read.py::campaigns_on_dataset` matches the name label, so two banks under one name (a
  re-upload, a name two tiers hold) pool into one read: a served-answer change, not a cleanup.
  Action: the name resolved once and matched on `Campaign.declaration.dataset_hash`. **Re-test:**
  `git grep -n "c.dataset_name == dataset_name" -- promptpotter/application/evidence`.

**D. Spelled by hand beside the rows.** **Rides with:** the next read added.
- `api/deps.py::read_input`'s pattern, `cli/parsers.py`' read help,
  `complexity_ledger.py::_TERMINAL_VIEW`, and the 29 hand-written URLs of `webapp/lib/api/reads.ts`
  each restate `ReadKind`'s rows. The webapp re-asks reads by an untyped name (`invalidateReads("…")`).
  Action: each derives from the rows, and a read's name is a type. **Re-test:**
  `git grep -c '\`${API}/' -- webapp/lib/api/reads.ts`.

**E. Webapp read state.** **Rides with:** FEED.
- A `/me` failure is reported twice: by the cache's failure and by the auth catch, which treats a 401
  as an incident, and a 401 rests. `read-cache.ts::dropCachedRead` on a mounted read clears it with
  no re-ask. `api/reads.ts::couponRead` has no validator, and evidence computes its ETag after the
  build. `channel-hooks.ts::usePickedPoint` reads null mid-scoring. `ChannelDetail.tsx` shows its
  pending copy on a failed read. `read-cache.ts`'s `syncBeat` is latent. The round in flight is spelled
  in `round-candidates.ts`, `search-point.ts` (three sites) and `round-reading.ts`. Callers' cadences
  and `poll.tsx::useDashboard`'s viewed check are held by no test. Action: Q2 deletes the cadences.
  The rest get one owner each. **Re-test:** `git grep -n "dropCachedRead(" -- webapp/lib webapp/components`.

**F. The check-in draft, from EN's review.** **Rides with:** M6 (the Start), M5 (the running set), FEED
(the read).
- `mint_and_start.py::commit_upload_definition` re-derives the upload's two halves
  (`DraftPipeline.local`, `pipeline_resolve.py::draft_declared`) that `pipeline_resolve.py` already split.
  Action: `DraftPipeline` carries the campaign half beside `local`. **Re-test:**
  `git grep -n "draft_declared" -- promptpotter/application/jobs`.
- `cli/commands/new.py`'s `--set` hand-maps its names onto `EditDraftPatch.config` paths
  (`_SET_SCORING`, `_PATCH_KEY_OF`); `--set scoring.*` is held by no test and `verbs.py`' help omits it.
  Action: `--set` takes the patch's dotted path, parsed over its schema. **Re-test:**
  `git grep -nE "_SET_SCORING|_PATCH_KEY_OF" -- promptpotter`.
- A scoring edit takes `matcher` and `dials`, which the compiler's block refuses, converted by
  `draft_patch.py::_folded_scoring` and `draft_campaign.py::draft_criterion`. Action: the compiler owns
  the pick's spelling, or the pick leaves `config`. **Re-test:**
  `git grep -n '"matcher"' -- promptpotter/application/datasets`.
- `PipelineSetupSection.tsx` derives Mode's exclusions client-side (`active_steps` plus
  `config.exclude_nodes`): an adapter holding the running-set rule, held by no test. Action: RS serves
  each mode's exclusions. **Re-test:** `git grep -n "exclude_nodes" -- webapp/components/ingest`.
- `ReadResult` literals are minted outside `useRead`: a draft's pipeline in
  `useConnector.ts::useConnector`, `useNestedPipelines.ts` and `OptimizerSetupSection.tsx`. Action:
  one constructor beside `useRead`, or `PipelineFlow` takes the data and its gap. **Re-test:**
  `git grep -n 'status: "ready", data' -- webapp/lib/hooks webapp/components`.

**G. Singles.**
- A per-cell criterion naming a term no cell carries compiles (`authored.py::scorer_of`) and refuses
  per cell at the run (`compiler.py`'s `ScoringTermMissingError`), though the draft holds its backend's
  terms (`compiler.py::campaign_terms`). Action: the check-in compiles it against them. **Rides
  with:** M6 (refused before admission). **Re-test:** a
  scoring edit `correct - no_such_term` is refused.
- `domain/scoring.py::turn_scalars` overwrites `n_turns` with the count of a step named `n`. Action:
  the per-step terms take a namespace the totals cannot meet. **Re-test:** a turn list with a step
  `n` keeps its total.
- `tests/test_scoring.py` writes `HostSession._tasks` past the interface. Action: Q1's log read
  replaces it. **Re-test:** `git grep -n "_tasks\[" -- tests`.
- `cycle_files.py::read_cycle_file` at campaign scope serves any shown file under the campaign root
  (another cycle's, a sandbox's), while `list_cycle_files` lists only `CampaignLayout.files()` (batch
  7's fix pass). Action: the campaign scope reads what it lists. **Re-test:** a campaign-scope read of
  `cycles/<id>/readout.log` is refused.
- `domain/phases.py::RunPhaseInfo.parked` is read only for a phase with an action
  (`phases.py::RunAdmission`'s refusal, `run-phase.ts`' PAUSED): QUEUED's, STARTING's and GATE's words
  are served and read by nothing (censuses 3 and 4). Action: the word lives only where it is read.
  **Re-test:** every non-empty `parked` row has a reader.

## Open — multi-arc

- **The crowning bar tests nothing a result can quote.** `application/scoring/selection.py::elect_round_winner`
  admits with no interval and no multiplicity correction, and `RoundResult.improved` reads any selection
  past round 0 as a promotion; at panel width `exact_p_floor(6) = 0.031`, Holm across three arms 0.094, so
  no corrected exact verdict reaches α=0.05. Call any "this round improved" provisional. Action: re-read
  separability on a run that used the tenant ruler, then choose a bar — never tighten one before. **Rides
  with:** [`../../paper/README.md`](../../paper/README.md) § The sequence, stage 3. **Re-test:** a
  tenant-ruler run's `index.json::final.rounds_to_separable` beside its `rounds_to_improved`.
- **The free tier offers what it refuses.** `application/free_tier.py::with_free_tier_layer` narrows each
  model's params but never the model axis, so a free check-in offers models Start refuses. Action: the
  per-value lists name only values known to fail; the spend ceiling bounds the rest. **Rides with:**
  the next check-in pass. **Re-test:** a free account's model menu equals
  `free_tier.yaml::task_models`.
- **NEXT — the default host pin is a trial with no verdict.** `domain/pipeline_overlay.py::pin_default_routes`
  writes each model's default host (`infrastructure/llm/registry.py::_MODEL_PROFILES`) into every
  node config; `route_order: []` opts out. **Decided:** a model keeps its pin only where its realized
  cache share and effective $/M beat running uncached. **Rides with:** the next supervised campaign on
  a pinned model, beside its `route_order: []` twin. **Re-test:** `SpendRollup.by_node` in each twin's `review.md`.
- **The responsive walk takes no Lighthouse score.** Action: one Lighthouse run at 375 px, recorded
  here. **Rides with:** any layout change with a browser open. **Re-test:** a score recorded here.
- **Compare cards are laid out for few wide columns**, not five thin stripes on a 16:9 display
  (`components/compare/ChannelCard.tsx`). **Rides with:** the next compare-pane pass, through `/design`.
  **Re-test:** five candidates at 1920×1080 without horizontal scroll.
- **Optimizer-model repair rate on L2/L3 structured output is unmeasured.** **Rides with:** the next
  supervised campaign that escalates. **Re-test:** the share of its L2/L3 calls that needed a parse repair.
- **`tests/test_scoring.py::_peer_cycle` stands a `Cycle` up by hand**, banking a C0 report
  `application/origin.py::establish_campaign_origin` cannot produce. Action: a backend whose answers
  produce the asserted origin, started through run init. **Rides with:** any change to how run init
  scores the origin. **Re-test:** `grep -n "_peer_cycle" tests/test_scoring.py`.
- **The never / partly / always spread contradicts the headline beside it.**
  `application/scoring/measurement_log.py::_cycle_cells` pools every candidate the cycle ran, so
  `domain/cells.py::hit_spread` reads a sample only C0 saw as "never". One decision: the spread
  describes the subject (the selected searchpoint's own cells), or stays a search-coverage statistic
  labelled as such, with samples only weak candidates measured counted as *unchallenged*. **Rides
  with:** any change to `hit_spread` or the run card. **Re-test:** ask the operator which.
- **A scoring cell beats as an LLM call.** `application/scoring/sample_measurement.py::_send_cell`
  and `search_point_scorer.py::_claim_cell` append `LLMCallProgressRecord`s named
  `node="backend_scoring"` (no view has it) with a `call_id` of `scoring:{position}`, so the
  dashboard fold cannot key a beat's `inner` hop to its open cell: with look-ahead above 1,
  `CurrentRound.inner` names the latest-beating cell. Action: a cell beat carries its `position`
  and no node. **Rides with:** the next change to the heartbeat record. **Re-test:**
  `git grep -n '"backend_scoring"' promptpotter`.
- **`domain/export.py` carries no optimization spend or tokens**, so a reader cannot compute amortized lifetime cost ([`../research/external-constraints.md`](../research/external-constraints.md) § Cost reporting — binds every run from now, not only the preprint). **Rides with:** the next export shape change. **Re-test:** `grep -n "spend\|token" promptpotter/domain/export.py`.

## Open — optimizer tuning

**Rides with:** "The optimizer climbs" in [`roadmap.md`](roadmap.md) § Sequence.

- **PoBB's lock-in reads the election's bar after every cell (optional stopping); the switch is off by
  default** (`pobb` `lock_in`, [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md)).
  The round order puts the parent's misses first (`build_round_order`, "win-opportunities first"), so
  a candidate that fixes them reads `P(c>p)=93.8%` after a few cells and `pobb` locks it in at
  `theta_election.admission: 0.85`. On tenant `inbox-requires-action-v6` round 1 (parent 92.5% of 40),
  C1.2 locked at q6 and C1.4 at q13, both 100%; the election refused both (`θ lift +0.400 logits, se
  1.165`, `collapsed_band`), and the round's cells went to the three losers, so the round held at the
  price of a full round. On stream (`banking77-n8n__7b4528` round 2, 2026-11-06), `leader locked q4/20
  p_best=93.8% >= admission=85%`: the election selected that arm at 100% of 4 cells, and its verify
  read 1.000 → 0.600 on 40 unseen cells. The operator asked for one bar, not two: the lock-in reads P(best) on
  the cells drawn so far (front-loaded with the parent's losses), the election reads θ with its se over
  the whole ruler. Action: the lock-in asks the election's own question (would `theta_election` admit
  this arm now?), or a locked leader keeps buying cells until it does or the round's budget ends; a lock
  at q6 against a 92.5% parent needs at least the cells that could show one parent-level error.
  **Rides with:** M19 ([`roadmap.md`](roadmap.md) § The right winner, its anytime-valid bound). **Re-test:**
  with `lock_in` on, rerun round 1 of `inbox-requires-action-v6__e1bda1`; an arm that is locked in is
  either elected or not locked.
- **A run reads every campaign's ledger, so one stale campaign crashes another**: `crm-extract-names__29f63f`
  crashed entering round 1 on `LedgerUnreadableError` for `inbox-requires-action-v6__e1bda1`'s ledger. The bill
  no longer reads a ledger and `land-data` refuses a tree holding one that does not load, but a desk tree is
  never landed, and `intelligence/earned_blocks.py` and `initialization/loop_start.py::_banked_instruments`
  still read every campaign's ledger after origin scoring is paid. Action: those readers refuse at mint,
  before origin. **Re-test:** with one pre-shape-change campaign on a desk tree, `new` refuses before spending.
- **A re-read arm has two names**: `runner/measurement.py::ReferencePairs.of_round` names an individual
  by the slot an EARLIER round walked it in, so a population member walked again (CAPO's
  `population_rejoin`, potter's held leader) reads `C2.1` on its pair and `C3.1` on the round card. **Rides with:** any change to
  `arm_name`. **Re-test:** a held leader's round-3 pair names its round-3 slot.
- **An operator's authored arm is refused a field they held**: `bench/children.py::carried_writes`
  refuses every child a `NodeSearchNarrowing.prompt_fields_held` field, the operator's own arm with
  them. Open: may that arm write it? **Rides with:** any change to `carried_writes`.
- **A declared bench is not the held-out count**: `CampaignConfig.dataset_split` `{bench: 100}` held out
  162 rows on the box's `hotpotqa-closedbook`. No shipped recipe adds it a `held_out` tail and the
  loader dedups rows, so the 62 are in the box's tenant definition; the one code path holding out past
  `bench` is `domain/bench.py::partition_bank` holding declared `held_out` rows beside the ranked
  ones, which L4 inner cells (`bench: 0`) and `justlogic/d234-held` are built on. Open: read the
  definition (`/potter-box`), then decide whether `bench` becomes the total. **Re-test:** a fresh
  `{bench: 100}` campaign on that definition holds out 100.
- **`noise-floor` lets its pause escape as `PassStoppedError`**, a band over fewer than `k` rescores
  being no band; the pass's address is Q1's (§ The quantum-leap pass). **Rides with:** any change to
  `paid_pass`. **Re-test:** stop a `noise-floor` from a second terminal; it banks no band.
- **A locked-in arm's catch-up cells are read by its race, never its round**: `potter/race.py`
  measures a prior on each later challenger's cells (`RaceCatchUpRecord`), but
  `runner/measurement.py::_walk_population` folds only the arm's own walk into `rows[key]`, so the
  election and scoreboard read the leader on the cells it stopped at. capo-pobb (CAPO ranks on shared
  cells) elects an arm PoBB eliminated. Action: a caught-up cell is a row of the prior's round reading.
  **Rides with:** any change to `CatchUpPool`. **Re-test:** a locked-in leader's round reading counts
  its caught-up cells.
- **A lost mirror loses its money**: `infrastructure/llm/spend_book.py::SpendBook.mirror` logs a failed
  append, yet the nested record already says `mirrored=True`, so no bill or bank counts that call. Action: a
  failed carry raises, or the nested record is written only after the carry lands. **Re-test:** a root
  append that raises leaves the call counted on the account.
- **A fork cannot narrow a dead param axis**: `ForkProposal.unlock_schema_field_rename` is the sole
  config a rebase applies (`stand_on` re-elects, it changes no config), so L3's measured ask to drop
  an axis value no arm gained on has no channel. Action: decide whether a narrowed
  `param_allowed_values` is fork-only, and if so name it as a `ForkProposal` member mapped to one
  `ConfigOverrides` edit. **Re-test:** `git grep -n "class ForkProposal" -A20 -- promptpotter`.
- **The fastest hosts refuse the `l1_generate` schema**: Cerebras and Groq (`openai/gpt-oss-*`) answer 400 to
  `L1GenerateOutput`'s inlined JSON schema — Cerebras wants `properties` on every object, Groq strict mode wants
  every property `required`. Every object on the wire comes closed and spelling its `properties` from
  the response models themselves (`dispatch/l1_wire_schema.py::build_l1_response_schema`); every optional property is still optional, since the wire sends `strict: False`
  (`infrastructure/llm/openai_compat.py`) and an all-required, nullable schema grows each request and reply.
  Action: make the wire all-required only together with sending `strict: True`. **Re-test:** one `l1_generate`
  call pinned to Cerebras parses.

## Open — from the head-to-head arc

A leading **INVESTIGATE** means nobody has shown the item is real; answer that first.

- **An arm is planned on a replicate it does not have yet**: `command_kinds.py::HeadToHeadRequest.purpose`
  declares replicate 1, and `jobs/mint.py::_numbered` overwrites it under the record hold. The number
  keys on `plan.treatment`, which `resolve_cycle_plan` builds from a whole `RunDeclaration` that already
  holds `purpose`, so the plan cannot be built without a number. The placeholder also reaches the root id
  (`measured_address`), so admission prices plans whose ids are not final. Holding the record across
  planning is no fix: the hold is try-once, and the arms mint after the dispatcher's ack. Action: a
  purpose-free run spec feeds `resolve_cycle_plan`, `effective_config` and `shared_part`; the plan carries
  `Optimize | ArmSeat(head_to_head_id, arm_key)`, and the mint builds `Compare(Arm(…, replicate))` once.
  `declare` rewrites `optimizer_narrowing` and `bench_trigger` on `isinstance(purpose, Compare)`, and that
  moves with it. **Rides with:** M6 (plan before admission). **Re-test:**
  `git grep -n "replicate=1" -- promptpotter`.

- **`dashboard.json::run_limits`'s money axes still go stale on `set-limits --max-usd`**: the
  `CampaignCeilingRecord` sits on the root cycle's ledger, which no other cycle's fold reads, so
  `campaign_store/store.py::CampaignStore.armed_run_limits` lays it at the served read only. One budget rule is spelled in
  three places: the fold, `jobs/quota.py::declare_run_budget` and `infrastructure/llm/spend_book.py`.
  **Re-test:** after `set-limits --max-usd X` on a fork, `dashboard.json::run_limits.budget.usd == X`.
- **A reserved cell's bill is on no ledger** (`Connector.holds_own_sends` writes no hold), so
  `infrastructure/llm/spend_book.py::unreported_on` can re-teach a book its sends but never the cell.
  Action: a hold record per reserved cell. **Rides with:** any change to `spend_book.py::reserved`.
  **Re-test:** `grep -n "emit_spend_hold" promptpotter/infrastructure/llm/spend_book.py`.
- **INVESTIGATE — does an errored cell bias a head-to-head?** `gpt-oss-20b:nitro` can end
  `finish_reason=error` then pass one schema repair; unknown whether the row banks the answer or a
  miss. **Rides with:** the next head-to-head read. **Re-test:** per arm, the hit rate of error rows
  against the arm's other rows.
- **INVESTIGATE — CAPO's survivors are ranked on another reading than the reference ranks them on.**
  `population` keeps μ best-first by mean objective on the cells all survivors measured; promptolution's
  CAPO sorts survivors on raw score over every cell each has been evaluated on, cached across rounds,
  so an incumbent's deeper blocks from earlier races count. After a one-block race (the paper's rule,
  `candidate-elimination.md` § CAPO's race) the two can crown differently. Open: which reading the
  bench compares. **Rides with:** any change to `capo/members.py::_rank_survivors`. **Re-test:** on a CAPO
  run with offspring < μ, rank one round's survivors both ways.
- **"Calls held equal" has no axis to hold.** `domain/spend.py::Budget` meters usd, tokens, rounds and
  wall clock; the roadmap and `external-constraints.md` § 4 hold calls equal, `paper/README.md` holds
  spent tokens, and `paper/plans/` holds USD under the arm's `search_incurred` meter, so two arms of one
  point make the same calls only at one cell price and one optimizer cost. Action: one matched axis,
  decided; if calls, a cells axis on `Budget` the search meter binds. **Rides with:** any change to
  `Budget`'s axes. **Re-test:** `price-head-to-head paper/plans/comparability.yaml` — do one point's
  `search_cells` differ by arm?
- **Structured optimizer calls send the clamp's seed raw** (`bench/llm_call.py::_merged_config`)
  while `Calls.ask` draws one per call: two seed rules, so unpinned structured calls with one prompt
  replay across campaigns. Action: one seed rule, read off `domain/campaign.py::run_clamp`.
- **The $0 price opens a backend session** (`application/head_to_head_price.py::price_head_to_head`
  through `jobs/launcher/head_to_head.py::dataset_arms`) only to resolve the node models and the
  partition, so a stopped backend refuses a read that makes no call. **Rides with:** any change to
  `dataset_arms`. **Re-test:** price a plan with the backend down.
- **An optimizer's round price is its name's, not its treatment's**
  (`application/head_to_head_price.py::optimizer_round_price`): the three `escalation_ladder` arms read one potter
  price, though an `l1` round sends fewer optimizer calls than a `full` one. Action: key it on the
  treatment once enough campaigns of each have run. **Re-test:** the ablation's optimizer line names
  one price for three ladders.
- **INVESTIGATE — a CAPO arm is admitted on its round CEILING** (`preflight.py::check_cap_funds_round`
  over `round_cells_ceiling`: 4,230 cells on 310 search rows), which its race rarely spends, so a
  budget point must fund the worst case to mint CAPO at all. **Re-test:** a finished CAPO round's
  cells against its ceiling.
- **A selection is still made beside the one a read already holds.** `checkin.py::_land_draft`
  refuses each overlay before landing, then `resolve_cycle_plan` selects and refuses again;
  `config_map.py::config_map` selects off the campaign's cycle config for itself, beside
  `pipeline_resolve.py::_Merge`'s. Action: a check-in plans its cycles off the draft before landing,
  and a campaign's cycle config carries its selection. **Rides with:** M6's `RunStart`.
  **Re-test:** `git grep -n "select_optimizer(" -- promptpotter/application/config_map.py`.
- **A moved capability or backend revision alone passes a resume.** It is named as a drift cause only
  when the origin hash also mismatches (`jobs/mint.py::resolve_resume` → `ConfigDriftError`).
  **Fork:** refusing contradicts "a drift is served, never refused" (`RunWiringRecord.backend_revision`);
  serving needs a `RunWiringRecord` field like `defaults_drift` at every entry point.
- **`round_cells` has two meanings for one draw:** potter's unclamped value, read by L4, verify and the
  overlap, and the backbone's ceiling panel, clamped to the search split. Also, `RoundPlan.walk` and
  `round_walks` each decide which walk counts as a round of its own.
- **The capo offline bench turns on one seed**: lift +0.700 or none after a draw-order change, so the
  fixture cannot tell a mating-law change from a reshuffle.

## Open — renames the operator picked (2026-10-29, low priority)

**Rides with:** a pass touching the name, or the next run-data wipe for on-disk keys. Names are root
`CLAUDE.md`'s rule; each rename lands with its doc and `CLAUDE.md` row in one commit. **Re-test:** the old
word's `git grep -c` for that sense is 0.

- **`candidate_library` → `answer_terms`** (the target's answer-term list, beside
  `DraftFile.answer_space`), and `PoolSource.LIBRARY` → `PACKS`: `EditDraftPatch`'s three
  `candidate_library*` fields and the dataset file move, and
  `shared/hashing.py::content_hash`'s key re-keys root ids.
- **"course" → "cycle" in the served and doc layer** (`HierarchyCourse`, `level: "course"`,
  `/hierarchy/{course}`, `ids.ts::courseAddressOf`); `CycleHop` stays an element of a `CyclePath`.
- **"line" in its live-cycle sense → `live_cycle`** (`command_kinds.py::LinePayload`,
  `BenchLine.on_line`, `domain/bench.py::LineRun`); "selection line" is never bare "line". A command
  payload kind, so ledger shape.
- **`nodes.py::Panel` / served `ArmPanel` → `RoundSamples` / `ArmSamples`**, the L4 roster → `inner_panel`;
  "panel" stays the evidence section an LLM reads.
- **"slot"**: prompt slot → prompt field, layout slot → region, `domain/arms.py::ArmSlot` →
  `ArmPosition` (ledger field), bank `slot_of` → `position_of`. The layout key is optimizer-prompt text.
- **`CycleSeed*` / `ForkSeed` → `CycleStart*` / `ForkStart`**, so "seed" is the RNG seed alone. A
  ledger record.
- **"member" for an individual → "individual"** (`run_standing.py`, `origin_panel.py`); "member" is the
  optimizer node; "candidate", an individual at a round address, is owed to the Vocabulary row.
- **The served hierarchy discriminator `level` → `entry_kind`**
  (`git grep -c -w level -- webapp/components webapp/lib`).
- **The READING node → reader node** in `resolved_schemas.json`'s layout prose: prompt text, so a
  re-measure.
- **draft vs check-in, by rule, no rename**: "check-in" is the resolver-and-gates process, "draft" the
  object (`DraftFile`); owed to root `CLAUDE.md`'s Vocabulary row.

## Open — from the structure pass

**Rides with:** any change to the file named. **Re-test:** the `file::symbol` still holding it.

- **Seen in the `lib/` regroup**: `lib/hooks/useRegistry.ts` and `lib/workspace.tsx` import `derivations/hierarchy` past the barrel
  to dodge a cycle (barrel → search-point → poll → workspace); `lib/theme.ts` mixes chart setup with
  the theme preference.

- **A scored but unhealthy origin still holds a live process**: `runner/origin_gate.py::run_origin_gate`
  halts an origin with no scored cell, but holds a degraded one, and its three answers repeat run verbs
  (`abort` ≈ leave it ended, `proceed` ≈ `resume`, `rescore` ≈ `resume --from 0` force-fresh).
  Open: halt it too, deleting the hold.
- **A cold server folds every ledger before the first page answers**: the first
  `GET /hierarchy?depth=1` waits on a full `LEDGER_FOLDS` pass over every `projects/` ledger, and
  the browser shows a sidebar stuck on "loading…". On 2026-10-30, with 91 MB / 129k lines in
  `projects/`, it took 33 s cold beside the boot sweep and 1.1 s warm (`.scratch/cold_real.py`).
  One shape remains (the `run_state_stamps` ETag is Q2's,
  § The quantum-leap pass): `jobs/reaper.py::periodic_sweep` runs at boot, beside the first page. Over
  58 ledgers it folds 23 s, about 10 s of it on L4 inner-sandbox ledgers that no page reads.

  The fold itself is the root: pydantic validates each screened line on every cold start. A fold
  snapshot keyed by `file_sig` would make a restart pay only the tail.
- **Seen in the parsimony pass (2026-11-01)**:
  - **An activity row is a `Said` row under other names**: `domain/activity.py::ActivityItem` carries
    label, tone and detail, with kind glyphs (★ ◆ ↻) beside the CSS tone glyphs. One `Said` row
    reaches every constructor, `chat/LiveSegment`, `TimeRay` and the event contract. That reshapes
    the event contract (AsyncAPI) and its chat readers, so it is its own arc, not a word table.
  - **Off-screen view columns stay mounted and poll**: `shell/Views.tsx` mounts every open column, so a
    phone on the Dashboard preset runs every pane's reads at once. React's `<Activity mode="hidden">`
    per column, on one IntersectionObserver, stops them and keeps their state. The fork: a column
    swiped into is blank until its reads land.
  - **Run data past `MAX_PATH` is read only where `store/io.py` reads it**: `io.py` writes through
    `_long_path`, but `store/read_model.py::file_sig` and `store/io.py::complete_lines` / `holds_mark`
    (among many: `grep -rnE "\.stat\(\)|[^_.a-z]open\(" promptpotter`) read bare, so a ledger past 260
    characters reads as absent and a deep L4 store lists no course. This desk has long paths off. Root:
    spell the data root once in its long form (`config/paths.py`) so every derived path carries it;
    the cost is every comparison with a short-spelled path (`tmp_path`, `REPO`). The Linux box is unaffected.
  - Unverified, for the next change to each file: `shell/CriticalAlertBanner` builds `Said` rows in the
    browser and re-sorts `TONES` for `pinned`; `ViewTabs.show` is many `open`/`close` calls, not one
    workspace act; `cycle_files.list_cycle_files` walks all of `.runtime/` and filters after
    (`iter_files(skip=)` exists); `FileTree` mounts a 2 s `useActivePointer()` poll for one
    failure line; a `/hierarchy` 200 replaces every entry object `lib/hooks/useRegistry.ts::useRegistry` indexes.
- **The collapse gate cuts a hedging arm on noise**: `pobb/checks.py::check` →
  `domain/scoring.py::is_answer_collapsed` reads one repeated label over `n_min` answered cells as
  "no measurement". The panel walk runs hardest-first, so its first cells can hold one or two truth
  labels. A task model that answers `Uncertain` on about 75% of cells then trips the gate by chance in
  about 18% of arms (0.75⁶).
  - Seen 2026-10-30 in L4 inner `justlogic.d234__731eda`: C1.2 was cut at 0/6, all `Uncertain` and
    every one parsed. The round's own n_min top-up read it at 0.393 against C0's 0.5 over the 28-cell
    origin panel.
  - The bar and the sidebar lead with the 6-cell 0%; the 39% shows only as the overlap series.
  - Collapse must be judged against the truth labels the cells hold, and against the parent's answers on
    the same cells. An engine decision: prove it with an offline run.
- **A member knob's `NodeConfigParam` serves no estimand**, which the config map shows for every campaign
  knob (`application/knobs.py`), so the setup screen cannot say what moving one moves.
- **Seen building the setup screen**:
  - An edited optimizer-prompt field cannot be reset: `OptimizerNodePrompt` serves no template text and
    `campaign_config._merge_dicts` keeps a null as a value.
  - Smaller: `title=` used as teaching prose in `NodeDetail` and `OptimizerCard`;
    `CheckinLoadingWindow` keeps its own `setInterval` outside `lib/cadence`; `.setup-step` is styled in
    `chat.css`.
- **Seen in the phone-first setup pass**:
  - A changed knob value names no source: the resolver keeps none for `optimization.nodes` layers
    (dataset block, draft override, cycle seed), so the row says only "Changed from default".
  - `locus_crossover` is listed as a maker with nothing upstream making two-parent children; an
    arm-editing maker's on/off belongs on `nodes.Proposer` as a member-declared rule.
- **Seen building the prompt workspace**:
  - An override cannot be removed, only overwritten: knob Reset writes the declared value and a cleared
    prompt field writes `""`, so the draft stays "edited". The overlay merge needs a delete.
  - The packs copy each snippet's arXiv id and method into its `tags`, so Tags repeats Source and Method.
  - `ui/MenuCheck` takes a `title=` prop, which `CandidatesToolbar` fills with teaching prose.
- **Seen framing snippets in a prompt**:
  - `.prompt-snippet-input` re-declares the field skin's border and background inside the frame.
- **A tree rooted past ~140 characters fails the suite's warm-up**: `tests_owning.py::_record`'s
  `compileall` writes each `.pyc` through a `.pyc.<id>` temp, so under Windows' `LongPathsEnabled=0`
  a 180-character root fails with `FileNotFoundError`, where an import only skips its cache. A
  `.claude/worktrees/` tree (101) and a pushed tip's Temp checkout pass. Action: the desk's
  `LongPathsEnabled` is 1, the root; a gate refusing the tree would only rename the failure.
  **Held:** the operator's registry edit. **Re-test:** the gate from a 180-character root.
- **Seen building the read catalogue** (2026-11-04):
  - `GET /auth/me` writes (`account.arrive`): a read catalogued as `ReadKind.ME` that opens an account.
    Action: arrival is the sign-in callback's act alone. **Re-test:** `git grep -n "arrive(" -- promptpotter/presentation`.
    Until it ships, the `me` read itself (`read_params.py::MeParams`) reads through
    `account.py::account_read`, which writes nothing; the route alone arrives, since an open box has
    no sign-in callback to arrive at.
- **Seen building the host coupon** (2026-10-29):
  - Every connector declares its cells the box's to pay (`Connector.cell_payer`): none can send on the
    account's key, so an own-key account past its coupon is refused any run with a backend. Action: a
    connector that takes the account's key per call declares `"user"`. **Re-test:** an own-key account
    at a used-up coupon runs one TermNorm cell.
  - `api_keys.json` holds account keys in plaintext at rest, beside `user.json`. Action: encrypt under a
    box secret, or name the file in access-model's at-rest table. **Re-test:** `cat` one.
  - A resumed cycle's prior metering counts as the box's in its book's coupon reserve
    (`SpendBook.take_up` reads only unreported own-key holds), so a resumed own-key run reserves more of
    the coupon than it draws. Conservative, not a leak. **Re-test:** resume an own-key cycle and read its
    `off_coupon_usd`.
  - `TokenUsageRecord.cached` and `key_source is None` are two spellings of one fact (a replay no
    wallet paid). Action: one field. **Re-test:** `git grep -n "key_source=None" -- promptpotter`.
  - `coupon.py::keyed_client` folds the account's charges on each send the box's key pays (it reads
    the remainder fresh). Action: the run's book carries the remainder it was admitted against.
    **Re-test:** time one send on an account with 100 cycles.
  - `LifetimeSpend` carries no token count, so the coupon's token side shows left / face, never used.
    **Re-test:** the Host allowance section with a token-bound grant.
- **Seen moving harbor's agent onto our client** (2026-11-04):
  - `LLMResponse.cost_usd` omits a resend `_admitted_send` made that may have billed (the book holds it
    as unreported), so `_AgentModel.cost_usd` can read a reported sum below the book's. **Re-test:** one
    5xx then an answer, read the row's `cost_usd` against the ledger.

## Bypasses — held for one holistic pass

**A send made on our behalf re-derives what `_admitted_send` decides for our own.** Patched singly,
each fix is one more copy; the remedy is the upstream redesign.

- `infrastructure/llm/send_pacing.py::SendBudget` stops at the relay's door: TermNorm's
  `_MAX_ATTEMPTS`, and terminus-2's `_query_llm` thrice over `connectors/harbor.py::_AgentModel` (each
  send inside on our budget), retry on their own. **Re-test:**
  `grep -n "_MAX_ATTEMPTS = " <termnorm>/backend-api/core/llm_providers.py`.

## Blocked

- **Two readings wait on the operator:** a declared slot before `rewrites` + `field` fold into one
  knob; which ruler `ab_replay` reads.
- **`infrastructure/llm/anthropic.py::AnthropicClient` cannot run a node on a current Claude model**
  (no effort mapping, sends `temperature`, never sends the schema, stale `max_tokens`). **Blocker:** no
  Anthropic credential. **Re-test:** `settings.ANTHROPIC_API_KEY` non-empty, then `probe-reasoning`.
- **A head-to-head's arms do not share one optimizer model.** **Decided:** every arm on one model,
  each peer's swap a declared deviation. **Rides with:** the next head-to-head mint. **Re-test:**
  `grep -n "model:" promptpotter/assets/optimizers/*/pipeline.yaml`.
- **Archive hygiene — build before the next bulk delete.** **Re-test:** `compact-archive inventory`.
  (1) **Reclaim**: a dataset-scoped destructive `delete` counterpart, dry-run by default, refusing
  while a producer can append, naming what it would strand. (2) **Attribution**: an L4 outer cell
  stamps the runs its inner campaign produced when it spawns them; never re-file a backfill. (3)
  **The reach map**: the campaign family left, the archive partitions it reaches right, the
  load-bearing column what is shared outside the selection.
- **Cross-repo (TermNorm `backend-api`):** a `/version` endpoint, then bump
  `connectors/termnorm.py::_EXPECTED_REVISION` (re-test: it is `None`). A co-owned backend fix replays
  stale results because neither cache keys on backend revision: fold the revision pin into the
  measurement-cache key (re-test: grep for `--fresh` or a revision term on the key).
- **No inbound REST credential** — owned by [`../developer/stable-api.md`](../developer/stable-api.md)
  § 8; owed here: the credential and its `submit → poll → fetch` examples. **Re-test:** a bearer or
  API-key reader in `promptpotter/presentation/api/`.
- **Swapping a model means hand-editing two `pipeline.yaml` lines.** **Blocker:** a swap verb.
  **Re-test:** one in `presentation/cli/commands/`.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` banks a missing count as an
  empty account.** Never estimate from content length. **Blocker:** `TokenUsageRecord` has no
  unknown-count dimension. **Re-test:** a `provider: groq` in any dataset pipeline.
