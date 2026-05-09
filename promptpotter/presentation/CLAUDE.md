# presentation/ — entry-point adapters (read-only over application/)

The thin shells that wire the operator's three entry points (CLI,
notebook, webapp) to one orchestration layer. Per CLAUDE.md root: entry
points MUST NOT write campaign artifacts directly — they call into
`application/` and subscribe to the ledger via projections owned by
`infrastructure/`.

## Layout

| Module | Owns |
|---|---|
| `cli/` | `campaign_runner.py` (the `optimize` shell), `session.py` (`init` shell), `parsers.py` (argparse). Thin shells over `application/runner.py` and `application/bootstrap`. |
| `views/` | Display formatters: `view_models.py` (frozen view dataclasses), `display.py` (ANSI primitives), `view_ingress.py` (live `PhaseEvent → typed View → wire dict` ingress + `view_from_record` reconstruction), `render.py` (`to_text` / `to_markdown` dispatchers + heatmap + sweep summary), `live.py` (`LiveDisplay` ledger subscriber + per-sample / per-candidate formatters), `notebook_run.py`. Pure data → text/markdown — no I/O outside the file-tree-readable surface. Disk-side reconstruction (`from_disk_round` / `from_disk_log`) lives in `presentation/writers.py` next to its single consumer. |
| `api.py` | FastAPI read-only API: backend storage, campaign registry, per-cycle live reads (dashboard passthrough, log.md, ledger reads + filtered views). |
| `writers.py` | Per-cycle markdown writers (`write_log_md`, `write_review_md`); disk-side view reconstruction (`from_disk_round`, `from_disk_log`) feeds the same `RoundCompleteView` / `LogMdView` shapes the live ingress emits. |

## Out-of-bounds

- **No campaign-artifact writes from entry-point code.** Disk writes go
  through `CycleEventLog.append` (orchestration) or a declared projection
  (display); the per-cycle markdown writers (`log.md`, `review.md`) in
  `writers.py` are the documented exception, audited in §1.
- **No business logic in CLI commands or API handlers.** `cli/` and
  `api.py` are thin shells: parse args / route requests, call into
  `application/`, format the result. Business logic that creeps in here
  is drift — push it into `application/`.
- **Notebook + webapp parity.** Three entry points, one orchestration
  layer. A behavior available in CLI but not in the notebook is a
  bug, not a feature; the orchestration layer is meant to be the
  single source.

## Read-only API stance

`api.py` is **read-only by design** beyond the explicitly-sanctioned
mutating endpoints (e.g. backend registration, dataset preview).
Webapp panels poll `dashboard.json` + a few JSON endpoints; they don't
control the loop. Control-plane work (start / stop / steer a cycle
remotely) is M12 daemon territory and out of charter here.

## Display constraint

Markdown writes go only to documented paths. Anything emitted to stdout
must also be findable as a file someone (or something) can open later
— per root CLAUDE.md "everything material lives on disk, in
human-readable form."
