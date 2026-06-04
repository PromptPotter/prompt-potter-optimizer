# presentation/ — entry-point adapters (read-only over application/)

The thin shells that wire the operator's three entry points (CLI,
notebook, webapp) to one orchestration layer. Per CLAUDE.md root: entry
points MUST NOT write campaign artifacts directly — they call into
`application/` and subscribe to the ledger via projections owned by
`infrastructure/`.

## Layout

| Module | Owns |
|---|---|
| `cli/` | `campaign_runner.py` (the `optimize` shell), `session.py` (`init` shell), `parsers.py` (argparse). Thin shells over `application/runner/` and `application/bootstrap`. **Two write verbs:** `new` (dataset name *or* raw file) + `resume`. The raw-file form is the CLI's origin-ingest path — `new`'s `Path(arg).is_file()` branch (`commands/new.py::_ingest_to_dataset`) parses + origin-resolves the file, then commits via `launcher.py::commit_draft_to_dataset` (the *same* commit step the web `/commands/mint-campaign-from-draft` runs) and falls through to the authored mint+loop. There is no separate `ingest` verb. |
| `views/` | Display formatters: `view_models.py` (frozen view dataclasses), `display.py` (ANSI primitives), `view_ingress.py` (live `PhaseEvent → typed View` ingress via `from_phase_event`; the producer emits the **typed** view on `payload['view']` and Pydantic serializes it byte-identically to disk + SSE — no wire-dict, no reconstruction), `render/` (`to_text` / `to_markdown` dispatchers + heatmap + sweep summary + sp_diff), `live/` (`LiveDisplay` ledger subscriber + per-sample / per-candidate / phase formatters), `notebook_run.py`. Pure data → text/markdown — no I/O outside the file-tree-readable surface. Disk-side reconstruction (`from_disk_round` / `from_disk_log`) lives in `presentation/writers.py` next to its single consumer. |
| `api/` | FastAPI API. `routers/{backends, campaigns/, active, datasets, measurements, verify, commands, auth}` (backend storage, campaign registry + per-cycle live reads, active-session reads, dataset preview + ingest, measurement leverage, diagnostic-run verify, the `POST /commands/{kind}` highway, OIDC auth; `campaigns/` is a package, the rest are modules). `deps.py` has `resolve_identity` → `IdentityDep` → `build_stores_from_identity` → `StoreDep` + `get_backend_or_404`. Stage 1 (M12 OIDC client) replaces only `resolve_identity`; every route keeps consuming `IdentityDep` / `StoreDep` unchanged. `__init__.py` re-exports the eight router objects for `main.py`. |
| `writers.py` | Per-cycle markdown writers (`write_log_md`, `write_review_md`); disk-side view reconstruction (`from_disk_round`, `from_disk_log`) feeds the same `RoundCompleteView` / `LogMdView` shapes the live ingress emits. |

## Out-of-bounds

- **No campaign-artifact writes from entry-point code.** Disk writes go
  through `CycleEventLog.append` (orchestration) or a declared projection
  (display); the per-cycle markdown writers (`log.md`, `review.md`) in
  `writers.py` are the documented exception, audited in §1.
- **No business logic in CLI commands or API handlers.** `cli/` and
  `api/` are thin shells: parse args / route requests, call into
  `application/`, format the result. Business logic that creeps in here
  is drift — push it into `application/`.
- **Notebook + webapp parity.** Three entry points, one orchestration
  layer. A behavior available in CLI but not in the notebook is a
  bug, not a feature; the orchestration layer is meant to be the
  single source.

## Read-only API stance

`api/` is **read-only by design** beyond the explicitly-sanctioned
mutating endpoints. Webapp panels poll `dashboard.json` + a few JSON
endpoints; they don't drive the loop. Adding any other mutating route
that touches **campaign / cycle** state is Control-remote highway
territory and out of charter here.

This is *not* a ban on every mutation in the codebase. **Identity-surface
administration** (editing the sign-in allowlist, provider config) is a
different I/O kind — it is delivered by an **operator-admin channel**
(a deployment-side, outbound-only companion such as
`presentation/admin_bot.py`), **not** an inbound API route and **not**
`/commands/{kind}`. Permanent contract:
[`../../docs/adr/0004-operator-admin-channels.md`](../../docs/adr/0004-operator-admin-channels.md).
Don't reach for the command highway when the right home is the
operator-admin channel.

**Sanctioned mutating endpoints:**

- `POST /commands/{kind}` — the Control-remote highway. Closed inbound
  set declared in `docs/specs/m12-api-openapi.yaml`; sole writer at the
  seam is `CommandDispatcher` (`presentation/api/middleware/`). Every
  command is appended to its target ledger (per-cycle, campaign root
  cycle, or workspace ledger at `projects/{tenant}/.workspace/events.jsonl`)
  as a `CommandRecord`; inline-applied; paired `CommandAckRecord` written
  by the same dispatcher (workspace + lifecycle) or by
  `RunnerCommandSubscriber` (cycle-scoped).
- `POST /datasets/ingest` — multipart CSV upload; returns a server-held `DraftCampaign` (declared in `docs/specs/m12-api-openapi.yaml`; spec at `docs/specs/m13-chat-first-user-web.md § Ingest`). Workspace-scoped, identity-bound; no `CommandRecord` lands on a ledger until the operator commits via the separate `/commands/mint-campaign-from-draft` verb.

## Display constraint

Markdown writes go only to documented paths. Anything emitted to stdout
must also be findable as a file someone (or something) can open later
— per root CLAUDE.md "everything material lives on disk, in
human-readable form."
