# infrastructure/store/ — stores and the reads over them

## Stores

`store/stores.py`: `Stores` frozen dataclass + `build_stores(identity, *, projects_root=…, install_datasets_root=…, shared_root=…)`. `shared_root` roots every CONTENT-ADDRESSED cache and equals `projects_root` everywhere except an L4 inner sandbox, which isolates campaign state but must NOT isolate a cache keyed by content hash, nor the dataset tier (`tenant_datasets`) its inner benchmark resolves through. **`config/paths.py::SHARED_CACHE_DIRS` is the sole enumeration of that set**, because three surfaces must agree on it — `build_stores` roots them at the archive home, `application/maintenance/reset.py` preserves them, and the workspace storage report counts them as the home's. A cache named in one list and not the others is destroyed by `reset` or double-counted, silently, and one of those costs money.

`Stores.identity` is the sole source of tenant scope, with `Stores.tenant_id` a derived `@property` returning the `TenantId` newtype — never an independent field (identity-foundation no-drift gate #4). Composite over the leaf stores `Stores` declares as its own fields, one class per `store/*.py`, except `optimizer_reuse` and `judge_reuse` — two instances of the one `LLMReuseCache` (`store/llm_reuse_cache.py`, beside `hash_call`, its key) differing only in namespace directory. Separate attributes rather than a shared instance: a grader able to read the loop's cached answers would be a ruler fed by what it measures. **Cite one as attribute → class → file.**

**`store/__init__.py` re-exports nothing** — import each leaf directly, so a leaf import never drags in `CampaignStore` and cycles back through `runtime_flags` / `ledger`.

Shared I/O in `store/io.py`, and **format follows authorship**: `write_json`/`read_json*` for what code writes and only code reads, `write_yaml`/`read_yaml*` for the operator-authored definition tier (`dataset.yaml`, `origin.yaml`). There is deliberately no `read_yaml_tolerant` — a corrupt config degrading to "not there" attributes a measurement to the wrong fingerprint.

Path helpers live in `store/layout.py`, the per-tenant active-session pointer in `store/session_pointer.py`, and derived reads are free functions in query modules (`store/archive_queries.py` is the template). **`store/read_model.py::append_row` / `fold_jsonl*` / `LedgerIndex` (typed records) / `LineIndex` (raw lines) are the only derived-index persistence primitives**; a second mechanism is the bug. `measurements/` is cross-cycle and cross-campaign **within one archive home** — `build_stores` roots it and every `SHARED_CACHE_DIRS` peer at `Stores.archive_home` (`layout.py::archive_home`), so content-addressing makes a row shareable across campaigns and into an L4 sandbox, and across accounts only where the host named one install-wide home (`settings.SHARED_ARCHIVE`). **A home has an owner**: by default the tenant, whose own tree it is; a named one the install's, in a namespace beside the tenants' (`layout.py::_ARCHIVE_HOMES_DIR`) that no tenant id and no tenant walk can reach. A verb that rewrites a home's cells asks its owner (`archive_maintenance.py::_refuse_unowned_home`). **A store keyed by what one ACCOUNT holds or decided roots at the tenant, never the home** — `tenant_datasets` and the δ ruler (`ruler_store.py::RulerStore`); a reader of the caches reaches them through `Stores` (`archive_home.root`), never by re-joining a tenant path. `MeasurementArchive` is the DB core — ONE instance per `base_dir` per process (`MeasurementArchive.at`), so readers share its index tail — and `store/archive_queries.py` the facade a run files through and where a task's memory scope (`bind_memory_scope`: `OpenAt` · `ArmOf` · `CellOf`) is applied — a MEMORY read past it is the bug; `application/maintenance/` rewrites the archive itself.

The `CycleDir` / `WorkspaceDir` write-target newtypes live in `domain/cycle_paths.py` — projections and stores accept these, not raw `str`/`Path` — as does `CycleHop`, which every per-cycle `CampaignStore` method takes in place of a `(campaign_id, cycle_id)` pair (both `str`, so a swapped call read as "no data" rather than raising). Build it from the carrier that owns both, never by re-pairing.

**`store/account_spend.py` banks what a subject still HOLDS, not what its rows say.** It sums an account's lifetime spend and banks it as a `SpendTombstoneRecord` before a delete takes the rows carrying it. It sits in `infrastructure/` rather than `application/` for exactly that reason: the three destroyers (`delete_campaign`, `try_delete_stub_cycle`, `delete_inner_sandbox`) call it themselves, so no caller can destroy a ledger and skip the bank. An L4 inner cycle's calls are carried onto its outer ledger as they settle, and its own copies are flagged `mirrored`, which the sum skips — banking them would bill that money twice.

**Read-once ledger records ride `CampaignStore`.** `write_cycle_seed`/`read_cycle_seed` append and scan the cycle seed as a `CycleSeedRecord`, read once at the runner seam. A fork inherits the parent's seed record virtually then appends its own, so a scan of the cycle's own ledger returns that cycle's seed.

**The δ ruler is the tenant's, not a cycle's** — `ruler.jsonl` at the tenant root, one `RulerFold` line per election, the line count its version. A fold holds rows, never a fit: a reading re-fits at the version its stamp names, so no line is ever rewritten and a torn tail costs one version, never the scale.

`write_standing_budget`/`read_standing_budget` carry the operator's standing cycle axes (`StandingBudgetRecord`, last-wins), scanned physically so a fork never inherits it; its money axes are the campaign's (`Campaign.standing_budget`). The record is the ONE store of those axes: a launch reads it and so does a run in flight (`runtime_flags.py::standing_budget`, an incremental fold), so there is no mirror file. A run's reservation and its share of the campaign's ceiling are its JOB's (`application/jobs/registry.py::Job`), which its book reads live. `metered_spend` is what a campaign's ceiling counts, each cycle's OWN appends once. **A command for a running loop is read the same way** — `runtime_flags.py::standing_controls`, the fold of the cycle's own `CommandRecord`s and acks (`ledger_scan.py::Controls`) — so a cycle's `.runtime` holds no control file and no lifecycle flag.

**A cycle's own facts are ledger records too, and `index.json` is their fold.** Its mint (`CycleMintedRecord` — parent, cut offset and `ForkSpec` for a fork), a launch's claim on it and that claim's release, its ending (the last `RunPhaseRecord` `terminal`), its result (`CycleFinalRecord`), a supersede, a measured fork direction, an operator intervention and an L4 spawn are each one append; `projections/cycle_index.py::read_cycle_index` is the ONE reading of them (`CampaignStore.load`), and `write_cycle_index` the file's one writer. Nothing reads the file, and `ledger_chain` finds a fork's parent on the mint record, never in it.

## The lineage tree — one timeline per campaign

`store/lineage_queries.py` serves the genealogy; nodes alternate `Course -> Candidate -> Course`
at any depth, so L5+ needs no new tier. **A fork is not a node** — its candidates mount onto
the parent's ONE timeline and are renumbered into it, because `C{round}.{n}` is a position in
a course's PRIVATE counter and every course mints its own `C1.1`. **Unless the fork corrects
the parent** (a `supersede` cut): then its attempts take the positions they replaced, and the
retired tail keeps its numbers under `superseded_by`.

Three bounds — get them wrong and the tree lies without erroring:

- **A cut retires only as far as the branch actually GOT** — `_retired_by` reads the branch's
  own reach back rather than asserting the future. A branch that died retires nothing.
- **Identity outranks direction.** A repair re-measures without re-minting, so a
  `candidate_id` already on the timeline is corrected in place (`equivalent` — the common
  outcome) or kept beside its
  withdrawn twin (`supersede` — two measurements of one individual, both facts), never given
  a fresh index. The RUNS are single-homed on the live row: they measured the INDIVIDUAL, and
  a fork's inner runs land in the PARENT's sandbox, so all of them arrive filed under the row
  being replaced. ⑂ marks an OFFSHOOT only — except on a branch that minted nothing, whose
  row is the course as a stand-in and keeps its own provenance.
- **Whichever cut moved the POINTER answers for run-state** — `supersede` and `equivalent`
  both do, `offshoot` alone leaves the parent running. Separate from what a cut retires.

**A round fact lands on the record that OWNS it.** Everything
`elect_round_winner` stamps rides `ElectionRecord` — the crown, each arm's θ and its matched-parent
lift (`ElectionRecord.fit`, keyed by `ArmKey`) — so the tree
carries the whole verdict before the adapters run and the round closes, and `ArmElection.held` is what
separates a round that HELD from one still scoring (`selected: false` reads identically for both).
A round's LEVEL is the cycle's, never the round's: `RoundClosedRecord.level_series` re-reads every closed round on one stamp, and the last close's is the run's. **The lift may not go on
`ArmState`**: the election stamps it AFTER the arm's last
`ArmRecord`, so it would be an all-null column on every live run.

**It is a READ MODEL and decides nothing.** The decision genealogy (`application/mask/`, the
resume replayers) rides positional `(cycle_id, round)` and must not move onto it. What a cut
writes, and why — [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md).

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per archive home (`Stores.archive_home`), and it outlives the campaigns that filled it.** Three consequences:

- **A row is filed under the BANK it measured — the dataset hash, never a label and never the campaign that paid for it.** A relabel moves a name onto a hash and rewrites no row; a re-cut bank is a new hash. On the recursion that is the *inner* benchmark's bank (`dataset.yaml::panel.inner_benchmark`) — an inner sandbox isolates campaign state but deliberately shares `shared_root`, so **`promptpotter-self`'s bytes are almost all filed under the inner dataset's hash.** Scoping anything by `--dataset promptpotter-self` reaches the outer cells and essentially nothing L4 actually cost. Count before concluding: `compact-archive inventory --dataset <name>` resolves the label to the hash it names now and prints configurations, answers and bytes by dataset, role and age.
- **Nothing on an answer names a campaign.** It carries its configuration, its dataset, its role and its grade — no `campaign_id`, no `cycle_id`, no run, because a cache hit is supposed to cross campaigns; which walk took which answer is the ledger's to say. So "what did this campaign cost on disk" is not a question the archive answers, and the join a surface needs is `ArmNode.reading.sp_hash` → the row's `prompt_fields_id` (`docs/developer/README.md` § Cross-run memory).
- **Cycle state is disposable and the rows are not**, so the rows routinely outlive every campaign that could select them: an emptied `.promptpotter/.inner/` leaves its measurements addressable only by dataset. Selecting a family and acting on "what it produced" is therefore a claim about *surviving* state — say so, rather than reporting a smaller number as if it were the whole.

Reversibility is what makes the first two survivable: `compact` keeps every field the δ ruler re-grades from and the replay cache needs, so compacting rows another campaign replays from costs it nothing. Only `purge-cold` needs the attribution, and only it is irreversible.

## Dataset content has two tiers, and only one is writable

**A dataset DEFINITION is authored; everything DERIVED from it is the operator's.**
A definition is the operator's own (`projects/{tenant}/datasets/{name}/`, a WHOLE document) or one of
the templates that ship read-only in the wheel (`config/paths.py::install_datasets_root`); the
rows a source materializes, the panel a recipe builds and the framing an LLM
decomposed are measurement inputs the operator paid for, so they land in the tenant tree — never
under the definition's own directory, where a bank satisfies the resolver's tenant-first rule and shadows the
definition it was fetched for. **A bank is held ONCE, by content**: `banks/{dataset_hash}.json`, with
one label → hash map beside them (`TenantDatasetStore.put_bank` / `relabel` / `labelled`), a label moved only
by a mint, never by a read, a fetch or an L4 inner sandbox; a fetch records which bank its
`source:` recipe cut (`record_cut` / `cut`, keyed by the recipe, `rev` included), so a new `rev`
is a new cut and the next mint moves the label onto it;
an upload is kept as `uploads/{sha256}` and named by its definition's `source:` recipe, never copied
into rows. The panel a recipe builds and the framing stay keyed by name (`materialized/{name}/`,
`task-context/{name}.yaml`). A name resolves ONCE (`store/dataset_access.py::resolve_dataset`), and every
read takes the `ResolvedDataset` it returns (`application/datasets/loaders.py::held_bank`, `dataset_experiment`; `TenantDatasetStore.materialized_panel_dir`);
sharing a directory is the only thing that would make the install tier need to be writable. **No verb writes the
install tier** — `restamp` prunes the tenant's definitions only; install content is edited as source.

## Picking a JSON reader is a decision

`read_json_tolerant` collapses *absent* and *corrupt* into one answer; `read_json_optional` lets corrupt
raise. Use **tolerant** for a cross-cycle SURVEY — walking siblings, building the
lineage tree, sizing a storage report — where one unreadable neighbour must not
fail the whole read. Use **optional** wherever the caller acts differently on the
two, and say which in a comment: `try_delete_stub_cycle` (absent = a stub to
delete, corrupt = a cycle we cannot vouch for), the served dashboard (absent folds the
ledger in silence, corrupt folds it with a warning), and the three identity readers, where absent
and malformed are opposite security answers (`check_blocklist` admits on absent
and blocks everyone on malformed — collapsing them would fail OPEN). Hand-rolling
`json.loads(path.read_text())` in a `try` is the bug.
