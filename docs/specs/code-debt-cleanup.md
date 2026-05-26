# Code-Debt Cleanup — Backlog

**Scope is literal: code debt only.** Dead code, redundant guards,
single-caller indirections, premature optimizations that no longer
earn their keep, vibe-coded scaffolding. The default action on every
entry is **delete** (or inline, or strip) — verify-first when the
evidence isn't on disk.

**Not debt — goes elsewhere:**
- Forward-looking webapp perf / feature work → [`m12-plus-backlog.md` § Webapp Perf](m12-plus-backlog.md)
- New milestones / specs → `docs/specs/`, indexed at [`CLAUDE.md`](CLAUDE.md)
- Architectural decisions → `docs/architecture.md`

This file is the dump location for new debt as it's found. Add a bullet under **Active backlog** with enough detail that a future session can pick it up cold:
- file + line range (or symbol)
- one sentence on *why* it's debt (not "what" — the code shows what)
- proposed action (delete / inline / extract / replace / verify)
- any blockers (needs telemetry, needs a mini-spec, depends on another item)

When an item ships, delete it from the file. The file is the live
backlog, not a history log — `git log` is the history layer.

## Active backlog

Lens: **vibe-coded remainder** — LLM-autopilot residue from
AI-assisted iteration the recent polish arc didn't catch. High
confidence after verification (call sites traced + bodies read), not
"I spotted a code smell." See § Audit guidance below for the
patterns.

- **`SampleIndex._cache_records` cache — verify first** —
  `application/intelligence/indexes/sample.py:58, 123, 186-207`.
  Memoizes the `records()` list between digest calls. The real cost
  inside `records()` is the per-sample `_dominant_failure_mode`
  (Counter aggregation), which the list cache doesn't help with.
  Suspected savings: ~2 list rebuilds per cycle (cheap).
  **Blocker:** instrument before acting. **Recipe:** add transient
  `print(f"[cache] sample.records hit={cache_hit} cost_us={dt*1e6:.1f}")`
  inside `records()` around the cache-check + the per-record build
  loop; capture across one full M10 campaign on a representative
  dataset. **Decision rule:** if `hit_rate × per_call_cost <
  1ms/cycle` → delete the cache; if the per-record Counter
  aggregation dominates → memoize `_dominant_failure_mode`
  per-sample instead of caching the whole list.
  **Pattern:** premature optimization (verify-first).

- **`"use client"` audit — continue the sweep** —
  `webapp/components/**/*.tsx`. App Router server components are
  unused; stripping the directive on browser-API-free leaves keeps
  that code out of the client bundle. First sweep done 2026-05-26
  (`card.tsx`, `FitnessRankSummary.tsx`, `states.tsx` stripped;
  `icons.tsx` was already clean; `MeasHeatCell.tsx` kept —
  `useState`/`useEffect`/`useRef`/`PointerEvent`). **Action:** continue
  leaf-by-leaf across the other ~60 `.tsx` files. **Note:** bundle-
  size impact is zero when every consumer is itself client (the leaf
  gets pulled into a client subtree anyway) — the win is correctness-
  of-declaration, not bundle bytes.
  **Pattern:** underused framework capability.

- **Dashboard route re-parses on every non-304 hit — verify first** —
  `promptpotter/presentation/api/routers/campaigns/cycles.py::get_cycle_dashboard`.
  After the 304 short-circuit, hits still `read_text` +
  `json.loads` on the whole file. Cache the parsed dict in-process
  keyed on `(path, mtime_ns)`. Sub-millisecond per hit at current
  ~90 KB; only matters if dashboard grows materially. **Blocker:**
  measure first; likely below noise floor today. **Recipe:** add an
  `X-Parse-Us` response header carrying `(t_after_load - t_before_load) * 1e6`
  on every non-304 path; capture across one full M10 campaign. **Decision
  rule:** act only if median crosses ~1ms or p99 crosses ~5ms.
  **Pattern:** premature optimization (verify-first).

- **TermNorm backend reports a provider slug, not a model** — backend
  `dashboard.json::spend.backend.model = "openrouter"` is the provider,
  not the actual upstream model (e.g. `mistralai/mistral-7b-instruct`).
  Without the real model on the wire, $ for backend usage cannot be
  derived from `shared.spend.lookup_rate(model)` × tokens; the
  Account modal's Activity pane back-fills $ from
  `dashboard.json::spend.backend.total_usd` instead. **Action:** wire
  TermNorm's per-request response to carry the upstream `model` string
  (cross-repo edit at the sibling backend
  `C:\Users\dsacc\OfficeAddinApps\TermNorm-excel\backend-api`). Once
  the wire carries `model`, drop the `_synth_legacy_backend_record`
  back-fill in `presentation/api/routers/auth.py`.
  **Pattern:** missing telemetry field at the wire boundary.

<!-- round_summary.py + factory.py revisit (2026-05-26): both KEEP.
  round_summary.py = named Python→Pydantic adapter
  (RoundResult → RoundSummary); inlining would push raw
  RoundSummaryCandidate(...) constructor calls into _handle_phase
  (wrong abstraction layer in a 920-line projection class).
  factory.py = resume-time disk-reconciliation; for_session docstring
  explicitly commits the classmethod to "thin assembly", and
  resolve_resume_state's stale-pointer healing (_max_round_on_disk +
  prior-state merge) is a named concern that earns its own file. -->

## Audit guidance — what to hunt for

The bar for entries here is **high confidence after verification**,
not "I spotted a code smell." Generic-smell audits flood the backlog
with debatable items. These six patterns merit deletion, each with a
precedent from the closed arc.

### Pattern: premature optimization with apologetic docstring
Code that protects against a scenario that doesn't actually occur,
often hedged by a comment ("for perf", "cached because", "in case
the schema changes"). Verify by reading call sites + measuring
hit-rate / fire-rate. If the protected scenario provably can't
happen, or fires never/rarely on real campaigns, it's debt.
**Precedents (deleted):** `_apply_budget` shed allocator (fired
only when composed prompts exceeded 10k chars; real composed
prompts capped at ~4.7k mandatory + ~3k static = under 8k);
`catalogues.py` global pipeline-param cache (one-entry, sub-ms
render).

### Pattern: redundant double-protection
Two guards on the same condition where one strictly subsumes the
other. Verify by writing the decision boundaries (e.g. two-sided
95% CI: z=1.96 vs one-sided ε=0.05: z=1.645) and confirm one
swallows the other's legitimate cases. **Precedent (deleted):**
PoBB separability floor sitting on top of the Bayesian gate
(strictly stricter; swallowed every mid-budget abort the gate
wanted to fire).

### Pattern: single-caller indirection without architectural reason
Modules / helpers / classes consumed by exactly one caller, with no
test of their own + no layer-boundary justification. Skip splits
that cross a load-bearing layer
(`application/intelligence/ ↮ application/optimization/` per the
invariant) or have their own dedicated test in `tests/`.
**Precedents (inlined):** `l2_driver.py` + `l3_driver.py` →
`executor.py`; audit-1.C `candidate_block` + `score` + `sample` +
`pobb` → `view.py`.

### Pattern: dead exception paths / dead enum variants
Enum members + their handler arms left behind after the code path
that raised them was deleted. Verify by `grep` for every variant —
if the only references are the enum definition + handler arms with
no `raise` / construction site, the variant is debt. **Precedent
(deleted):** `StopReason.PROMPT_BUDGET` after `_apply_budget`
removal.

### Pattern: speculative API surface
Parameters accepted but never read; optional return types `X | None`
where every return is non-None; default kwargs no caller overrides;
Pydantic / dataclass fields declared but never populated. Verify by
tracing call sites + reading the body. **Precedent (deleted):**
`L1Variant.target_axis` + `.reasoning` — the docstring claimed
"persisted in the audit trail but doesn't read them at runtime,"
but l1_behavior validators substring-matched them as
peaked-axis / rebut signals. Resolved by routing both signals
through `pipeline_params_override` keys + `changes_description` +
the citation string, then deleting the fields.

### Pattern: vibe-coded scaffolding
Half-finished branches behind `raise NotImplementedError`, enum
variants promising dynamism the system never delivers, comments
referring to future work the project doesn't plan to build. The
root `CLAUDE.md` is explicit: "Document current state, not
half-done plans." **Verify the "future" actually isn't on the
roadmap before flagging** — `ForkTrigger.L2_REBASE` / `L3_REBASE` /
`OPERATOR_REWIND` looked like vibe-coded scaffolding behind a
`NotImplementedError` branch, but `m10-prompt-iteration-framework.md`
explicitly schedules them for wiring. They're now active backlog
("Wire rebase emission") instead of a delete candidate.

### Anti-patterns to skip
These are NOT debt — skip on sight:
- Intentional UI placeholders for M13+ (see § below)
- Per-injection `char_cap` (LLM-overrun truncation; real boundary
  guard)
- Domain vocabulary policed elsewhere (`origin` not `baseline`,
  `sample` not `query`)
- Layer-invariant splits (`application/intelligence/` ↮
  `application/optimization/`)
- ABC `@abstractmethod` / `Protocol` `...` bodies
- `from __future__ import annotations` (standard PEP 563)
- Boundary guards at external-input sites (file I/O, JSON ingest)
- Validators on user-config Pydantic models with `extra='forbid'`
- `_*` private helpers used by exactly one caller in the same file
  (intra-file decomposition isn't inter-file indirection)

### Next-round audit angles
The closed arc + the current backlog drained the obvious vibe-coded
classes. Remaining productive angles for future re-audits:
1. **`dict[str, Any]` parameter soup in hot paths** (polish-D.1
   typed `view_ingress`, but `RoundResult` / `CandidateResult` /
   `PipelineParams` payloads remain). M-sized refactor, own arc.
2. **Test charter violations** — substring assertions on rendered
   text, stub-forest regression tests, tests for trivial wrappers.
   The charter caps the suite at ≤200 collected tests; currently
   199.
3. **Stale `Field(description=...)` strings on LLM-facing schemas** —
   load-bearing per [[feedback-field-description-load-bearing]] but
   some may have drifted from current behavior.
4. **INFO/WARN-level logging for events nobody actually surfaces** —
   log noise audit.

## M13+ intentional UI placeholders

UI affordances the product *intentionally* ships disabled today — they
preview the M13+ chat-first UX + config-edit surface + analytics-search
surface. They are **not** scaffolding, not credibility hits, and not in
scope for any "hide non-functional controls" sweep.

| Placeholder | File | Future surface |
|---|---|---|
| Topbar search input (disabled) | `webapp/components/shell/Topbar.tsx:29` | M13+ analytics search |
| ChatPane attach + textarea + send button (disabled) | `webapp/components/dashboard/ChatPane.tsx:273-279` | M13+ chat-first operator UX |
| ChatPane Extended-thinking / Web-search / Code-execution toggles (`toggle locked`) | `webapp/components/dashboard/ChatPane.tsx:286-322` | M13+ chat-first feature toggles |
| ChatPane "job-footer" — "Adjust spend / finishing criteria — wired in M12" | `webapp/components/dashboard/ChatPane.tsx:204-206` | M12 control-plane (spend cap + finishing criteria editor) |
| ConfigMenu — gear icon + frozen-parameters panel | `webapp/components/dashboard/ConfigMenu.tsx` (+ render at `ChatPane.tsx:217`) | M12 control-plane (editable config surface) |
| AccountModal "Update profile" button (disabled) | `webapp/components/account/AccountModal.tsx:193-200` | M13+ profile-editing surface |
| AccountModal "Remove account" menu item (disabled) | `webapp/components/account/AccountModal.tsx:251-258` | M13+ multi-provider account management |
| AccountModal "+ Connect account" button (alerts then no-ops) | `webapp/components/account/AccountModal.tsx:267-278` | M13+ multi-provider account linking |

**Rule:** any future cleanup that touches these surfaces must
distinguish *intentional placeholder* from *scaffolding text/comment*.
Milestone-reference text inside these placeholders is OK (and exempts
them from a "no M-milestone references on operator surfaces" final-grep
gate); other operator surfaces still must not leak milestone numbers.

## History

All prior tiers + the pre-public-release polish arc (Tiers 0–6 + polish
A–E + audits 1–3) closed by 2026-05-25. Done-log entries lived here and
were pruned with the arc; recover via `git log` if needed.
