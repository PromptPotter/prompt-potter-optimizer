# infrastructure/store/ — stores and the reads over them

## Stores

- **A new content-addressed cache joins `config/paths.py::SHARED_CACHE_DIRS`** (an L4 sandbox isolates campaign state, never a content-keyed cache); a new file joins `data_root.py::KINDS` as one row, its shape its reader's own model ([decisions](../../../docs/decisions.md) § 0011).
- **A new measurement field that is a FACT joins `MeasuredCell`, a STAMP joins `FiledAnswer`**; a `pipeline_data` key needs `_INFRA_KEYS` or an observation mapping, or it is dropped at measure time in silence.
- **`optimizer_reuse` and `judge_reuse` are separate `LLMReuseCache` instances**: a grader reading the loop's cached answers would be a ruler fed by what it measures.
- **`store/__init__.py` re-exports nothing**, or a leaf import cycles back through `ledger`. <!-- probe: git grep -cE "^(from|import) " -- promptpotter/infrastructure/store/__init__.py == 0 -->
- **JSON for what code writes, YAML for the operator-authored tier; there is no tolerant YAML reader.** <!-- probe: git grep -cE "read_yaml_tolerant" -- promptpotter/*.py == 0 -->
- **`account_spend.py` banks a cycle's spend inside every destroyer**; `mirrored` L4 inner copies are skipped, or that money banks twice.
- **A cycle's own facts are ledger records; `index.json` is their fold**, read back off the ledger, never the file; so are the cycle seed and the standing budget (last-wins, never inherited by a fork). What a cycle's loop was asked is the account's command log's (`CommandBook.controls`), which no ledger move or delete reaches. The δ ruler is the tenant's `ruler.jsonl`, never rewritten.

## The lineage tree

**The tree is a read model and decides nothing** (`application/hierarchy_subtree.py`). **A round fact lands on the record that OWNS it**: the crown, θ and matched-parent lift ride `ElectionRecord`, never `ArmState`, which closes before the election stamps them.

## The archive is not scoped by campaign

**`measurements/` is ONE content-addressed tree per archive home, and it outlives the campaigns that filled it.** A row is filed under the dataset hash it measured, never a label or the paying campaign; the join is `CandidateFacts.reading.sp_hash` → `prompt_fields_id`. **`MeasurementArchive` is the archive's only gateway**; programmatic reads go through `archive_queries.py`. **Every archive path is built from `measurement_archive.py::ArchiveFile`** <!-- probe: git grep -cE "MEASUREMENTS_DIR\s*/" -- promptpotter == 0 -->; a dataset name in any other tenant path is one segment, `layout.py::dataset_id_prefix`.

## Dataset content has three tiers, and only one is writable

**A dataset DEFINITION is authored; everything DERIVED from it is the operator's** and lands in the tenant tree, never under the definition's directory. A name resolves ONCE, to the first tier holding it: tenant, install, research (`dataset_access.py::resolve_dataset`); every chain read takes the `ResolvedDataset`, never a bare directory. `Stores.write_own_definition` refuses a NEW name another tier holds; no verb writes the install or research tier.

## Picking a JSON reader is a decision

`read_json_tolerant` collapses *absent* and *corrupt*; `read_json_optional` lets corrupt raise. Tolerant only for a cross-cycle survey; optional wherever the caller acts differently on the two (the identity readers, where collapsing would fail OPEN). Hand-rolled `json.loads(path.read_text())` in a `try` is the bug. <!-- probe: git grep -cE "json\.loads\(.*read_text\(" -- promptpotter/infrastructure/*.py == 0 -->
