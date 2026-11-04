# application/evidence/ — what a set of subjects jointly says

**Read-only**: the read names a leader; graduating one is a hand-edit.

- **Every subject kind (`campaign`, `course`, `candidate`) resolves to one searchpoint's rows and reduces through `cell_channels`**; `parse_subject` owns the grammar, and a sandbox is an ordinary address (`SubjectReading.inside`).
- **A candidate's cells are the ARCHIVE's rows at every address its ledger names**, graded here — never a round file's or ledger record's copy; `SubjectReading.status: measuring` marks every figure a prefix.
- **The head-to-head reads the BENCH under ONE grader** (`comparison_grader`, served as `Evidence.scorer_id`); `head_to_head.py::pair_guard` labels a contrast (`controlled` / `uncontrolled` / `differs`), and a `differs` contrast sits outside the Holm/BH family ([decisions](../../../docs/decisions.md) § 0009). What the arms differ by is served as the members each swapped in (`HeadToHeadRow.swapped_in`).
- **The matrix is arm by arm** (`HeadToHead.contrasts`): an arm is a TREATMENT over its replicate campaigns, each bench row's replicate means differenced and the row the cluster (`scoring/paired.py::read_arms`); Holm gates on the lift's `TestFamily`, BH's q rides the contrast (`ArmContrast.q_value`); a pick's election column rides `HeadToHeadRow.election`, never this matrix.
- **A mask rides the address** (`;lens=`, `;samples=`) and ends where the readings part (`mask/divergence.py::scenario_spine`).
- **A row carries who authored the point** (`subjects.py::authorship_of`), `cached_samples` and `human_intervened` — a babysat cycle pairs with nothing.
- **`scored_cells` is the intersection, `covered_cells` the union; every pair is a `PairedReading`** (`comparison.py::pair_subjects`), keyed on `Sample.key`, Holm-corrected across pairs, never across metrics.
- **A ranked edit is a searchpoint against its own campaign's origin, keyed on `sp_hash`; never pool across campaigns.**
- **The environment table reads the roster on the variance split's cells**: `snr` over the pooled residual, Friedman on cells every subject scored, a held-out cell never pooled with search rows. Noise is `EditSpread` over a replicate ARM.
