# infrastructure/store/ — stores and the reads over them

## Stores

**`config/paths.py::SHARED_CACHE_DIRS` is the sole enumeration of the content-addressed caches** —
`build_stores` roots them, `application/maintenance/reset.py` preserves them, the storage report counts them;
a cache missing from one is destroyed or double-counted. `shared_root` equals `projects_root` except in an L4
inner sandbox, which isolates campaign state but never a content-keyed cache.

**`Stores.identity` is the sole source of tenant scope**; `Stores.tenant_id` is a derived property.

**`optimizer_reuse` and `judge_reuse` are separate `LLMReuseCache` instances** (`store/llm_reuse_cache.py`) —
a grader reading the loop's cached answers would be a ruler fed by what it measures.

**`store/__init__.py` re-exports nothing** — import each leaf, or a leaf import cycles back through `ledger`.

**Format follows authorship**: JSON for what code writes and reads, YAML for the operator-authored definition
tier. There is no `read_yaml_tolerant` — a corrupt config read as absent misattributes a measurement.

**`store/read_model.py::append_row` / `fold_jsonl*` / `LedgerIndex` / `LineIndex` are the only derived-index
persistence primitives**; a second mechanism is the bug.

**A home has an owner** — the tenant, or for a named install-wide home the install; a verb rewriting a home's
cells asks its owner. A store keyed by what one account holds (`tenant_datasets`, the δ ruler) roots at the
tenant, never the home. A MEMORY read past `store/archive_queries.py`'s scope (`bind_memory_scope`) is the bug.

**`store/account_spend.py` banks what a subject still holds before a delete** — the three destroyers call it
themselves, so none can skip the bank; `mirrored` L4 inner copies are skipped, or that money bills twice.

**A cycle's own facts are ledger records, and `index.json` is their fold** — `projections/cycle_index.py::read_cycle_index`
is the one reading, `write_cycle_index` the one writer, and nothing reads the file. The same holds for the
cycle seed (`CycleSeedRecord`), standing budget (`StandingBudgetRecord`, last-wins, never inherited by a fork)
and running-loop commands (`runtime_flags.py::standing_controls`): no mirror or control file. **The δ ruler is
the tenant's** — `ruler.jsonl`, one `RulerFold` per election, never rewritten.

## The lineage tree — every course drawn where it spawned

`application/hierarchy_subtree.py::hierarchy_subtree` serves the genealogy as the hierarchy's entries, at any
depth. **A fork IS a course**, hung off the candidate it was cut from; `C{round}.{n}` is a course's private
counter, so nothing is renumbered. **A course's line is ONE resolution, pure over recorded facts** (`_Read.owner`):
every name resolves to an address on that line, never a search. Three bounds:

- **A cut retires only as far as the branch actually GOT** — status (`live` · `rewound` · `retired(by)`) is
  derived after every line resolves, so no fork writes it; a retired candidate stays, tagged `superseded`.
- **Identity outranks position** — two candidates may share an `individual`; `answers_for_id` marks the one a
  remembered id resolves to.
- **An inner run hangs under the candidate that SPAWNED it** (`SpawnedBy`, recorded at launch).

**A round fact lands on the record that OWNS it**: the crown, θ and matched-parent lift ride `ElectionRecord`,
never `ArmState`, which closes before the election stamps them. **The tree is a read model and decides
nothing**; a course's card is `application/hierarchy.py::campaign_entry`'s.

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per archive home, and it outlives the campaigns that filled it:**

- **A row is filed under the bank it measured — the dataset hash, never a label or the paying campaign.** On
  the recursion that is the inner benchmark's bank, so `--dataset promptpotter-self` reaches almost nothing L4
  cost. Count first: `compact-archive inventory --dataset <name>`.
- **Nothing on an answer names a campaign**, so per-campaign disk cost is not an archive question; the join is
  `CandidateFacts.reading.sp_hash` → `prompt_fields_id`.
- **Cycle state is disposable and the rows are not** — a claim about what a family produced is about surviving state.

`compact` is reversible; only `purge-cold` needs attribution, and only it is irreversible.

**Every archive path is built from `measurement_archive.py::ArchiveFile`** — a new kind is a member, and
`application/maintenance/restamp.py::_ARCHIVE_WALKS` refuses to import without its walker.

## Dataset content has two tiers, and only one is writable

**A dataset DEFINITION is authored; everything DERIVED from it is the operator's** and lands in the tenant tree,
never under the definition's directory, where it would shadow the definition. **A bank is held once, by
content** (`banks/{dataset_hash}.json` + one label → hash map); a label moves only on a mint. A name resolves
ONCE (`store/dataset_access.py::resolve_dataset`). **No verb writes the install tier** — install content is source.

## Picking a JSON reader is a decision

`read_json_tolerant` collapses *absent* and *corrupt*; `read_json_optional` lets corrupt raise. **Tolerant only
for a cross-cycle survey; optional wherever the caller acts differently on the two**, with a comment — e.g.
`try_delete_stub_cycle`, and the identity readers, where `check_blocklist` collapsing them would fail OPEN.
Hand-rolled `json.loads(path.read_text())` in a `try` is the bug.
