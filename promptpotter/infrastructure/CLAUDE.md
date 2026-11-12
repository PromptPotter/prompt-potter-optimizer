# infrastructure/ — I/O contracts

No use case writes to disk or talks to a network except through one of these seams.

## Persistence — one ingress, two projections

- **Ingress is the per-cycle `CycleEventLog`** (`cycles/{cycle_id}/.runtime/ledger.jsonl`). **An append is never swallowed.** <!-- probe: git grep -cE "graceful\(.*append" -- promptpotter/*.py == 0 --> Every append to or rewrite of a shared file serializes inside `store/io.py` (`take_lock`, the one OS file lock). <!-- probe: git grep -cE "filelock|msvcrt|fcntl" -- promptpotter/*.py <= 7 -->
- **A new ledger record is a `CycleRecord` arm**, appended where the runner holds its cycle's ledger, or by an `emit_*` (`llm/telemetry.py`) where the call site holds no ledger handle; each projection surfacing it overrides `_handle_*` and each fold reading it takes it: a record no projection handles reaches no artifact, and a kind no fold takes refuses the wiring (`store/read_model.py::LedgerIndex.check_every_arm_read`).
- **The ledger is a chronology**: a field earns its place only if ordering makes it findable; an archive row rides as its `(node_configs, sample_key)` address. A record has no live-only half, so a live fold and a disk fold agree at every offset. <!-- probe: git grep -cE "class RoundStandingRecord" -- promptpotter/domain/run_records.py == 0 -->
- **`dashboard.json` is a checkout of the fold, and nothing reads it back** <!-- probe: git grep -cE "\.dashboard\b" -- promptpotter/application/served_dashboard.py == 0 -->; `closed_rounds.py::RoundFileProjection` is the round file's sole writer. <!-- probe: git grep -cE "RoundFileProjection\(" -- promptpotter/*.py <= 2 -->
- **A round IS its last `RoundStatement`**; only a `RoundClosedRecord` moves the clock, and every resume, fork and read takes standing rounds off the ledger. A scan takes the LAST close, lets an `OSError` raise and instantiates no `CycleEventLog`. <!-- probe: git grep -cE "CycleEventLog\(" -- promptpotter/infrastructure/store/campaign_store/ledger_scan.py == 0 -->
- **A projection is held by its drainer's `store/read_model.py::Projecting`** (the run, a bench pass, one store append), never the process; every entry point holds the same set.
- **`LiveDashboardProjection` RESOLVES; it never hands the browser scalars to join**: a live row is the closed row's shape, with no `live` flag beside it.
- **A `RoundWarningRecord` is a self-heal only**: a halt is its `StopReason`, a decided outcome a field of its step's record, a projection's fault its `derived_file_stale`, never a raise into the append whose line landed. <!-- probe: git grep -cE "emit_round_warning\(" -- promptpotter/*.py <= 3 -->

## One deleter, one poster, one docker call

- Every recursive delete goes through `store/io.py::rmtree_robust` / `unlink_robust`. <!-- probe: git grep -cE "shutil\.rmtree" -- promptpotter/*.py <= 1 -->
- Every notice this box posts (operator, CRM, trail) goes through `outbound.py::post_json`, the one reader of a non-2xx body as a refusal. <!-- probe: git grep -cE "status_code != 200|is_success" -- promptpotter/presentation/admin_bot.py == 0 -->
- `docker_host.py` is the ONE `docker` CLI call <!-- probe: git grep -cE "^\s+.docker.,$" -- promptpotter/*.py <= 1 -->; taking a machine slot claims the machine (`backend.py::MachineSlots.hold`).
