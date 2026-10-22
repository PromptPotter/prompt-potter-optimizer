# infrastructure/ — I/O contracts

Persistence, LLM clients, backend wire, projections, tracing. No use case writes to disk
or talks to a network except through one of these seams.

## Subdirectory contracts

| Subdirectory | Owns | CLAUDE.md |
|---|---|---|
| `store/` | `Stores`, the lineage tree, the archive's scope, the two dataset tiers, which JSON reader. | [`store/CLAUDE.md`](store/CLAUDE.md) |
| `llm/` | The LLM client: admission, provider and route, what a model accepts. | [`llm/CLAUDE.md`](llm/CLAUDE.md) |
| `tracing/` | Fan-out sinks off the ledger, dormant on purpose. | [`tracing/CLAUDE.md`](tracing/CLAUDE.md) |
| `identity/` | The OIDC foundation. | [`identity/CLAUDE.md`](identity/CLAUDE.md) |

## Persistence — one ingress, two projections

**Ingress:** the per-cycle `CycleEventLog` (`ledger.py`, `cycles/{cycle_id}/.runtime/ledger.jsonl`). **No campaign fact rides a side file beside it** — a campaign's result is a fold of its line's ledgers. <!-- probe: git grep -cE "result\.json" -- promptpotter/infrastructure/store/layout.py == 0 --> The writer-side API above it is `RunCallbacks` (`application/run_callbacks.py`), a typed event constructor over `CycleEventLog.append`. A fork's file holds only its own appends: the parent's prefix is WALKED from the cut its `CycleMintedRecord` names, never copied. `append`, like every append to or rewrite of a shared file, serializes inside `store/io.py` (`append_line`, `rewrite_lines`, `replace_lines`): Windows runs `O_APPEND` as seek-then-write, and the record overwritten is lost without an error. It is not crash-atomic, and a rewrite goes tmp + `os.replace` keeping the line count, since the line index IS `sequence`.

Per-call telemetry firing from deep inside the dispatch chain uses the `emit_*` shape instead (`llm/telemetry.py`) — same canonical ledger. **Which shape a new surface takes** — owned by [`../application/CLAUDE.md`](../application/CLAUDE.md) § Conventions.

**The ledger is a CHRONOLOGY, and a payload earns its place only by needing one.** The test
for a field is *is the ordering what makes it findable?* A value the
archive holds addressed by `(node_configs, sample_key)` is already addressable without it —
which is why a round's close carries each row's ADDRESS and never the row. The projection at the writer
(`RunCallbacks` → `domain/scoring.py::GradedCell.ledger_wire`, `ViewContext.anchors`) keeps a record to
the union of what its subscribers RENDER.

**A record has no live-only half.** Every field is declared and persisted, so a subscriber folding live and a reader folding off disk hold the same value at every offset; a round is ONE record, its `RoundClosedRecord`, which carries where the run stands after it. <!-- probe: git grep -cE "class RoundStandingRecord" -- promptpotter/domain/run_records.py == 0 --> A fact one fold needs and the record lacks is added to the record — never handed past it in memory, where a resume sees nothing: no error, just zeros, and budget re-spent. `application/maintenance/restamp.py::compact_cycle_ledgers` re-projects written records by CALLING the writer's projections, and lifts no retired shape across.

**Newtype-guarded projections** under `projections/`:

| Projection | Scope | Writes | Role |
|---|---|---|---|
| `LiveDashboardProjection` (`projections/live_dashboard/projection.py`) | per cycle | `dashboard.json` | **Display surface** — completed-round summaries (`dash.rounds[]`; **round 0 = the origin's round-0 score**, a one-candidate round emitted via the standard `close_round` path, no separate origin block) + in-flight `current_round` block + `spend` rollup (sole writer for every bucket via `_handle_token_usage` → `SpendRollup.bank`, which keys one by the record's kind and folds the totals over `SpendRollup.by_kind` — never a hand-named pair, or a new spend kind is money the cap cannot see; a run's spend book is seeded off its own cycle's `CampaignStore.cycle_metered`, in the units its campaign's ceiling meters, when it is armed). Sole webapp source for the chart and trend sparkline; the lineage tree reads the ledger instead. |
| `AuditTrailProjection` (`projections/audit_trail.py`) | per cycle / fork | `.runtime/cache/rounds/round_NNNN.json` | **Deep audit** — each optimizer node's full LLM I/O and the round's warnings; the file IS `domain/round_audit.py::RoundAudit` dumped, and every reader loads it through that model. Fetched lazily by the webapp (`useRoundNodes`) only when an operator drills into a specific round; `useRound` is the peer hook for the round itself, served typed off the ledger (`GET …/cycles/{cycle_id}/rounds/{round_num}`) — never the round-file checkout. |
| `RacingStreamProjection` (`projections/racing_stream.py`) | per cycle | `.runtime/streams/round_NNNN_{member}.jsonl` | Per-sample race standing under the eliminator `member` names, for post-hoc posterior analysis. Operator-tailable; webapp does not consume it. |
| `ReadoutProjection` (`application/views/readout.py` — it renders through `views/render/`, so it sits a layer up) | per cycle / fork | `readout.log` | **The run readout** — the ledger stream as lines, ANSI-stripped, appended per launch. Bound at every entry point; one with a terminal hands in a line sink and sees the styled line too. |

**`dashboard.json` is an operator surface, and three guarantees hold at the writer.** The served read also takes it as the head state (`application/served_dashboard.py::_head_state`), so today it is a cache of the ledger fold too. <!-- probe: git grep -cE "def _head_state" -- promptpotter/application/served_dashboard.py == 0 -->
An operator opening the file tree mid-run must see the truth: before deferring or skipping
a write, answer whether they still can — and a SERVED read rests on the same guarantees
(a served dashboard is this file, validated; a cell never is). It is **always on disk and always swapped atomically**
(tmp + rename — never a partial write or a torn read), present after any ledger event in the cycle.
It **settles within `_DASHBOARD_DEBOUNCE_S` of the last event** (`infrastructure/projections/live_dashboard/projection.py::_schedule_persist`
coalesces bursts). And it flushes **immediately, with no debounce, at round boundaries**
(`infrastructure/projections/live_dashboard/projection.py::_flush_pending_persist`), so a round's file is current before the next begins.
Do not relax the swap, remove those flushes, or add a path that lets the file lag past a completed
round. The public round file carries the same atomicity, with
`application/scoring/closed_rounds.py::RoundFileProjection` the tree's sole writer — a ledger subscriber,
so a close, a restated state and a rewind each move it from the record. The model **is** the
round file, and it has no reader in this package or above it: the file is the operator's.

**A round IS its last `RoundClosedRecord`, and the round file is a checkout of it.** The close
carries the whole outcome and the archive address of every row; a resume, a fork, the index and
every served or rendered read take the rounds that STAND off the ledger chain (`infrastructure/store/campaign_store/ledger_scan.py::scan_standing_rounds`, rows
through `application/scoring/closed_rounds.py::closed_rounds`), never off the round files. A second writer of round truth — a file a
resume reads, a copy a fork carries — is the bug.

**`LiveDashboardProjection` RESOLVES; it does not hand the browser scalars to join** from facts written on different ledger events. Five rules, each a field or a filter rather than a convention:

- **`active_node` is served**, by a `match` TOTAL over `DashboardState` (`assert_never`), naming the nodes the ledger recorded at `propose:enter` / `measure:enter` / an optimizer step's enter rather than any optimizer's literal. A partial answer does not fail loudly; it means "nothing is running", which is a lie for every state it omits.
- **`current_round.round` is `state.round`, always**, so a reader selects this block over the audit twin by equality. There is deliberately no `live` flag beside it.
- **`current_round.nodes` holds only THIS round's optimizer calls.** `_sticky_llm_calls` is most-recent-fire-per-slot and survives round transitions, so it is filtered by the round each block fired in: presence in the served map is the client's whole definition of "this node has fired". The measurement is not a node block: its tape and searchpoints ride `current_round.candidates`.
- **A pass no arm walks moves the RUN's scalars and not the ROUND's population** — it counts as queries scored and drives the in-flight markers. A cell naming an arm no `ArmRecord` announced raises (`domain/run_records.py::AnnouncedArms.take`), so every writer announces an arm before its first cell, in the cycle file that cell lands in — the SSE tail and the ray fold one file, and `RoundEnteredRecord` displaces every fold's arms from its round on.
- **A live row is the same shape as a closed one** — `DashboardCandidate` and `DashboardSample`, both `domain/dashboard_rows.py`; `LiveCandidate` only adds what a closed round keeps in its round file. Two shapes for one entity force the client to merge them field by field. **Each field lands at the moment its FACT exists, and none of them is the round close:** the value and its band ride the scoring gateway's own fold (`search_point_scorer::_composite`) on every sample, so the whisker widens with the bar, while the crown, θ and the matched-parent lift ride `ElectionRecord`. θ cannot come sooner and its nullness before the election is a fact rather than a delay — the δ scale it is read on is fixed at measure start, when the round's arms join what it judges (`application/bench/difficulty.py::DifficultyView.judge`).

The **outbound SSE highway is NOT a projection/subscriber** — it *tails* the on-disk
ledger (`projections/event_stream.py::CycleLedgerTail`), **cross-process**, so the stream
does not depend on the run living in the reader's own process. Contract:
[`docs/developer/event-stream.md`](../../docs/developer/event-stream.md).

**Every cycle — root, fork, diag — owns its live stream** at
`cycles/{cycle_id}/dashboard.json`, stamped with its own id; a fork's view can never
surface the parent's; read sites serve the viewed cycle's own file — no `root_cycle_id` collapse. **`dashboard.json::declared_phase` is a MIRROR of the runner's
declaration and no served read parses it.** The declaration is a ledger record (`RunPhaseRecord`),
and `runtime_flags.py::derive_run_state` — the ONE function every surface and every guard is
answered from, phase and producer (`ProducerReading`) together — reads
the last one off the ledger index. Its only writer lives in the process that dies, so served raw
it reads `running` forever after a kill; `run_phase` is `ServedDashboard`'s
(`application/served_dashboard.py`) — the fold's served facts (`DashboardFacts`; what
`LiveDashboardState` declares beside them is an input to a served reading and is not served) plus
what only a read can say, built by one function for the head and a replay alike, and never on disk.
**Whether a producer is attached is its lock on the cycle (`producer_lock.py::cycle_held`) and
nothing else**; file time only grades an attached one. That grade's edge moves with the CLOCK, not
with a write, so it is expressed once (`_beat_stale_after`) and the conditional-GET validator
reads it from there — a 304 computed off a second copy outlives the answer it stands for.

**Seeding from that file may not be able to fail the run, so nothing seeds from it.**
A launch's projection IS the fold of the cycle's LEDGER chain (`materializing_over`), each
`RunWiringRecord` clearing what the launch before it left and every stamp a record's own
timestamp, so a refold reproduces the file at every offset — a projection is never an input to itself,
and no field on this model is on-disk shape anyone must migrate. The one reader that DOES parse the
file (`application/served_dashboard.py::_head_state`; the model is `extra="forbid"`) drops a state that does
not read as this build's WHOLE and loudly and refolds the ledger (`infrastructure/projections/live_dashboard/projection.py::fold_at`) —
never salvaged field by field: a partial read is a compatibility shim, and the ledger is the
truth. **What a cycle runs under is a ledger record too** (`RunWiringRecord`, declared at the
mint and again by each launch — `application/run_observers.py::declare_run_wiring`), so the
fold needs nothing a prior file carried, and **a state exists only from that record on**: a
replay to a moment before it (inside the check-in a root's moved-in draft ledger opens with)
folds to `None` and serves `WarmingDashboard`.

`Projection.on_record` (`projections/base.py`) owns the dispatch. **Subscribers MUST NOT write campaign artifacts beyond their declared allowlist** — it fails loud, since an out-of-allowlist write shows up in the file tree ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

**`--from N` admissibility is that same LEDGER question, never a `cycles/{cycle_id}/rounds/` tree question** — and a round can close more than once, so a scan takes the LAST close. **Every `store/campaign_store/ledger_scan.py::scan_*` lets an `OSError` past an absent file raise** — "unreadable" answering "nothing stands" has a resume or rewind act on an empty history — and none instantiates `CycleEventLog`, so no subscriber fires during a check.

## One deleter — `rmtree_robust`

**Route every recursive delete through `store/io.py::rmtree_robust`** (or
`unlink_robust`, its by-arity sibling); **a bare `shutil.rmtree` in this package is a
bug.** It cannot remove the trees this package writes — an L4 inner sandbox nests paths past
Windows `MAX_PATH=260` — and with `ignore_errors=True` it fails *silently*, leaving a
half-deleted cycle that later reads as a real one.

## Backend wire

`backend.py`: `BackendClient` is connector-agnostic; per-connector wire
adapters live in `promptpotter/connectors/`.

## Docker host

`docker_host.py` is the ONE `docker` CLI call, daemon probe, package cache, labelled cell
container, producer scratch and dead-producer sweep. **Taking a machine slot claims the machine**
(`backend.py::MachineSlots.hold`), so a connector calls no claim: it declares `compose_overlay`,
keeps scratch under `PRODUCER_SCRATCH` and starts a container of its own only through
`run_cell_container`.
