# M11 Webapp — Minimal Read-Only Preview

**Status:** Slice 1 archived 2026-05-07. Vanilla `webapp/index.html` carried the read-only preview from 2026-05-05 → 2026-05-07; it has been replaced by the Next.js export at `webapp/` (`/ui` mount). Migration spec + ongoing work live in [`m11-webapp-react-port.md`](m11-webapp-react-port.md). This document is retained as the architectural memo for the read-only surface (API contracts, status thresholds, cycle-pinning behavior) — those carry into the React port unchanged. Further M11 monitoring slices (hard-sample leaderboard, per-searchpoint score histogram, family-tree speciation view, dataset preview on drop) land in the React port.
**Implementation:**
- API: `_active_router` + `/campaigns/{cycle_id}/files` + `/campaigns/{cycle_id}/file` + `/optimizer/pipeline` in `promptpotter/presentation/api.py`.
- Static mount: `app.mount("/ui", StaticFiles(...))` in `promptpotter/main.py`; bundle at `webapp/index.html` (single file, vanilla JS + Chart.js + marked CDNs).
- Tests: `tests/test_api.py` covers active pointer (404 + 200), file listing, file content (json + traversal-reject + 404 + oversize), `/ui/` mount, and the optimizer-pipeline view shape.
- View topology lives in `promptpotter/application/optimization/optimizer_pipeline.json::view` — flat `nodes` (id + label + kind) + `edges` (from/to/kind/label). Positions are **hardcoded in `webapp/index.html`** (`LAYOUT` + `EDGES`); the pipeline shape never changes.

**How the operator runs it:** `python -m uvicorn promptpotter.main:app --port 8001` in one terminal; `python -m promptpotter optimize` in another; open `http://localhost:8001/ui/`. The page polls `dashboard.json` every 2 s. Reload the page to repin after `init`.

**Depends on:** Parity (complete), FastAPI read endpoints in `promptpotter/presentation/api.py` (complete)
**Blocks:** M11 Track 3 full read-only views; M12 webapp Phase 2 (launcher + live monitoring)

---

## Goal

Single static HTML page served by the existing FastAPI app. Renders the **currently active cycle** from `.promptpotter/active_session.json` — no backend list, no campaign picker, no cycle selector. Polls every 2 s; live-updates as `dashboard.json` is rewritten by the optimizer. All files under the cycle dir + family-root telemetry are navigable + inspectable.

Visual target: hand-supplied stub at `C:\Users\dsacc\Downloads\curator_dashboard_with_workflow.html` — Curator-style shell (sidebar, topbar, stat cards, workflow canvas, two-column bottom). Most stub interactions stay as stubs; only the data panels become real.

## Non-goals

- No control plane (no start / stop / fork buttons — those are M12).
- No campaign / backend list view. The page assumes `active_session.json` points to the cycle.
- No SSE / WebSocket. Polling is the live mechanism.
- No build tooling (no Next.js, no React, no bundler). Single HTML + vanilla JS + Chart.js CDN. M11 Track 3's full Next.js scaffold replaces this preview later.
- No write endpoints, no file editing.

---

## Surface map — stub panel → real data

| Stub region | Status | Real source | Endpoint |
|---|---|---|---|
| Sidebar nav (Dashboard, Analytics, Evaluations, Reports, Settings) | Two real, rest stubs | "Dashboard" + "Files" become tabs that swap the main pane. Others render `<disabled>` style. | none |
| Sidebar `+ New Analysis` | Stub (disabled) | — | — |
| Topbar tabs (New Job, View Results, Model Audit, Prompts, Datasets) | Mixed | Spec previously read "first-load active = View Results". Operator-confirmed 2026-05-07: **first-load active is now "New Job"** — deliberate launcher pre-shape ahead of M12 Wave 2's API extensions. The "New Job" tab is M12 Track 3 load-bearing scaffold (chat panel = future user-facing surface for the existing `restructure` optimizer node; wand toggle = canonical control surface). View Results / Model Audit / Prompts / Datasets remain stubs / read-only payloads. | none |
| Topbar `Search analytics…` | Stub | — | — |
| Topbar `Export Data` | Stub | — | — |
| Breadcrumb | Real | `"Cycle » {cycle_id}"` | `GET /api/v1/active` |
| h1 | Real | `dataset_name` from `index.json` (or `cycle_id` if absent) | `GET /api/v1/campaigns/{cycle_id}/file?scope=cycle&path=index.json` |
| Meta (avatar/user/time) | Real | `session {session_id} • updated {wallclock_serialized_at}` | `/active` + `dashboard.json` |
| Stat card 1 — `PROVIDERS` | Repurpose | `BEST` → `dashboard.best` | `dashboard.json` |
| Stat card 2 — `TOTAL TESTS` | Repurpose | `QUERIES` → `dashboard.total_queries_scored` | `dashboard.json` |
| Stat card 3 — `DURATION` | Repurpose | `LAST QUERY` → `dashboard.last_query_elapsed_s` (formatted `s`) | `dashboard.json` |
| Workflow canvas | Real, dynamic | Pipeline node graph: `l1_generate → l1_critique → l2_context → l3_plan → restructure`, current node highlighted from `dashboard.phase` / `dashboard.layer`. Drop the diamond/circle sub-nodes from the stub — replace with the real 5-node optimizer pipeline. | `dashboard.json` (`current_round.nodes`) |
| Workflow selection panel | Real | When a node is clicked: show `input.template_name`, `usage.total_tokens`, `duration_s`, `model`, `timestamp`. | `dashboard.json` (`current_round.nodes.{node_id}`) |
| Pass Rate card (3 horizontal bars) | Real | Top-N candidates this round: `composite_fitness` per candidate, label = candidate name. Color stub keeps `#185FA5 / #BA7517` for above/below origin. | `dashboard.json` (`current_round.candidates[]`) — fall back to log.md parse if not on dashboard |
| Score Frequency density chart | Real | Per-sample composite score histogram for the current best candidate. | `rounds/round_NNNN.json` (most recent) |
| Prompt Score Trends line chart | Real | Per-round `best` and `current_acc` lines across all rounds. | Iterate `rounds/round_*.json` |
| Prompt Variables card (editable text) | Real, read-only | Show `current_round.nodes.l1_generate.input.template_fields.problem_description` truncated. Disable `contenteditable`. | `dashboard.json` |
| Evaluation Outputs table | Real | Per-sample HIT/MISS for the current best candidate this round. | `rounds/round_NNNN.json` |
| Evaluation Outputs `+ Add row` / `Analyze ↗` | Stubs | Visual only. | — |
| **NEW: Files pane** (sidebar nav switch) | New | Recursive tree of cycle dir + family-root files. Click a file → right-pane viewer (JSON pretty-printed, .md rendered with `marked`, .log monospace, others = "preview unavailable"). | `GET /campaigns/{cycle_id}/files` + `/file` |

The stub references `var(--color-background-primary)` etc. with no theme — the implementer must inject Anthropic-style design tokens at the top of the page (light-theme defaults are fine for the preview):
```css
:root{
  --color-background-primary:#fff; --color-background-secondary:#F7F7F5; --color-background-tertiary:#F0EFEC;
  --color-text-primary:#181818; --color-text-secondary:#666; --color-text-tertiary:#999;
  --color-border-tertiary:#E5E4E0;
  --border-radius-md:6px; --border-radius-lg:10px;
  --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
```

---

## API additions

Three new endpoints in `promptpotter/presentation/api.py`. Mount under existing `/api/v1` prefix.

### 1. `GET /active` — active session pointer

```python
class ActiveSessionResponse(BaseModel):
    tenant_id: str
    session_id: str
    cycle_id: str
```

Reads via `promptpotter.infrastructure.store.read_active_pointer()` (re-exported from the package; already imported across the codebase). 404 when pointer is missing/empty (`tenant_id == ""`). Use a new `_active_router = APIRouter(tags=["Active"])`, register in `main.py` at `/api/v1`.

### 2. `GET /campaigns/{cycle_id}/files` — recursive file listing

```python
class FileEntry(BaseModel):
    path: str         # relative to scope root, forward slashes
    scope: Literal["cycle", "family"]
    size: int
    mtime: str        # ISO 8601 UTC

class FilesResponse(BaseModel):
    cycle_id: str
    is_fork: bool
    entries: list[FileEntry]
```

Walk:
- `campaign_dir_for(store.base_dir, cycle_id)` → all entries with `scope="cycle"`.
- If `is_fork` (i.e. `root_dir != campaign_dir`), additionally walk family-root file-level artifacts (`dashboard.json`, `output.log` per `tests/test_invariants.py::ROOT_TELEMETRY_ARTIFACTS`) with `scope="family"`.

Skip dotfiles (`.cache/` is included — operator wants per-round audit visible). Sort: directories grouped, then alphabetical. Cap entries at 5000 — return 413 above (no real cycle should hit this; guard against accidents).

### 3. `GET /campaigns/{cycle_id}/file?scope=...&path=...` — file content

```python
class FileContentResponse(BaseModel):
    cycle_id: str
    scope: Literal["cycle", "family"]
    path: str
    size: int
    mtime: str
    content_type: Literal["json", "markdown", "log", "text", "binary"]
    content: str | None    # None when binary or oversized; UTF-8 text otherwise
```

Path safety:
- Reject if `path == ""` or contains `..`, `\`, leading `/`.
- Build `resolved = (scope_root / path).resolve()` and verify `resolved.is_relative_to(scope_root_resolved)` — raise 400 otherwise.
- Verify `resolved.is_file()` — 404 otherwise.

Content gating:
- `path.suffix == ".json"` → `content_type="json"`, content = raw text (frontend pretty-prints).
- `path.suffix == ".md"` → `content_type="markdown"`.
- `path.suffix == ".log"` → `content_type="log"`.
- Other text-decodable suffixes (`.txt`, `.jsonl`, no suffix) → try UTF-8 decode; on failure mark `binary`.
- Size > 2 MiB → `content=None`, `content_type="text"`, frontend shows "preview truncated".

---

## Static mount

In `promptpotter/main.py`:
```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path
WEBAPP_DIR = Path(__file__).resolve().parents[1] / "webapp"
if WEBAPP_DIR.exists():
    app.mount("/ui", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
```

Operator loads `http://localhost:8001/ui/`. Single file at `webapp/index.html`; optional `webapp/app.js` and `webapp/style.css` if the implementer prefers split files (the stub keeps it monolithic — fine to mirror).

CORS already permissive in `main.py`; same-origin so no preflight matters.

---

## Live update strategy

Single `setInterval(refresh, 2000)`:
1. `GET /api/v1/campaigns/{cycle_id}/file?scope=cycle&path=dashboard.json` → re-render stat cards, workflow node states, prompt variables, breadcrumb timestamp.
2. If `dashboard.round` changed since last tick → fetch newest `rounds/round_{round-1}.json` for the trends chart + score-frequency chart + evaluation table.
3. Files pane: refresh on tab open + on user click of "↻", not on the polling tick.

`active_session.json` is read once on page load — implementer can cache `cycle_id` in memory. If the operator forks / starts a new cycle the page must be reloaded; that's acceptable for the preview.

Use `If-None-Match` / `Last-Modified` only if it lands without effort; otherwise polling is cheap enough.

---

## File-tree UX

Sidebar nav has two clickable items (`Dashboard`, `Files`) and four stubs. Clicking `Files` swaps the entire `.content` region for:

```
┌────────────────────────────┬──────────────────────────────────────┐
│ TREE                       │ VIEWER                               │
│ ▾ cycle (root)             │ rounds/round_0001.json               │
│   dashboard.json           │ ─────────────────────────────────    │
│   index.json               │ {                                    │
│   log.md                   │   "round": 1,                        │
│   review.md                │   "candidates": [ ... ]              │
│   ▾ rounds/                │ }                                    │
│     round_0000.json        │                                      │
│     round_0001.json        │                                      │
│   ▾ prompts/               │                                      │
│   ▾ langfuse/              │                                      │
└────────────────────────────┴──────────────────────────────────────┘
```

Tree is collapsible. JSON viewer = `<pre>` with 2-space pretty-print (no syntax highlighting). Markdown viewer = `marked.parse()`. `.log` = `<pre>` monospace.

For forks, family-root files appear under a sibling top-level node `▾ family (telemetry)`.

---

## Implementation steps

1. **API endpoints** (`promptpotter/presentation/api.py`): add the three endpoints + Pydantic models. Register `_active_router` in `main.py`. Re-use `_open_cycle_ledger_or_404` pattern for path validation around `campaign_dir_for`/`root_dir_for`.
2. **Static mount** (`main.py`): add the `StaticFiles` mount block. `webapp/` may not exist on disk yet — the `if exists` guard keeps tests happy.
3. **`webapp/index.html`**: start from the stub. Strip stub-only sections (Curator branding stays as a placeholder; operator can rebrand later). Replace hardcoded `nodes` / `edges` with the real 5-node optimizer pipeline. Replace `nodeInfo` static data with a function that reads `dashboard.current_round.nodes`. Replace `freqChart` / `trendChart` data sources with fetched values. Inject the design-tokens block at the top.
4. **Tests** (`tests/test_api.py`): one test per endpoint —
   - `/active`: 404 when `active_session.json` missing; 200 with right shape when present.
   - `/files`: lists `dashboard.json`, `log.md`, `rounds/round_0000.json` for a fixture cycle.
   - `/file`: rejects `..`, `/abs`, missing file (404), oversize (mock 3 MiB, expect `content=None`).
   - Mount: `GET /ui/` returns 200 with HTML when `webapp/index.html` exists; 404 otherwise.
   - **No** test of the rendered DOM. The HTML is a preview slice; full Next.js coverage lands later.
5. **Allowlist update** (`tests/test_invariants.py`): no campaign artifact changes — webapp does not write. No allowlist update needed.
6. **Roadmap entry**: bump `m11-publication-benchmarks.md` Track 3 with a "Slice 1: minimal preview shipped" note.

---

## Pre-reading for the implementer

- The HTML stub: `C:\Users\dsacc\Downloads\curator_dashboard_with_workflow.html`
- Existing API: `promptpotter/presentation/api.py` (read end-to-end; new endpoints sit at the bottom alongside the existing per-cycle ones).
- Active pointer: `promptpotter/infrastructure/store/__init__.py` (use `read_active_pointer`, don't reach into the file directly).
- Path helpers: `promptpotter/infrastructure/store/paths.py` (`campaign_dir_for`, `root_dir_for`, `validate_path_component`).
- Fixture cycle for testing: `.promptpotter/projects/default/campaigns/cycle_0a7a7c410aa7/` has the full layout (dashboard.json, index.json, log.md, review.md, rounds/, prompts/, langfuse/).
- `dashboard.json` shape — top-level keys to render:
  - `phase`, `round`, `candidate`, `query`, `patience`, `layer`
  - `origin`, `best`, `current_acc`, `composite_fitness_formula_short`
  - `cycle_id`, `total_queries_scored`, `total_backend_calls`, `last_query_elapsed_s`, `wallclock_serialized_at`
  - `n_variants`, `sp_budget_ttest`
  - `current_round.nodes.{l1_generate|l1_critique|l2_context|l3_plan|restructure}` with `input`, `output`, `usage`, `model`, `config`, `duration_s`, `timestamp`, `round`

---

## Acceptance

- Operator runs `uvicorn promptpotter.main:app --port 8001`, opens `http://localhost:8001/ui/`, sees the active cycle's dashboard live-updating without configuration.
- File-tree nav lets the operator open every file in `campaigns/{active_cycle}/`.
- All four required test families pass.
- No new lint / mypy / deptry errors.
