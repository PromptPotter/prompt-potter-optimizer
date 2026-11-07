# Wave 2 — census 2 (read-only, origin/batch/2 702c7f2a4, 2026-11-07)

Every grep below ran as `git grep -n <pattern> origin/batch/2 -- <paths>` unless stated. The extra
paths `paper scripts tests webapp docs .claude deploy-linux .env.example pyproject.toml README.md`
were swept for every fact. `arc/w2j` (831e4d120: `RowHoverCard.tsx` + 4 ledger lines at 152) counts
as landed. **H** (`arc/w2h` c234d6048: `quota.py`, `test_spend.py`, `test-fixtures.ts`, the generated
pair, 2 ledger lines at ~1081) is queued next and counts as landed for chain 3.

Digest reach was measured on a `git archive` of batch/2 with a probe. The probe replaces
`nodes.optimizer_prompt_shapers` with a recorder of the same walk, then installs every
`optimizers.MANIFESTS` treatment (`PackShelf(account=None)`, all presumptions) plus
`runner/inner/connector.py::measurement_modules()`. It was run as
`PYTHONPATH=<export> PROMPTPOTTER_HOME=<scratch> .venv/Scripts/python.exe -X utf8 probe_reach.py <export>`.
It records, per treatment, the modules hashed whole and every unit reached. "Unreached" means a
module in no treatment's whole set with no reached unit. Two notes on the method:
- `_normalized_source` drops comments and docstrings, so a comment-only edit moves nothing.
- `infrastructure.*` is plumbing, which stops the walk.

The modules hashed whole that matter here:
- potter's dispatch modules;
- `domain.pipeline_parsing`, `domain.ruler`, `shared.composite`, `shared.plugin_registry`;
- the estimator's `bench.difficulty`, `intelligence.rasch`, `scoring.metrics`, `scoring.selection`
  and `domain.l4.proxies`.

The reached units that matter here: `domain.scoring::MeasuredCell`, `shared.errors::FileKind`,
`spawn_context::{_INNER_SPAWN, inner_cells, inner_spawn_context}` (not `_outer_cells`),
`pipeline_resolve::cycle_config` (not `_response`), `validate_template`, `READING_STATE_KIND`,
`ROUND_ADVANCE_STALL` and `potter.race::CatchUpPool`.

Unreached:
- in the engine: `scoring.sample_measurement`, `scoring.query_loop`, `evidence.read`,
  `application.origin`, `runner.inner.tasks`, `runner.inner.connector`, `connectors.*`,
  `views.readout`, `jobs.quota`, `application.knobs`, `config_map`, `jobs.launcher.run_job`,
  `config.first_run`;
- all of `presentation`.

Line numbers in `docs/specs/code-debt-cleanup.md` (the ledger) are batch/2's. J deletes 4 lines at
152 and H deletes 2 at ~1081, so on a tree carrying them, lines past 152 shift by −4 and lines past
1081 by −6.

---

## Chains (pairwise-disjoint files; only the ledger is shared, in disjoint hunks)

| Chain | Arcs, in order | Hand-edited files | Regenerated |
|---|---|---|---|
| 1 — engine reads | **K** stale-data step string → **L** evidence's tolerant `campaign.json` → **M** `Session.backend_id` readers → **N** `self_optimization` declaration | 11 | none |
| 2 — docs, webapp words, store writer | **O** `backend-integration.md` contract → **P** `roundOf` ×3 → **Q** unpadded `#N` → **R** files created private | 13 | none |
| 3 — served types, after H | **S** the "provider key" claim → **T** `KnobSource` literal | 4 | openapi + TS types (S, T) |

**The generated pair** (`docs/specs/openapi.generated.json`, `webapp/lib/api/types.generated.ts`)
is H's in batch 3. Chain 3 is the only one that regenerates it, so **S branches off H's tip**
(`arc/w2h`, or batch 3 once H is on it). S also shares `quota.py` with H. Chains 1 and 2 change no
served model, so they start now.

**The ledger hunks** per chain (batch/2 lines):
- chain 1:
  - 301-306: M trims the holders sentence;
  - 325-326: N deletes;
  - 790-795: L narrows;
  - 796-800: K rewrites to its other half;
  - 1137-1138: L trims a clause.
- chain 2:
  - 226-229: P deletes;
  - 255-259 and 1061-1064: R deletes;
  - 331-335: O deletes;
  - 1220-1223: Q deletes.
- chain 3:
  - 1122-1123: T deletes;
  - 1664: S deletes the sub-bullet.

The nearest cross-chain pair is chain 1's 325-326 and chain 2's 331-335. Four untouched lines (the
DSPy row) sit between them, so git merges them clean. Chain 1's 1137-1138 and chain 3's 1122-1123
have 13 untouched lines between them.

**No wipe.** No arc reshapes run data or a survivor.

---

## Chain 1

### K — the stale-data protocol's dropped step string

**Row:** "**The stale-data ladder's outcome has two owners**" (796-800). It splits:
- K ships the step-string half and rewrites the row to the rest: "`execute_stale_data_protocol`
  stamps one of five exclusive bools on `MeasuredCell` that `render/sample.py` re-reads as an `elif`
  chain; the ladder is string-keyed. Action: one outcome enum on the cell, the ladder a `match`."
- The enum half stays. `domain.scoring::MeasuredCell` is reached by every treatment, so it
  **Rides with:** the next accepted re-key. Its new re-test is
  `git grep -nE "switched_out|persistently_degraded|config_fundamental_skip" -- promptpotter/domain/scoring.py`.

**Owns (2 hand-edited):** `promptpotter/application/scoring/{sample_measurement.py, query_loop.py}`,
and ledger hunk 796-800.

**Greps:** `execute_stale_data_protocol` and `recovered, _step` repo-wide;
`"below_threshold"\|"skipped_config_fundamental"\|"exhausted"` in `promptpotter tests webapp`.

**Holders:**
- `sample_measurement.py:767`: `-> tuple[MeasuredCell, str]` becomes `-> MeasuredCell`.
- `sample_measurement.py:772, 782, 787, 798, 805, 807`: each return drops its `, "<step>"`. The step
  strings have no other reader.
- `query_loop.py:221-228`: `recovered, _step = await execute_stale_data_protocol(...)` and
  `return recovered` become `return await execute_stale_data_protocol(...)`.
- Not holders:
  - `STALE_DATA_LOAD_PROTOCOL` (`:60`, the ladder; the enum half);
  - `docs/methods/candidate-elimination.md:77` (names the function, which stays);
  - `paper/evidence/ep04-wallclock.md:78` (history);
  - the bools at `domain/scoring.py:265-270` and their reader `render/sample.py:141-180`.

**Digest:** `sample_measurement` and `query_loop` are unreached; equal.

**Served deltas, named:** none.

**Equals-main checks:**
- Full gate.
- `--digests` byte-equal.
- `--against <main home>`: decisions and requests unmoved for every optimizer.
- `git grep -n "recovered, _step" -- promptpotter` is empty.

**Estimate:** −1 / +0, about 8 lines edited in place; the ledger row −5 / +4.

**Risks:** none known. Salvage overlap: m4-2 (`query_loop.py`).

### L — `evidence/read.py` reads `campaign.json` through `load_campaign`

**Rows:**
- "**`evidence/read.py` reads `campaign.json` as a tolerant dict**" (790-795). L ships the
  `load_campaign` half. The row narrows to its other half: "`campaigns_on_dataset` matches the name
  label, so two banks under one name pool into one read. Action: the name resolved once and matched
  on `Campaign.declaration.dataset_hash`." That half changes which campaigns an evidence read pools,
  so it is a served-answer change and not a cleanup.
- 1137-1138, the "Run files read as dicts" row: the clause "B for `evidence/read.py` (its
  `campaign.json` reads, already an entry there)," goes, leaving "D for the three `llm/` and
  `tracing/` readers".

**Owns (1 hand-edited):** `promptpotter/application/evidence/read.py`, and ledger hunks 790-795 and
1137-1138.

**Greps:**
- `read_json_tolerant(CampaignLayout`, `_dataset_name`, `_dataset_of` and `campaigns_on_dataset`
  repo-wide.
- `campaign_config` in `promptpotter/domain/campaign.py`.
- `load_campaign` in `promptpotter/application/evidence`.

**Holders:**
- `:102`: `read_json_tolerant` leaves the import (`validate_path_component` stays).
- `:242-246` `_dataset_name`: deleted. Its `campaign_config` branch reads a shape `Campaign`
  (`domain/campaign.py:309`; `dataset_name: str` at `:314`) has no field for.
- `:248-249` `_dataset_of`: deleted. This also ends a name collision with
  `application/account.py:370::_dataset_of`, the typed one.
- `:252-258` `campaigns_on_dataset`: this is a second door beside
  `campaign_store/store.py:342::list_campaigns`, which already loads each manifest typed and skips
  an unreadable one with a log. It becomes
  `[c.campaign_id for c in stores.campaigns.list_campaigns(lifecycle="all") if c.dataset_name == dataset_name]`.
  It filters inline and does not pass `dataset_name=`, because `list_campaigns` reads a falsy name
  as "all". Its one caller is `:279`.
- `:532-536` `_Located` gains `campaign: Campaign`.
- `:540-554` `_at`: `leaf.campaigns.load_campaign(spec.campaign_id)` replaces the tolerant read.
  `None` returns `None`. `root_cycle_id` comes from the model.
- `:615-630` `_resolve_head`: reads `at.campaign.dataset_name` and `at.campaign.created_at`, and
  drops its own manifest read.
- `:410`: the third load of the same manifest (`load_campaign` + `if campaign is not None`) becomes
  `at.campaign`.
- Kept:
  - `:559` `_files_read` still signs the manifest file, so the cache key is unchanged;
  - `:822`, the `load_campaign` in another function, on a hop.
- Optional, for the brief to settle: `_at`'s `iter_campaign_dirs` scan could go too, by
  `campaign_root_dir`, if `load_campaign` refuses a path-unsafe id. The reading closest to main keeps
  the scan.

**Digest:** `evidence.read` is unreached; equal.

**Served deltas, named:**
- None on a landed tree: the landing proves every `campaign.json` as `Campaign`.
- On a torn manifest, an evidence read now refuses with `FileUnreadableError` (409
  `manifest_unreadable`, its remedy as `tests/test_identity.py:1196-1204` pins it). Today it is
  inconsistent: it already raises that at `:410` when the subject names a cycle id, and otherwise
  answers the subject `None`.

**Equals-main checks:**
- Full gate.
- `--digests` equal.
- `--against`: decisions and requests unmoved.
- `git grep -n "read_json_tolerant(CampaignLayout" -- promptpotter` is empty.
- `git grep -n "campaign_config" -- promptpotter/application/evidence` is empty.

**Estimate:** −14 / +6.

**Risks:**
- `_at`'s docstring ("`None`, never a raise: one mistyped id … must not fail every other subject")
  is reworded to "an address that names nothing answers `None`; a manifest that will not read
  refuses, naming its file".
- Salvage overlap: ea-8, m4-2 (`evidence/read.py`).

**Operator decision:** a torn manifest in a multi-subject evidence read. **Recommended: refuse
(409, typed)**, as `load_campaign` does everywhere else outside a survey. The alternative, `_at`
catching it as `None`, hides a state the system entered. Serving it as an unreadable row is the
survey rule of row 260, a new mechanism, which is not built here.

### M — `Session.backend_id`: the three production readers take the connector

**Row:** "**`Session.backend_id` is a test-only archive switch**" (301-306). M narrows it: the
sentence "Holders beside it: `application/origin.py:75` … `presentation/cli/commands/new.py:446`."
goes. The row's delete, its re-test (`session.py` holds no `backend_id`) and its "a test asks for no
archive explicitly" stay for the next census. The previous census left C out because its holders
spanned three chains. A (`wiring.py`) and B (`mint.py`) have landed since, so the three readers are
now one arc. The switch itself still needs a test-side no-archive door, which is new (see "Rows left
out").

**Owns (3 hand-edited):** `promptpotter/application/origin.py`,
`promptpotter/application/runner/inner/spawn_context.py`,
`promptpotter/presentation/cli/commands/new.py`, and ledger hunk 301-306.

**Greps:**
- `backend_id` in `promptpotter tests scripts paper webapp/lib webapp/components docs .claude`.
- `backend_client.connector` in promptpotter, to confirm the property exists: `node_context.py:421`
  and `seed_screen.py:275` use it.
- `def campaign_terms` and `def backend_reach_line`, for their signatures.

**Holders:**
- `origin.py:75` (`lock_criterion`): `campaign_terms(session.backend_id)` becomes
  `campaign_terms(session.backend_client.connector.name)`.
- `spawn_context.py:66` (`_outer_cells`): `is_self_optimization(session.backend_id)` becomes
  `…(session.backend_client.connector.name)`. N then replaces the call; same chain.
- `new.py:441`: `backend_reach_line(session.backend_id, session.backend_client.base_url)` becomes
  `…(session.backend_client.connector.name, …)`.
- The switch's own holders stay untouched:
  - `search_point_scorer.py:216, 305, 311`;
  - `wiring.py:274` (`backend_id=connector.name`, with `client` built from the same connector, so
    production reads one value either way);
  - `session.py:100`;
  - `tests/factories.py:413, 425, 471, 496`;
  - `test_archive.py:205`;
  - `test_scoring.py:1151, 1213, 1503, 1716, 1800, 1848`;
  - `test_spend.py:803, 1249`.
- Not holders:
  - the `measurement_archive.py:282` docstring;
  - `webapp/lib/__tests__/connector-state.test.ts:6`;
  - the `/backends/{backend_id}` REST path, a different id.

**Digest:**
- `application.origin` is unreached; `lock_criterion` runs at run init, outside the shaper walk.
- `_outer_cells` is not a reached unit, and `presentation` is unreached.
- Equal.

**Served deltas, named:** none. Production sets both values from one `Connector`.

**Equals-main checks:**
- Full gate.
- `--digests` equal.
- `--against`: decisions and requests unmoved. The offline run's dials lock goes through
  `lock_criterion`.
- `git grep -n "session.backend_id" -- promptpotter` names only `search_point_scorer.py`.

**Estimate:** ±0, three lines edited in place; the ledger −2.

**Risks:**
- A test that reaches one of the readers with `backend_id` ≠ the client's connector name.
- The factory connector is `Connector("fake", …)` (`factories.py:329`), and no test `backend_id`
  names a registered connector.
- `lock_criterion` with dials is reached only through `planned(...)` in `test_identity.py:1348-1356`,
  which builds no `Session`. The gate proves it.
- Salvage overlap: m4-2 (`origin.py`), m3 (`spawn_context.py`), ea-8 / en-9 / m14a-2 (`new.py`).

### N — `is_self_optimization` reads a `Connector` declaration

**Row:** "**`is_self_optimization` is decided by the name `promptpotter`**" (325-326). The row is
deleted.

**Owns (5 hand-edited + 1 shared in chain):** `promptpotter/connectors/{protocol.py, __init__.py}`,
`promptpotter/application/runner/inner/{connector.py, tasks.py}`,
`docs/developer/node-standard.md`, `spawn_context.py` (M's file, same chain), and ledger hunk
325-326.

**Greps:**
- `is_self_optimization`, `_recursion_connector`, `self_optimization` and
  `CONNECTORS.get("promptpotter")` repo-wide.
- `asdict(connector\|fields(Connector` in promptpotter: no generic dump of a `Connector`, so a new
  field serves nothing.

**Holders:**
- `connectors/protocol.py:125`: beside `serves_uploads`, add
  `self_optimization: bool = False` with a one-line docstring, for example "The recursion:
  a campaign on it is an L4 outer loop".
- `runner/inner/connector.py:138`: `CONNECTOR = Connector(name="promptpotter", …,
  self_optimization=True)`.
- `runner/inner/tasks.py:306-307`: `_recursion_connector` is deleted.
- `tasks.py:310-311`: the body becomes
  `(c := connectors.CONNECTORS.table.get(backend_type)) is not None and c.self_optimization`.
  An unregistered name stays `False`, as today.
- `spawn_context.py:66`: reads `session.backend_client.connector.self_optimization` directly. M left
  it as a name for the lookup to resolve again.
- `connectors/__init__.py:23` `_validate`: refuses `self_optimization` from any origin outside
  `_APPLICATION_BUILT_INS` (`:63`). This holds what the name test guaranteed, that exactly one
  connector is the recursion. It is pending the decision below.
- `docs/developer/node-standard.md:40`: "Whether it names the recursion connector … is
  `runner/inner/tasks.py::is_self_optimization`" becomes "Whether its connector declares
  `self_optimization` …".
- The callers stay unchanged: `hierarchy.py:373` (`campaign.connector`) and `pipeline_resolve.py:828`
  (`_response`, served as `self_optimization`).

**Digest:**
- `tasks`, `connectors.protocol`, `runner.inner.connector` and `_outer_cells` are unreached.
- `runner.inner.connector` is not in `measurement_modules()`.
- Equal.

**Served deltas, named:** none. `CampaignPipelineResponse.self_optimization` reads the same value.

**Equals-main checks:**
- Full gate.
- `--digests` equal.
- `--against`: unmoved.
- `git grep -n "\"promptpotter\")" -- promptpotter/application/runner/inner/tasks.py` is empty.

**Estimate:** −5 / +5.

**Risks:**
- The new name: grep found no `self_optimization` field on `Connector`, only the served
  `CampaignPipelineResponse.self_optimization`, which this flag feeds. One word for one fact.
- Salvage overlap: en-9 (`tasks.py`, `protocol.py`); m4-2 (`protocol.py`, `runner/inner/connector.py`).

**Operator decisions:**
- The field name `self_optimization`. **Recommended: yes**; it matches the served tag and the
  vocabulary.
- The `_validate` guard. **Recommended: yes**: without it, a plugin connector can declare itself the
  recursion, which the name test made impossible.

---

## Chain 2

### O — `backend-integration.md` states the `Connector` contract

**Row:** "**`backend-integration.md` presents TermNorm's wire as every backend's contract**"
(331-335). The row is deleted.

**Owns (2 hand-edited):** `docs/operations/backend-integration.md`, `README.md`, and ledger hunk
331-335.

**Greps:**
- `backend-integration` repo-wide, for the inbound links and the headings they need.
- `-i "three endpoints"` repo-wide.
- `connect.astro` in the site repo, outside this repo.

**Holders:**
- `backend-integration.md:3`: "PromptPotter optimizes any backend that speaks three endpoints"
  becomes the `connectors/protocol.py::Connector` declaration contract. A backend is a registered
  `Connector`, and every axis derives from the pipeline it declares.
- `:5-15` `## Endpoints` becomes TermNorm's section, as the HTTP wire's default. `:10` "Read once at
  init" is said of TermNorm's `GET /pipeline` only.
- `:48`: n8n's "each difference from the three endpoints declared on `Connector`" becomes "each
  difference from the HTTP default declared on `Connector`".
- Headings kept, since inbound links read them:
  - `## Connection security` (`:40`), linked by `ConnectorInspector.tsx:8` for every connector. Its
    first line says it is the HTTP connectors' env.
  - `## A webhook backend` (`:46`), linked by `.env.example:41`.
  - `## Self-healing a node` (`:72`), linked by `node-standard.md:3`.
  - `## Debugging the highway` (`:88`), linked by `connectors/CLAUDE.md:9`.
- `README.md:44`: the "agent framework … n8n, Dify, or plain FastAPI" row says "Three endpoints —
  `POST /matches`, `GET /pipeline`, `GET /status`".
- Not holders. These become true once the page states the contract:
  - `manual/03-first-campaign.md:24`;
  - `manual/06-going-deeper.md:14`;
  - `access-model.md:49`, which names TermNorm explicitly;
  - `connectors/resources/websearch-entity-profiling.n8n.json:118, 511`.
- Outside the repo: the site repo's `src/pages/connect.astro:97` repeats the three-endpoint claim.
  One line goes to the operator; it is not in this repo's Owns.

**Digest:** docs only; equal.

**Served deltas, named:** none.

**Equals-main checks:**
- Full gate (`doc-claims` checks the cited paths).
- Every inbound anchor above still resolves.

**Estimate:** −6 / +8.

**Risks:** none in code. No salvage overlap.

**Operator decision:** README.md:44. **Recommended:** keep the row, replace "Three endpoints — …"
with "A `Connector` declaration; an HTTP backend's default wire is three endpoints", and keep the
link. n8n stays in the list, since it runs through its connector.

### P — `roundOf` names three things

**Row:** "**`roundOf` names three things**" (226-229). The row is deleted.

**Owns (4 hand-edited):** `webapp/components/candidates/{dendrogram.ts, useCandidatesModel.ts,
forest-layout.ts}`, `webapp/lib/derivations/__tests__/round-candidates.test.ts`, and ledger hunk
226-229.

**Greps:** `-w roundOf` in webapp; `-w "barRound\|candidateRound\|rowRound"` in webapp, which has
no collision.

**Holders:**
- Kept, as the one `roundOf` (the dashboard's live round): `lib/poll.tsx:74` and its callers
  `round-candidates.ts:1, 19`, `search-point.ts:1, 197` and `hooks/useRound.ts:19, 40`.
- `candidates/dendrogram.ts:65, 73` become `barRound`, along with its importer
  `useCandidatesModel.ts:25, 109, 128, 147`.
- `candidates/forest-layout.ts:75, 81, 199, 219, 425` become `candidateRound`.
- The local in `round-candidates.test.ts:55, 58, 65, 67, 92` becomes `rowRound`.
- Not holders: `FileViewer.tsx::roundOfFile` and `test-fixtures.ts::roundOfArms`, which are distinct
  names. The row's re-test `git grep -n "roundOf"` matches them, so use `git grep -nw`.

**Digest:** webapp only; equal.

**Served deltas, named:** none.

**Equals-main checks:**
- `gate.py --web`.
- `git grep -nw "roundOf" -- webapp` names one definition (`poll.tsx:74`) and its importers.

**Estimate:** ±0 (16 lines renamed in place); the ledger −4.

**Risks:** salvage overlap: m1-2 (`round-candidates.test.ts`).

### Q — unpadded `#N` sample tags read the one formatter

**Row:** "**Unpadded `#N` outlives the one `sample_tag`**" (1220-1223). The row is deleted.

**Owns (4 hand-edited):** `promptpotter/application/views/readout.py`,
`webapp/components/shell/node-surface/L1Variants.tsx`,
`webapp/components/dashboard/samples/TrajectorySeriesView.tsx`,
`webapp/components/candidates/SampleSetControl.tsx`, and ledger hunk 1220-1223.

**Greps:**
- `git grep -nF -e 'sample #{' -e '#${sid}' -e '#${id}' -e '#${order' -e 'catch-up #{'` in
  `webapp/components promptpotter/application/views`. The row's own re-test regex matches none of
  the holders, so it is miscalibrated.
- `fmtSampleTag` and `sample_tag` in their homes.
- The changed strings in `tests webapp scripts docs`: no reader.

**Holders:**
- `readout.py:492` (`on_race_catch_up`): `catch-up #{position}` becomes `catch-up {sample_tag(position)}`.
  `position` is the bank slot (`query_loop.py:915-921`, `slot_of[key]`), and `sample_tag` is already
  imported and used at `:481`.
- `L1Variants.tsx:107`: `` `#${id}` `` becomes `fmtSampleTag(id)`.
- `TrajectorySeriesView.tsx:146` (`sample #{hover.sampleId}`, JSX text) and the titles at `:150`,
  `:154` and `:158`.
- `SampleSetControl.tsx:66` (the title). The row missed this one. Its `aria-label` at `:65` says
  "Sample {sid}" unpadded, with no `#`; it is left as is.
- The formatters: `webapp/lib/format.ts:19::fmtSampleTag` and `domain/sample.py:93::sample_tag`
  (`SAMPLE_TAG_DIGITS = 3`).
- Not holders:
  - `machine_status.py:43` (a queue position);
  - `VendorLogo.tsx:58` (an SVG href);
  - the chips' bare `{sid}` text.

**Digest:** `views.readout` is unreached; equal.

**Served deltas, named:**
- `readout.log` catch-up lines print `#007`, not `#7`.
- Four webapp titles and one hover line are padded.

**Equals-main checks:**
- Full gate (with `--web`).
- `--digests` equal.
- `--against`: decisions and requests unmoved. `readout.log` is not compared.
- The calibrated grep above is empty.

**Estimate:** +3 (imports) / −0, 6 lines edited in place; the ledger −4.

**Risks:** none known. No salvage overlap.

### R — every file the store appends to, and the first-run `.env`, are created private

**Rows:**
- "**A file created by `open` or `write_text` takes the umask**" (255-259). Deleted.
- "**A site-record file's mode is the writing process's umask**" (1061-1064). Deleted. The fix is
  the same: `append_jsonl` → `append_line` creates the file.

**Owns (3 hand-edited):** `promptpotter/infrastructure/store/io.py`,
`promptpotter/application/jobs/launcher/run_job.py`, `promptpotter/config/first_run.py`, and ledger
hunks 255-259 and 1061-1064.

**Greps:**
- `open\([^)]*"ab"` and `\.write_text\(` in `promptpotter`.
- `os.open(`, `O_BINARY`, `mkstemp` and `0o600` in promptpotter.
- `st_mode\|S_IMODE\|0o600` in tests: none.
- `UMask` in `deploy-linux`.
- `append_line\|append_jsonl` in promptpotter: 9 files.
- `open_append` repo-wide: no collision.

**Holders:**
- `store/io.py:480, 485` (`append_line`'s two `open(name, "ab")`) go through one public
  `open_append(path) -> BinaryIO`:
  `os.fdopen(os.open(name, O_WRONLY | O_APPEND | O_CREAT | getattr(os, "O_BINARY", 0), 0o600), "ab")`.
  The atomic writer already creates 0600 (`mkstemp`, `:219`), so this is that writer's rule, now
  stated for appends too.
- `jobs/launcher/run_job.py:134`: the stderr log's `open(log_path, "ab")` becomes `open_append(log_path)`.
- `config/first_run.py:37`: `env_path.write_text(...)` becomes `store/io.py::write_text`. A
  `config → infrastructure` import is allowed: no `_LAYERING` rule forbids it, and `io` imports
  `config.settings` with no cycle.
- `store/site_records.py`: no edit, since it appends through `append_jsonl`.
- Not holders:
  - `io.py:405, 408` `_open_lock` (empty lock files, no content);
  - dbllmbench and harbor work files;
  - `presentation/cli/commands/export.py:27`, an operator-chosen export path.

**Digest:** `infrastructure` is plumbing, and `run_job` and `first_run` are unreached; equal.

**Served deltas, named:** files created new are 0600 instead of 0644 under a 022 umask. Already
private on the box: `deploy-linux/hardening.sh:17` sets `UMask=0077` on both units. The delta lands
on the login user's CLI runs and the desk. On Windows, POSIX modes are moot.

**Equals-main checks:**
- Full gate.
- `--digests` equal.
- `--against`: unmoved.
- Ledger bytes are unchanged: `O_BINARY` keeps the CRT from translating `\n` on Windows.
- `git grep -nE 'open\([^)]*"ab"|\.write_text\(' -- promptpotter/application/jobs promptpotter/config`
  is empty.

**Estimate:** +10 / −3; the ledger −9.

**Risks:**
- Windows newline translation, if `O_BINARY` is dropped.
- A test: `tests/CLAUDE.md`'s charter admits one, since a 0644 secret is a silent leak, but it is
  POSIX-only (`skipif win32`). The brief decides.
- Salvage overlap: m17 and m2-2 (`store/io.py`).

**Operator decision:** **Recommended: yes**, every `store/io.py`-appended file (ledger, jsonl
tables, site records, job logs) is created 0600, matching the atomic writer. The alternative, 0600
for the two named files only, leaves a second rule beside the writer.

---

## Chain 3 (after H)

### S — the false "provider key" claim

**Row:** the sub-bullet "`AccountUsageTab::LimitControl`'s note ("runs on their provider key")
predates own keys." (1664). Deleted; its parent list stays.

**Owns (2 hand-edited + 2 regenerated):** `webapp/components/account/AccountUsageTab.tsx`,
`promptpotter/application/jobs/quota.py` (H's file; S branches off H's tip), the generated pair, and
ledger hunk 1664.

**Greps:** `max_concurrent_cycles_writable`, `concurrent_cycles_writable` and `provider key`
repo-wide.

**Holders:**
- `AccountUsageTab.tsx:243-244`: "This account runs on their provider key, so they decide how many
  of its runs share the machine."
- The rule it misstates: `quota.py:983-984` (`is_host(...) and has_capability(...,
  CAMPAIGN_BUDGET_CAP)`). The limit is writable by the host account holding the budget capability,
  whoever's key pays.
- The note becomes "Whoever runs this server sets how many of this account's runs share the
  machine."
- `quota.py:468-471`, the field description "False on the host's key, where the host sets it.",
  becomes "False for any caller but the host account with the budget capability." Its line numbers
  are batch/2's; H deletes 14 lines above it, so grep.
- Generated: `openapi.generated.json:13318` and `types.generated.ts:59` carry the description.
- Not holders: `deploy-linux/README.md:102` and `offline-run.md:22`, which use "provider key" in
  another sense.

**Digest:** `quota` is unreached; equal.

**Served deltas, named:** the field description in the openapi and TS docs. No shape moves.

**Equals-main checks:**
- Full gate (with `--web`).
- `--digests` equal.
- The openapi diff is that one description.

**Estimate:** ±0 hand-written (2 strings); ±2 regenerated.

**Risks:**
- It must follow H (`quota.py` and the generated pair).
- Salvage overlap: m14a-2 (`quota.py`).

### T — `KnobSource` literal

**Row:** "**`application/config_map.py::ConfigKnob.source` is an untyped `str`**" (1122-1123).
Deleted.

**Owns (2 hand-edited + 2 regenerated):** `promptpotter/application/{knobs.py, config_map.py}`, the
generated pair, and ledger hunk 1122-1123.

**Greps:** `ConfigKnob`, `KnobState`, `resolve_knob_states` and `KnobSource` repo-wide, which
finds no collision; `\.source\b` in `webapp/components`.

**Holders:**
- `knobs.py:298`: `KnobState.source: str` becomes `source: KnobSource`. Declare
  `KnobSource = Literal["default", "campaign", "required", "manifest"]` beside it. The writers are
  `:326` and `:328`.
- `config_map.py:32-35`: `ConfigKnob.source: str` becomes `KnobSource`. The description keeps its
  per-value glosses.
- Generated: `openapi.generated.json:4002` becomes an enum; `types.generated.ts:3908-3910` becomes a
  union.
- Readers, unedited: `webapp/components/shell/sidebar/RowHoverCard.tsx:22` (J's file;
  `knob.source === "campaign"` type-checks against the union).
- Not holders: `NodeKnobsPanel.tsx`'s `pool.source` (`POOL_SOURCE_INFO`), another model.
- `roadmap.md:70` (U1b), `:165` (M4c: "rides M8's `ConfigKnob.source` (yes)") and `:169` (M8) name
  this item. The orchestrator trims them; they are not in T's Owns.

**Digest:** `knobs` and `config_map` are unreached; equal.

**Served deltas, named:** `ConfigKnob.source` is served as an enum. The values are unchanged.

**Equals-main checks:**
- Full gate (with `--web`; `surface-ledger` must not rise).
- `--digests` equal.
- `git grep -n "source: str" -- promptpotter/application/config_map.py promptpotter/application/knobs.py`
  is empty.

**Estimate:** +2 / −0 hand-written; about +5 / −1 regenerated.

**Risks:**
- A `surface-ledger` row may move with a new exported literal. A raise is reasoned in the commit
  body.
- RowHoverCard is J's (batch 3), but it is not edited here.

**Operator decision:** T pre-empts an item M8 lists. **Recommended: yes.** It is a one-literal
change with no re-key, and M8 keeps its other items.

---

## Rows left out, and why

- **`Session.backend_id` itself** (C; M takes its three readers): the delete needs a named
  test-side no-archive door, for `factories.py` and the `test_scoring` / `test_spend` /
  `test_archive` sessions. That is a new mechanism; it gets its own census after M.
- **`PipelineSchema` name/version**: `domain.pipeline_parsing` is hashed whole, so it rides M4's
  re-key.
- **`embedded_run.__all__` wrappers and the two typed REST routes**: they wait on M6's one
  dispatcher.
- **The unknown-slot refusal**: `validate_template` is reached.
- **The two word tables** (`READING_STATE_KIND`, `ROUND_ADVANCE_STALL`): reached.
- **`useLeafDatasetName`, `ids.ts::rootCycleId`, `DefaultClaim.renamed`, `blocklist_audit` /
  `grants_audit`, `slug_exists`, the candidates-surface formatters**: their blockers are unchanged
  since census 1.
- **`CatchUps` / `NoCatchUps`**: `potter.race::CatchUpPool` is reached.
- **The stale-data outcome enum** (K's other half): `MeasuredCell` is reached by every treatment.
- **`shared/errors.py` "No migration exists" remedies**: `FileKind` is reached.
- **"declined pause '.'"**: served words in the generated pair, plus a wording choice. It could join
  chain 3 after T once the operator words it.
- **`ActiveSessionResponse` reshape**: a served reshape with a webapp edit beyond a cleanup.
- **`TokenUsageRecord.cached` vs `key_source is None`**: a ledger record shape change, so a
  run-data wipe. Keep it for a batch that already wipes.
- **DSPy pricing split** (327-330): it moves metered spend when a dataset sets `provider`.
- **`index_terms` two calls** (321-324): it moves request order at session open.
- **Three TermNorm defaults / `DEFAULT_CONNECTOR`** (307-313): an M18 design question.
- **Unregistered `backend_type` at fifteen sites** (314-320): a typed refusal at 15 sites plus a
  `_validate` rule is more than 10 files.
- **`Session.langfuse`**: it shares `tracing/bridge.py` with the `OBS_ENABLED` row, which is
  excluded.
- **`FAMILIES` null pane**: a mobile-first design fork.
- **`filesRead` URL**: it needs `/files` descend support first.
- **`.prompt-snippet-input`**: the render changes.
- **M13 / M14 names** (198-200): the operator names the milestones.
- **The CLI's `ERROR:` prefix**: Q3, with many sites.
- **`jobs/logs` retention**: an operator decision. R makes the logs private, not bounded.
- **Wave B forwarders** (`_spent`, `read_line_spend`, `fresh_campaign_id`): small named helpers with
  several callers, not dead.
- **Excluded by the brief**:
  - A, B, D1, D2, E, F, G, H, I, J;
  - every `scripts/gate.py`, `last_green.py`, `processes.py` and `build_*.py` row, including the
    WinError retry row at 205-209, which `processes.py` holds;
  - `storage_report.py::_leaf_of`;
  - `OBS_ENABLED`.

## Shared-file and order notes

- **The ledger** is the only file two chains touch, in the disjoint hunks above.
- **Within chain 1:**
  - K and L edit adjacent rows (796-800 and 790-795) in sequence.
  - M and N both edit `spawn_context.py:66`. M names the connector; N reads its declaration.
- **Chain 3 waits for H.** S branches off H's tip, and T off S. The generated pair is regenerated
  once per link.
- **Orchestrator-only ledger edits.** No arc owns these, and each is a ledger-only commit:
  - **Duplicate rows**: "`LaunchState` is built before its ledger opens" (201-204) and
    "`LaunchState.ledger` is `Optional` while a round needs it" (1182-1185) state one fact. Keep
    1182-1185, which names the five readers, and delete 201-204.
  - **A stale clause** in the hand-built-controls row (1014-1016): "`RunLimitsControl::capInput` and
    `LimitReconcile::parseCapIn` beside `run-limits.ts::parseCap`". Both already live in
    `webapp/lib/run-limits.ts` and are imported (`RunLimitsControl.tsx:15`, `LimitReconcile.tsx:5`),
    so its re-test matches the imports. Drop the clause and `parseCapIn|capInput` from the re-test.
  - **Roadmap** `:70`, `:165` and `:169`: drop `ConfigKnob.source` once T lands. M4c's "rides M8's
    `ConfigKnob.source` (yes)" goes with it.
- **Outside the repo**: `promptpotter-web` `src/pages/connect.astro:97` repeats O's three-endpoint
  claim. It is the operator's site.
- **Salvage overlaps**, for the brief writer to check each tag before the arc:
  - m4-2: K, L, M, N;
  - ea-8: L, M;
  - en-9: M, N;
  - m14a-2: M, S;
  - m3: M;
  - m1-2: P;
  - m17 and m2-2: R.
- **Batch suggestion:**
  - batch 3: J + H + the first links K and O, which have no dependency on H;
  - batch 4: L, P, S;
  - batch 5: M, Q, T;
  - batch 6: N, R.
  - Chains 1 and 2 can also run two links per batch, since their files are disjoint.
