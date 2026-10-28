# infrastructure/ — I/O contracts

**No use case writes to disk or talks to a network except through one of these seams.**

## Persistence — one ingress, two projections

**Ingress is the per-cycle `CycleEventLog`** (`ledger.py`, `cycles/{cycle_id}/.runtime/ledger.jsonl`); **no
campaign fact rides a side file beside it** — a campaign's result is a fold of its line's ledgers. <!-- probe: git grep -cE "result\.json" -- promptpotter/infrastructure/store/layout.py == 0 -->
A fork's file holds only its own appends; the parent's prefix is walked from the cut, never copied.
**Every append to or rewrite of a shared file serializes inside `store/io.py`** — Windows runs `O_APPEND` as
seek-then-write and loses the overwritten record silently.

**The ledger is a CHRONOLOGY: a field earns its place only if ordering is what makes it findable.** A value
the archive addresses by `(node_configs, sample_key)` does not, which is why a round's close carries each
row's address, never the row.

**A record has no live-only half.** Every field is declared and persisted, so a live fold and a disk fold
agree at every offset; a fact a fold needs is added to the record, never handed past it in memory. <!-- probe: git grep -cE "class RoundStandingRecord" -- promptpotter/domain/run_records.py == 0 -->

**Projections** under `projections/`, each newtype-guarded:

- `LiveDashboardProjection` → `dashboard.json`: the operator's display surface, and the sole writer of every
  spend bucket (keyed by the record's kind, never a hand-named pair).
- `AuditTrailProjection` → `.runtime/cache/rounds/round_NNNN.json`: each optimizer node's full LLM I/O, read only through `domain/round_audit.py::RoundAudit`.
- `RacingStreamProjection` → `.runtime/streams/round_NNNN_{member}.jsonl`: per-sample race standing, operator-tailable.
- `ReadoutProjection` (`application/views/readout.py`) → `readout.log`: the run readout, bound at every entry point.

**`dashboard.json` is a checkout of the fold, and three guarantees hold at its writer.** No served read parses it. <!-- probe: git grep -cE "\.dashboard\b" -- promptpotter/application/served_dashboard.py == 0 -->
It is **always on disk and swapped atomically** (tmp + rename); it **settles within `_DASHBOARD_DEBOUNCE_S`**
of the last event (`DashboardCheckout._settle`); and it is **written at once at round boundaries** (`_AT_ONCE`).
Never relax the swap, drop those flushes, or let the file lag past a completed round. The public round file
has the same atomicity, with `application/scoring/closed_rounds.py::RoundFileProjection` its sole writer.

**A round IS its last `RoundStatement`; the round file is a checkout of it.** Only a `RoundClosedRecord` moves
the clock; every resume, fork and read takes standing rounds off the ledger
(`store/campaign_store/ledger_scan.py::scan_standing_rounds`). A second writer of round truth is the bug.

**`LiveDashboardProjection` RESOLVES; it never hands the browser scalars to join:**

- `active_node` is served by a `match` total over `DashboardState` (`assert_never`).
- `current_round.round` is always `state.round`; no `live` flag beside it.
- `current_round.nodes` holds only THIS round's optimizer calls; presence is "this node fired".
- A cell naming an unannounced arm raises (`domain/run_records.py::AnnouncedArms.take`).
- A live row is the closed row's shape (`domain/dashboard_rows.py`); each field lands when its fact exists, θ at the election.

**The SSE highway tails the on-disk ledger cross-process** (`projections/event_stream.py::CycleLedgerTail`),
never a subscriber.

**Every cycle owns its stream** at `cycles/{cycle_id}/dashboard.json`. `declared_phase` there is a mirror no
served read parses: run state is `runtime_flags.py::derive_run_state`, off the ledger; whether a producer is
attached is its lock (`producer_lock.py::cycle_held`) and nothing else.

**Nothing reads that file back.** The fold is one `LedgerFold` in the ledger's index
(`store/read_model.py::LedgerIndex`). **Every ledger reader takes a line through ONE policy**
(`store/read_model.py::ledger_record`): an unreadable line is a `LedgerUnreadableError`, never read around.

**A subscriber writes nothing outside its declared allowlist** — `Projection.on_record` fails loud.

**`--from N` admissibility is a ledger question, and a scan takes the LAST close.** Every
`store/campaign_store/ledger_scan.py::scan_*` lets an `OSError` raise, and none instantiates `CycleEventLog`.

## One deleter — `rmtree_robust`

**Route every recursive delete through `store/io.py::rmtree_robust`** (or `unlink_robust`); **a bare
`shutil.rmtree` here is a bug** — it cannot remove trees past Windows `MAX_PATH`, and with `ignore_errors=True`
leaves a half-deleted cycle that reads as real.

## Backend wire and Docker host

- **`backend.py::BackendClient` is connector-agnostic**; wire adapters live in `promptpotter/connectors/`.
- **`docker_host.py` is the ONE `docker` CLI call.** Taking a machine slot claims the machine
  (`backend.py::MachineSlots.hold`); a connector starts a container only through `run_cell_container`.
