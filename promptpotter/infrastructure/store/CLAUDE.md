# infrastructure/store/ — stores and the reads over them

## Stores

**A new content-addressed cache joins `config/paths.py::SHARED_CACHE_DIRS`** — `build_stores` roots,
`application/maintenance/reset.py` preserves and the storage report counts only what it lists, so a
cache missing from it is destroyed or double-counted. `shared_root` equals `projects_root` except in an
L4 inner sandbox, which isolates campaign state but never a content-keyed cache.

**`optimizer_reuse` and `judge_reuse` are separate `LLMReuseCache` instances** (`store/llm_reuse_cache.py`) —
a grader reading the loop's cached answers would be a ruler fed by what it measures.

**`store/__init__.py` re-exports nothing** — import each leaf, or a leaf import cycles back through
`ledger`. <!-- probe: git grep -cE "^(from|import) " -- promptpotter/infrastructure/store/__init__.py == 0 -->

**Format follows authorship**: JSON for what code writes and reads, YAML for the operator-authored
definition tier. There is no tolerant YAML reader — a corrupt config read as absent misattributes a
measurement. <!-- probe: git grep -cE "read_yaml_tolerant" -- promptpotter/*.py == 0 -->

**A home has an owner** — the tenant, or for a named install-wide home the install; a verb rewriting a
home's cells asks its owner. A store keyed by what one account holds (`tenant_datasets`, the δ ruler)
roots at the tenant, never the home. A MEMORY read past `store/archive_queries.py`'s scope
(`bind_memory_scope`) is the bug.

**`store/account_spend.py` banks what a subject still holds before a delete** — every destroyer calls
it itself, so none can skip the bank; `mirrored` L4 inner copies are skipped, or that money bills twice.
**A subject's COST and the account's BILL are two folds** (`_Cost`, `_Billed`): a campaign row reads
`campaign_spend` (cost, an inner run's included); only a bill or a bank skips `mirrored`.

**A cycle's own facts are ledger records, and `index.json` is their fold** — written by
`projections/cycle_index.py::write_cycle_index`, read through `read_cycle_index` off the ledger, never
off the file. The same holds for the cycle seed (`CycleSeedRecord`), standing budget
(`StandingBudgetRecord`, last-wins, never inherited by a fork) and running-loop commands
(`runtime_flags.py::standing_controls`): no mirror or control file. **The δ ruler is the tenant's** —
`ruler.jsonl`, one `RulerFold` per election, never rewritten.

## The lineage tree

**The tree is a read model and decides nothing** (`application/hierarchy_subtree.py`); a course's card
is `application/hierarchy.py::campaign_entry`'s. **A round fact lands on the record that OWNS it**: the
crown, θ and matched-parent lift ride `ElectionRecord`, never `ArmState`, which closes before the
election stamps them.

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per archive home, and it outlives the campaigns that filled it:**

- **A row is filed under the bank it measured — the dataset hash, never a label or the paying campaign.** On
  the recursion that is the inner benchmark's bank, so `--dataset promptpotter-self` reaches almost nothing L4
  cost. Count first: `compact-archive inventory --dataset <name>`.
- **Nothing on an answer names a campaign**, so per-campaign disk cost is not an archive question; the join is
  `CandidateFacts.reading.sp_hash` → `prompt_fields_id`.
- **Cycle state is disposable and the rows are not** — a claim about what a family produced is about surviving state.

`compact` is reversible; only `purge-cold` needs attribution, and only it is irreversible.

**Every archive path is built from `measurement_archive.py::ArchiveFile`, keyed by a content address, never a
name** — a new kind is a member, and `application/maintenance/restamp.py::_ARCHIVE_WALKS` refuses to import
without its walker; a file no kind names is removed by the re-stamp. <!-- probe: git grep -cE "MEASUREMENTS_DIR\s*/" -- promptpotter == 0 --> A dataset name in any other tenant path
is one segment, `layout.py::dataset_id_prefix`.

## Dataset content has two tiers, and only one is writable

**A dataset DEFINITION is authored; everything DERIVED from it is the operator's** and lands in the tenant tree,
never under the definition's directory, where it would shadow the definition. **A bank is held once, by
content** (`banks/{dataset_hash}.json` + one label → hash map); a label moves only on a mint. **A name
resolves ONCE, tenant-first** (`store/dataset_access.py::resolve_dataset`); inside a run every read takes
the `ResolvedDataset` on `Session.dataset`. **No verb writes the install tier** — install content is source.

## Picking a JSON reader is a decision

`read_json_tolerant` collapses *absent* and *corrupt*; `read_json_optional` lets corrupt raise. **Tolerant only
for a cross-cycle survey; optional wherever the caller acts differently on the two**, with a comment — e.g.
`try_delete_stub_cycle`, and the identity readers, where `check_blocklist` collapsing them would fail OPEN.
Hand-rolled `json.loads(path.read_text())` in a `try` is the bug. <!-- probe: git grep -cE "json\.loads\(.*read_text\(" -- promptpotter/infrastructure/*.py == 0 -->
