# infrastructure/ — I/O contracts

No use case writes to disk or talks to a network except through one of these seams.

## Persistence — one ingress, two projections

- **Ingress is the per-cycle `CycleEventLog`** (`cycles/{cycle_id}/.runtime/ledger.jsonl`). **An append is never swallowed.** <!-- probe: git grep -cE "graceful\(.*append" -- promptpotter/*.py == 0 -->
- **Every append to or rewrite of a shared file serializes inside `store/io.py`**; `store/io.py::take_lock` is the one OS file lock. <!-- probe: git grep -cE "filelock|msvcrt|fcntl" -- promptpotter/*.py <= 7 -->
- **The ledger is a chronology**: a field earns its place only if ordering makes it findable; an archive row rides as its `(node_configs, sample_key)` address, never the row.
- **A record has no live-only half**, so a live fold and a disk fold agree at every offset. <!-- probe: git grep -cE "class RoundStandingRecord" -- promptpotter/domain/run_records.py == 0 -->
- **`dashboard.json` is a checkout of the fold, and nothing reads it back** <!-- probe: git grep -cE "\.dashboard\b" -- promptpotter/application/served_dashboard.py == 0 --> (`live_dashboard/projection.py::DashboardCheckout`, the one writer of its ledger's `store/read_model.py::LedgerIndex` fold). It swaps atomically, settles within `_DASHBOARD_DEBOUNCE_S` and writes at once at round boundaries (`_AT_ONCE`). The round file has the same atomicity; `closed_rounds.py::RoundFileProjection` is its sole writer. <!-- probe: git grep -cE "RoundFileProjection\(" -- promptpotter/*.py <= 2 -->
- **A round IS its last `RoundStatement`**; only a `RoundClosedRecord` moves the clock, and every resume, fork and read takes standing rounds off the ledger (`ledger_scan.py::scan_standing_rounds`).
- **`LiveDashboardProjection` RESOLVES; it never hands the browser scalars to join**: a live row is the closed row's shape (`domain/dashboard_rows.py`), with no `live` flag beside it.
- **A `RoundWarningRecord` is a self-heal only** (`run_records.py::RoundWarningKind`): a halt is its `StopReason`, a decided outcome a field of its step's record, a projection's fault its `derived_file_stale` (`store/read_model.py::LedgerIndex.shielded`), never a raise into the append whose line landed. <!-- probe: git grep -cE "emit_round_warning\(" -- promptpotter/*.py <= 4 -->
- **A scan takes the LAST close**; every `ledger_scan.py::scan_*` lets an `OSError` raise, and none instantiates `CycleEventLog`. <!-- probe: git grep -cE "CycleEventLog\(" -- promptpotter/infrastructure/store/campaign_store/ledger_scan.py == 0 -->

## One deleter — `rmtree_robust`

Every recursive delete goes through `store/io.py::rmtree_robust` / `unlink_robust`; a bare `shutil.rmtree` is a bug. <!-- probe: git grep -cE "shutil\.rmtree" -- promptpotter/*.py <= 1 -->

## Backend wire and Docker host

`docker_host.py` is the ONE `docker` CLI call <!-- probe: git grep -cE "^\s+.docker.,$" -- promptpotter/*.py <= 1 -->; taking a machine slot claims the machine (`backend.py::MachineSlots.hold`).
