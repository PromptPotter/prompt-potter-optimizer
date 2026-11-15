# rk-rest: the rows rk-run 4–8's landing left (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `rk-rest`, arc `arc/rk-rest`.

- **Base:** `origin/main` at `4874ea04c` (small-3 landed). This is your `<base sha>` in common.md.
  mkey lands beside you (accounts, auth, `dispatcher.py`, `shared/identity.py`), in no file of yours.
- **The code shrinks**, judged as common.md says. Two converters, a second pair binder, a re-parse at
  each reader and a registry outside its module go. Run `git diff --shortstat <base> HEAD -- promptpotter`
  after each link.
- **Spec:** every `code-debt-cleanup.md` row whose **Rides with** names `rk-rest`. That is seven rows,
  listed in the links below. Re-run each row's Re-test first. If one already reads fixed, delete it and
  say so. **Not yours:** any other row.
- **Beside you:** **faults-2a** works in `command_kinds.py`, `dispatcher.py`, `shared/errors.py` and
  `api/`. **l4-final** may run later in `runner/inner/tasks.py`. If you need a file one of them owns,
  name it in your commit body and touch only your hunk.

## The census (orchestrator, at main fd7362f81; re-grep it on your base)

- **Pairs:**
  - Core binds each node's pair once per cell (`scoring/sample_measurement.py::priced_pairs` →
    `BackendClient.priced_as`) and hands `cell_billing(schema, pairs, each)` to `run_query`.
  - `infrastructure/backend.py::_in_process_cell` calls `run(self.workload, sample, payload)`, so the pairs
    never reach the run.
  - `connectors/dspy_module.py` re-binds with `priced_pair` at `:328` and `:366`.
  - `InProcessRun` (`connectors/protocol.py:72`) has four implementations: `dspy_module`, `dbllmbench`,
    `harbor`, and `runner/inner/spawn.py`.
- **Resend cost:** `infrastructure/llm/openai_compat.py::_admitted_send` closes one admission per
  attempt. A failed attempt closes with `may_have_billed`. `_chat` builds the `LLMResponse` from the
  last reply alone. Harbor's `_AgentModel.cost_usd` sums those responses.
- **Check-in plan:** `jobs/launcher/checkin.py::_land_draft` loops over the overlays, calling
  `select_optimizer` and `refuse_unrunnable` (`:136-151`). Then `checkin.py:96` calls
  `jobs/mint.py::resolve_cycle_plan`, which selects again. `resolve_cycle_plan` has further callers in
  `mint.py` (`:353`, `:358`, `:505`, `:676`) and in `runner/inner/spawn.py:291`.
- **Criterion term:**
  - `datasets/draft_patch.py:140` compiles the patched draft through `authored.py::scorer_of`.
  - `scoring/formula/compiler.py::compile_scorer` resolves names against
    `scoring/evaluators.py::cell_name_owners` and the campaign's own owners.
- **matcher / dials:**
  - `draft_patch.py::_SCORING_EDIT_KEYS` and `::_folded_scoring` turn `matcher` into a `per_sample`
    formula.
  - `datasets/draft_campaign.py::draft_criterion` reads it back.
  - The browser sends it from `webapp/components/ingest/IngestConversation.tsx`.
  - `compiler.py::DIALS_KEY` is already a block key.
- **Experiment:**
  - `protocol.py::InProcessWorkload.experiment: Mapping | None` is built at
    `initialization/wiring.py:226`.
  - It is read raw by `dbllmbench.py:115`, `harbor.py:460` and `:1058`, `initialization/session.py:124`,
    and `pipeline_resolve.py:1248`.
  - It is re-parsed by `runner/inner/tasks.py::parse_resolved` (called at `tasks.py:454` and
    `spawn.py:431`, `:474`).
  - The source is `Connector.resolve_experiment` (`store/dataset_access.py:453`).
- **Registry:**
  - `application/optimizers/__init__.py` defines `NODE_MEMBERS` (`:130`), `llm_nodes`, `unfit_pipeline`
    and `_members`.
  - `optimizers/nodes.py` holds the member protocols.
  - No digest walk holds the registry.

## Decisions (orchestrator; build them, do not reopen them)

1. **A cell's pairs are handed to its run.** `InProcessRun` takes the pairs core bound for the cell, by
   node. `_in_process_cell` passes the pairs `run_query` already holds, and DSPy prices off them.
   - `CellBilling` stays a reply reader.
   - The other three implementations take the argument unused, as `_pairs`.
   - `priced_pair` leaves `dspy_module.py`.
2. **A call's cost is what its admissions booked.** `LLMResponse.cost_usd` sums every attempt that call
   closed as billed or may-have-billed, at the amount the admission booked, never the last reply's alone.
   - Harbor reads it unchanged.
   - One function per fact: a 5xx then an answer gives a `cost_usd` equal to the ledger's.
3. **A check-in plans before it lands.** The cycle plan is resolved off the draft's laid config before
   `commit_upload_definition`, and the campaign's cycle config carries the selection.
   - `refuse_unrunnable` reads that one selection. The second `select_optimizer` between Start and the
     first plan goes.
   - The other `resolve_cycle_plan` callers keep their signature, or take the carried selection where
     they already hold it. Name each.
4. **A criterion naming a term no owner holds is refused at compile.** `compile_scorer` refuses it, so
   `draft_patch.py:140` refuses it at the check-in, with no list of its own. The digests move by the
   compiler's source: name each, old → new.
5. **The compiler owns the pick's spelling.** `matcher` becomes a key of the compiler's scoring block
   (beside `per_sample`, `per_cell`, `dials`), and `_folded_scoring`'s conversion goes.
   - `draft_criterion` and the browser send and read the block as stored.
   - A stored campaign config whose shape changes wipes, named. It is run data, never a reader of two
     shapes.
6. **Each connector declares its experiment model.** `Connector` names the model.
   `resolve_experiment` returns it parsed, `InProcessWorkload.experiment` carries the parsed model, and
   every reader above reads its fields. `parse_resolved` goes, and the `promptpotter` connector declares
   `ResolvedInnerPanel`.
7. **The registry lives in `optimizers/nodes.py`**, beside the members: `NODE_MEMBERS`, `llm_nodes`,
   `unfit_pipeline`, `_members`. `optimizers/__init__.py` keeps only what the package re-exports, if
   anything. No treatment re-key, so the digests must not move.

## Links (one commit each, in order)

1. Pairs (decision 1) · 2. Resend cost (2) · 3. Typed experiment (6) · 4. Registry (7) ·
5. Criterion term + matcher (4, 5) · 6. Check-in plan (3).

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg, except where link 5's compiler move re-keys. Name every moved
  digest, old → new. A moved decision is a bug.
- Each row's re-test, before and after. Delete each row in the link that closes it.
- Tests only by `tests/CLAUDE.md`'s charter: link 2's cost equality, link 5's refusal at the check-in.
  Name any other read you checked by hand.
