# Code-Debt Cleanup — Backlog

Dump location for new debt as it's found. Add a bullet under **Active
backlog** with enough detail that a future session can pick it up cold:
- file + line range (or symbol)
- one sentence on *why* it's debt (not "what" — the code shows what)
- proposed action (delete / inline / extract / replace / verify)
- any blockers (needs telemetry, needs a mini-spec, depends on another
  item)

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

- **`live_dashboard/round_summary.py` + `factory.py` consolidation
  revisit** —
  `infrastructure/projections/live_dashboard/round_summary.py` (57L)
  and `factory.py` (71L). Spared from audit-1.C because of
  deliberate seams (`round_summary` = the `dash.rounds[]` shape
  transform; `factory` = resume-state healing). Both seams have
  stabilized over subsequent arcs. **Action:** re-read each + their
  sole caller (`view.py`); decide whether still load-bearing or
  ready to inline. Not a forced yes — kept intentionally last time;
  new decision needs a stronger reason than "single caller."
  **Pattern:** single-caller indirection (revisit).

## Frontend modernization + perf opportunities

Lens: deliberate hand-roll or underused framework capability —
distinct from the vibe-coded-remainder lens of § Active backlog. Each
item is an independent win, listed cheapest-first. Discovered during
the `371c476b` dashboard-writer-coalesce arc; none touched in that
commit.

### Cheap (hours)

- **`CycleStreamContext.Provider` value not memoized** —
  `webapp/lib/poll.tsx:446`. The provider passes `state` (return of
  `useCycleStreamSource`), a new object on every poll tick — even on
  a 304-skipped poll. Every context consumer re-renders. `React.memo`
  on the four chart components catches the shallow-equal-props case
  but doesn't help components reading other fields. **Action:** wrap
  the value in `useMemo` keyed on its component fields; or split into
  two contexts — steady metadata (`campaignId`, `cycleId`, `unitKey`)
  vs polling payload (`dash`, `status`). Verify in React DevTools
  Profiler: chart subtree no longer re-renders on a 304-skipped tick.
  **Pattern:** unnecessary re-render fanout.

- **Audit `"use client"` for server-component candidates** —
  `webapp/components/**/*.tsx`. All 65 `.tsx` files declare
  `"use client"`. App Router's server components are entirely unused.
  Static-export still benefits: server-component code stays out of
  the client bundle. **Action:** identify leaves with no browser APIs
  (no `useState` / `useEffect` / `useRef` / event handlers /
  `window` / `document`) and remove the directive. Starter candidates:
  `components/ui/card.tsx`, `components/whatif/icons.tsx`,
  `components/dashboard/MeasHeatCell.tsx`,
  `components/whatif/FitnessRankSummary.tsx`,
  `components/ui/states.tsx`. Verify via bundle-size diff in `out/`.
  **Pattern:** underused framework capability.

### Medium (half-day to a day)

- **Enable React 19 Compiler** — `webapp/next.config.ts` +
  `webapp/eslint.config.mjs`. React 19 ships an auto-memoizing
  compiler (`babel-plugin-react-compiler` +
  `eslint-plugin-react-compiler`). Would replace most manual
  `React.memo` / `useMemo` / `useCallback`, including the
  `l1RoundsKey` fingerprint (`LineageTree.tsx:73`) and the 12
  `useMemo`s in `HardSamplesTable.tsx`. **Action:** add the plugin
  pair, enable in Next config (`experimental.reactCompiler = true`),
  run typecheck + build, then strip redundant manual memos in a
  follow-up. **Blocker:** verify the compiler is stable on Next
  16.2.5 (was RC at React 19 launch; check release notes before
  shipping). **Pattern:** underused framework capability.

- **SSE replaces 2 s polling** —
  `promptpotter/presentation/api/routers/campaigns/cycles.py` +
  `webapp/lib/poll.tsx`. Backend pushes a tick event when
  `CycleEventLog` advances; client refetches `dashboard.json` only on
  tick. Eliminates 2 s 304 chatter and shortens time-to-update.
  FastAPI: `EventSourceResponse` via `sse-starlette`. **Action:** add
  `GET /api/campaigns/{id}/cycles/{cid}/events` returning an SSE
  stream of `{kind: "tick", last_modified: ...}`; client opens
  `EventSource` per `unitKey`, refetches dashboard on each event;
  fall back to interval polling if EventSource fails. **Blocker:**
  confirm static-export behind FastAPI doesn't buffer the SSE
  channel. **Pattern:** push-vs-poll modernization.

### Larger (multi-day)

- **Migrate `lib/poll.tsx` to SWR or TanStack Query** —
  `webapp/lib/{poll.tsx,useFetch.ts,usePoll.ts}`. Both libraries
  handle conditional polling, dedup, focus revalidation, exponential
  backoff, and 304 plumbing for free. The hand-roll predates both
  being viable for this shape. **Action:** SWR is the closer fit for
  read-only polling. Migration deletes most of `poll.tsx`, all of
  `useFetch.ts`, all of `usePoll.ts`. **Blocker:** verify SWR
  revalidation interacts cleanly with the render-phase reset on
  `unitKey` change. Pairs naturally with the SSE migration (drop
  interval once the SWR mutator is wired to SSE ticks).
  **Pattern:** hand-roll → library swap.

- **Virtualize `LineageTree` + `HardSamplesTable`** —
  `webapp/components/dashboard/{LineageTree.tsx,HardSamplesTable.tsx}`.
  Render whole list/tree today; fine at current sizes (tens to
  low-hundreds of rows). Will degrade at BBEH-scale or after long
  campaigns. **Action:** `react-window` (fixed-height rows) or
  `@tanstack/react-virtual` (flexible). **Blocker:** profile first —
  premature below ~300 rows. **Pattern:** preempt-before-scale.

- **Move `LineageTree` layout to a Web Worker** —
  `webapp/components/dashboard/LineageTree.tsx`. The sort +
  segment-mutation walk is O(N) per layout. Currently fine after the
  `l1RoundsKey` fingerprint shipped. If profiler later shows the
  layout step blocking the main thread on a large tree, move into a
  Worker via `Comlink` or vanilla `postMessage`. **Blocker:** profile
  first. **Pattern:** preempt-before-scale.

### Backend perf (parked, verify-first)

- **Dashboard route re-parses on every non-304 hit** —
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

**Rule:** any future cleanup that touches these surfaces must
distinguish *intentional placeholder* from *scaffolding text/comment*.
Milestone-reference text inside these placeholders is OK (and exempts
them from a "no M-milestone references on operator surfaces" final-grep
gate); other operator surfaces still must not leak milestone numbers.

## History

All prior tiers + the pre-public-release polish arc (Tiers 0–6 + polish
A–E + audits 1–3) closed by 2026-05-25. Done-log entries lived here and
were pruned with the arc; recover via `git log` if needed.
