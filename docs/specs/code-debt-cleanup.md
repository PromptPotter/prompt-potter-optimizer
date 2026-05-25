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

- **`L1Variant.target_axis` + `L1Variant.reasoning` dead fields** —
  `application/optimization/dispatch/schemas.py:172-173`. Declared as
  LLM-side "reasoning aids" in the docstring (lines 139-144); grep
  across 234 .py files returns zero reads of `.target_axis` /
  `.reasoning`. Pure token-cost on every L1-generate response +
  audit-trail clutter in `round_NNNN.json` for zero runtime benefit.
  **Action:** delete both fields from `L1Variant`, drop the docstring
  paragraph, confirm L1 prompts no longer ask the LLM to emit them
  (`datasets/_optimizer/pipeline.json::l1_generate/*`). **Pattern:**
  speculative API surface.

- **`ForkTrigger` dead variants + the `NotImplementedError` branch** —
  `infrastructure/store/fork_siblings.py:224` +
  `domain/run_records.py:187-195`. The if-elif handles 3 of 6 enum
  variants (`SCORING_DIVERGENCE`, `OPERATOR_DIAG`, `OPERATOR_SWEEP`);
  the other three (`L2_REBASE`, `L3_REBASE`, `OPERATOR_REWIND`) fall
  through to `raise NotImplementedError(f"ForkTrigger.{name} not wired")`.
  Per `promptpotter/CLAUDE.md` § L3 fork-proposal, L3's fork-proposal
  is observation-only — operators run `resume --from N` manually, so
  `L2_REBASE`/`L3_REBASE` are speculative auto-fork scaffolding for a
  feature that shipped as observation. **Action:** delete the three
  unwired variants from `ForkTrigger` + the `NotImplementedError`
  else branch (provably unreachable after the enum prune). If MCTS
  auto-forking ever lands per [[architecture-l4-is-recursion]],
  re-add with real wiring. **Pattern:** vibe-coded scaffolding.

- **`AxisIndex._cache_axis_impacts` cache** —
  `application/intelligence/indexes/axis.py:95, 164-166, 336-348`.
  Memoizes `AxisImpact` (effect-size + classification) per axis on a
  per-cycle `AxisIndex` instance; hit ~2-5 times per cycle on
  sub-millisecond work. The cache field + lookup guard +
  invalidation in `_recompute_failure_group_correlations` cost more
  reading-weight than the savings buy back. **Action:** delete the
  cache field + guard + invalidation; rely on direct computation.
  Verify: standard CI + manual smoke through a fresh
  `new <dataset>`. **Pattern:** premature optimization with
  apologetic docstring.

- **`SampleIndex._cache_records` cache — verify first** —
  `application/intelligence/indexes/sample.py:58, 123, 186-207`.
  Memoizes the `records()` list between digest calls. The real cost
  inside `records()` is the per-sample `_dominant_failure_mode`
  (Counter aggregation), which the list cache doesn't help with.
  Suspected savings: ~2 list rebuilds per cycle (cheap).
  **Blocker:** instrument hit count + measure recompute cost on a
  live campaign before acting. If hit-count × per-call-cost <
  1ms/cycle → delete the cache; if the Counter is the real
  bottleneck → memoize `_dominant_failure_mode` instead.
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
tracing call sites + reading the body. **Precedent (this
backlog):** `L1Variant.target_axis` + `.reasoning` — LLM emits them,
no code reads them.

### Pattern: vibe-coded scaffolding
Half-finished branches behind `raise NotImplementedError`, enum
variants promising dynamism the system never delivers, comments
referring to future work the project doesn't plan to build. The
root `CLAUDE.md` is explicit: "Document current state, not
half-done plans." Verify the "future" actually isn't on the
roadmap. **Precedent (this backlog):** `ForkTrigger.L2_REBASE` /
`L3_REBASE` / `OPERATOR_REWIND` + the unreachable
`NotImplementedError` branch.

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
