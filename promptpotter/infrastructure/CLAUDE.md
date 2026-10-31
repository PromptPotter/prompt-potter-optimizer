# infrastructure/ — I/O contracts

**No use case writes to disk or talks to a network except through one of these seams.**

## Persistence — one ingress, two projections

**Ingress is the per-cycle `CycleEventLog`** (`ledger.py`, `cycles/{cycle_id}/.runtime/ledger.jsonl`); **no
campaign fact rides a side file beside it** — a campaign's result is a fold of its line's ledgers. <!-- probe: git grep -cE "result\.json" -- promptpotter/infrastructure/store/layout.py == 0 -->
A fork's file holds only its own appends; the parent's prefix is walked from the cut, never copied.
**Every append to or rewrite of a shared file serializes inside `store/io.py`** — Windows runs `O_APPEND` as
seek-then-write and loses the overwritten record silently. **`store/io.py::take_lock` is the one OS file lock.** <!-- probe: git grep -cE "filelock|msvcrt|fcntl" -- promptpotter/*.py <= 7 -->

**The ledger is a CHRONOLOGY: a field earns its place only if ordering is what makes it findable.** A value
the archive addresses by `(node_configs, sample_key)` does not, which is why a round's close carries each
row's address, never the row.

**A record has no live-only half.** Every field is declared and persisted, so a live fold and a disk fold
agree at every offset; a fact a fold needs is added to the record, never handed past it in memory. <!-- probe: git grep -cE "class RoundStandingRecord" -- promptpotter/domain/run_records.py == 0 -->

**Projections** under `projections/` are newtype-guarded and bound by
`application/run_observers.py::build_run_observers`; a subscriber writes nothing outside its declared
allowlist. `LiveDashboardProjection` writes every spend bucket, keyed by the record's kind, never a
hand-named pair; the audit file is read through `domain/round_audit.py::RoundAudit`.

**`dashboard.json` is a checkout of the fold, and nothing reads it back** — not a served read, not the
fold (one `LedgerFold` in `store/read_model.py::LedgerIndex`). <!-- probe: git grep -cE "\.dashboard\b" -- promptpotter/application/served_dashboard.py == 0 -->
`declared_phase` there is a mirror: run state is `runtime_flags.py::derive_run_state`, off the ledger, and
whether a producer is attached is its lock (`producer_lock.py::cycle_held`). Three guarantees hold at its
writer: it is **always on disk and swapped atomically** (tmp + rename); it **settles within
`_DASHBOARD_DEBOUNCE_S`** of the last event (`DashboardCheckout._settle`); and it is **written at once at
round boundaries** (`_AT_ONCE`). Never relax the swap, drop those flushes, or let the file lag past a
completed round. The public round file has the same atomicity, with
`application/scoring/closed_rounds.py::RoundFileProjection` its sole writer. <!-- probe: git grep -cE "RoundFileProjection\(" -- promptpotter/*.py <= 2 -->

**A round IS its last `RoundStatement`; the round file is a checkout of it.** Only a `RoundClosedRecord` moves
the clock; every resume, fork and read takes standing rounds off the ledger
(`store/campaign_store/ledger_scan.py::scan_standing_rounds`).

**`LiveDashboardProjection` RESOLVES; it never hands the browser scalars to join**: `current_round.round` is
always `state.round`, with no `live` flag beside it; `current_round.nodes` holds only THIS round's optimizer
calls; a live row is the closed row's shape (`domain/dashboard_rows.py`), each field landing when its fact
exists, θ at the election.

**A `RoundWarningRecord` is a self-heal only** — owned by `domain/run_records.py::RoundWarningKind`:
a halt is its `StopReason`, a decided outcome a field of its step's record, neither a
`llm/telemetry.py::emit_round_warning`. <!-- probe: git grep -cE "emit_round_warning\(" -- promptpotter/*.py <= 4 -->

**The SSE highway tails the on-disk ledger cross-process** (`projections/event_stream.py::CycleLedgerTail`),
never a subscriber.

**Every ledger reader takes a line through `store/read_model.py::ledger_record`**: an unreadable line is a
`LedgerUnreadableError`, never read around.

**`--from N` admissibility is a ledger question, and a scan takes the LAST close.** Every
`store/campaign_store/ledger_scan.py::scan_*` lets an `OSError` raise, and none instantiates `CycleEventLog`. <!-- probe: git grep -cE "CycleEventLog\(" -- promptpotter/infrastructure/store/campaign_store/ledger_scan.py == 0 -->

## One deleter — `rmtree_robust`

**Route every recursive delete through `store/io.py::rmtree_robust`** (or `unlink_robust`); **a bare
`shutil.rmtree` is a bug** — it cannot remove trees past Windows `MAX_PATH`, and with `ignore_errors=True`
leaves a half-deleted cycle that reads as real. <!-- probe: git grep -cE "shutil\.rmtree" -- promptpotter/*.py <= 1 -->

## Backend wire and Docker host

- **`backend.py::BackendClient` is connector-agnostic**; wire adapters live in `promptpotter/connectors/`.
- **`docker_host.py` is the ONE `docker` CLI call** <!-- probe: git grep -cE "^\s+.docker.,$" -- promptpotter/*.py <= 1 -->; taking a machine slot claims the machine
  (`backend.py::MachineSlots.hold`).
