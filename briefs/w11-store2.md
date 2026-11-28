# w11-store2: area 02, round 2: a Line states its folds; the landing key is keyed by shape (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 02 and the debt doc's `## 02` section (14 rows). w11-store made a line's fold one `read_line` of a
`Line`, and derived the landing key. This round finishes both.

Target:
- A `Line` states its folds and the ledgers that answer for a hop, and no reader decides them.
- The landing key moves only when a stored shape moves.
- A store's version is its subject's, never the whole store's.
- A write either lands or refuses at its door, on every OS.

Links, deletion first. Work each row along its root:
1. **Which ledgers answer for a hop is decided at each reader**, **`store.py` holds two folds a `Line`
   should state** and **A replay feed spells the prefix-chain keys beside `file_keys`**. One root: the
   `Line` states them.
2. **The landing key over-keys by module**, **A landing key's first derivation costs ~2.3 s per code
   version** and **A landing waits on every live producer on another commit, whether or not the ledger
   shape moved**. One root: the key is derived from the stored shapes, so a code move that keeps every
   shape keeps the key, and a landing waits only on a moved shape.
3. **The feed's version spans a whole store**. The version is the subject's.
4. **The `Stores` hub holds a cycle's stop machine and the definition writer**, **An authored dataset
   definition has no writer** and **The dataset definition's `source` merges one level**. The ending
   moves to the campaign store's append door. The writer moves to the dataset store
   (`store/dataset_access.py`), the loader stamps its tier root, and `source` folds through
   `fold_layers`.
5. **On Windows a replace is refused while a reader holds the file**, **`write_json` writes with
   `indent=2`** and **`list_campaigns` drops an unreadable manifest into a log**. Each has its row's own
   root; the Windows one is re-tested on Windows semantics, as its row states.

Leave **`domain/sample.py`'s name no longer describes it** open: every chain in flight imports that
module, so its rename waits for the renames chain.

Census: `infrastructure/store/**`. Give each module with no row a verdict.

OWNS: `infrastructure/store/**` (its `io.py` included), `application/maintenance/landing.py`,
`application/maintenance/landing_key.py`, and their tests. Re-point importers anywhere, imports only.
Shared: w11-config2 runs at the same time and owns `store/measurement_archive.py::config_key` alone, for
its carrier row, so leave that function to it.

DONE WHEN:
- Each row is re-tested through a door, before and after.
- A commit that moves no stored shape keeps the landing key: name the command that proves it.
- Equals-main names each move, and the keys are named.
- Any wipe is counted first and named.
- `python scripts/gate.py` is green.
