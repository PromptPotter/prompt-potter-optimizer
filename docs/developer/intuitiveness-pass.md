# Intuitiveness Pass — refactor plan (Track A complete)

> **Not a LOC pass.** Net-minus lines may happen; they are a side effect, not the goal.
> The goal is **short discovery tours**: any question a reader (human, AI, or a `grep`)
> asks about the code should reach its conclusion in the fewest hops.

## Why this plan exists

We went on three wrong hunts (line count → split → dead-code detection). Each chased a
*countable* proxy for a property that isn't countable: whether the code is **intuitive**.
The codebase is metrics-clean (no dead code, no duplication, lean LOC) and still not
intuitive, because intuitiveness is *felt by a reader*, not measured by a tool.

This pass enforces a rule we already wrote but only ever applied to new PRs — the
**Pre-flight gate Q3/Q4** (`CLAUDE.md`) — against the *existing* code.

## The metric: discovery-tour length

A **discovery tour** is the sequence of file-opens needed to answer one question about the
code. The intuitiveness defect is a *long* tour. Target: every common question answerable
in **≤1 hop** (the symbol, its callers, and its meaning visible where you land).

Common tour-starting questions to test a spot against:
- "Who calls this / is this used?" — answerable by grepping the name?
- "Where is X written / decided?" — one obvious owner, or scattered?
- "What does this name mean?" — clear in isolation, or needs another file?
- "Is this live or dead?" — provable without tracing dynamic dispatch?

## Calibration case (DONE — it is the template)

**`FileSink` ← `ObservabilityBridge._DISPATCH` → `getattr(sink, method)(event)`**
(`infrastructure/tracing/bridge.py`, `file_sink.py`).

**The tour, before.** *"Is `FileSink.on_node_start` live?"*
→ open `file_sink.py` (looks orphaned) → grep the method (only a test) → open `bridge.py`
→ read `_DISPATCH` string map → find `getattr(sink, method)` → find `observed_node()`
→ grep its ~10 call sites. **Four-plus hops to a yes/no.** `vulture` walked the same dead
end and gave a false "unused."

**Root cause.** String-keyed dynamic dispatch. `_DISPATCH: dict[type[Event], str]` mapped
each event class to a *method-name string*, and `emit()` called `getattr(sink, method)(event)`.
The caller→method edge was a string, invisible to `grep` and to static tools, so "find
references" — the cheapest discovery tour there is — returned nothing.

**The fix shipped.** Deleted `_DISPATCH`; `emit()` is now an explicit `match event:` whose
arms call each sink handler by its literal bound-method reference (`self._file.on_node_start`,
`lf.on_node_start if lf else None`). `grep on_node_start` now lands on the call site in **one
hop**. The three sinks (`FileSink` / `LangfuseSink` / `MLflowSink`) implement deliberately
different *subsets* of the 13 handlers — that subset is now encoded by *which arms list a
sink*, not by a silent `getattr(..., None)` skip. A one-line generic `_fan` helper preserves
the per-sink `graceful()` isolation while keeping every method reference literal at the call
site. A `Protocol` was rejected: it would either lie (declare 13 methods two sinks lack) or
need three Protocols, and it still wouldn't make the *call edge* greppable — which is the only
metric that matters here. `file_sink.py::_WRITE_POINT_FIELDS` was **left as-is**: it is
string-keyed *data* lookup (reads event attributes), not a hidden call edge.

### The two collapse templates (the detector for the sweep)

The calibration yields the rule the rest of the sweep applies. Caller-hiding dispatch splits
into two cases with two different fixes:

- **Template `match`** — when the dispatch key is *not* a cross-file contract (a closed,
  internal type→behavior map), delete the lookup table and write an explicit `match` on the
  type with direct named calls. The call edge becomes greppable; mypy narrows each arm.
  *(bridge `emit`, scoring `_DIAG_DISPATCH`, view `_TEXT_RENDERERS`.)*
- **Template `@register`** — when the *string key itself is a contract* other files depend on
  (prompt templates, domain enums), keep the keyed dict but move registration to a decorator
  at the handler's definition site, so key↔handler are co-located and grep-from-key lands on
  the handler body in one hop. *(the `INJECTIONS` registry.)*

And the boundary the calibration draws: **string-keyed *call dispatch* is the defect;
string-keyed *data* tables are fine.** An enum-keyed dict guarded by an import-time
completeness assert (`resume_and_fork/replayers.py::REPLAYERS`) is already an acceptable
third pattern — it is the reference exemplar, not a defect.

## The sweep — results

The hunt covered the four patterns (caller-hiding dispatch, Q4 names, one-concept-two-names,
N-hop facts), hottest paths first. Caller-hiding dispatch dominated: every defect found was a
type/string → behavior table that hid the call edge from `grep`. Ranked by how often the tour
gets walked. **All rows shipped** (one commit each); fix column names the template from above.

| Spot | Question a reader asks | Hops before | Root fix | Hops after |
|---|---|---|---|---|
| `dispatch/hub/injections/registry.py::INJECTIONS` | "How is `axis_memory` (any slot) rendered?" — every optimizer LLM call | ~5 | `@signal` co-locates key+renderer at the def site (**`@register`**) | 1 |
| `tracing/bridge.py::emit` (calibration) | "Is `FileSink.on_node_start` live? / who handles event X?" — every traced event | 4–5 | explicit `match event` → literal sink calls (**`match`**) | 1 |
| `scoring/metrics.py::_DIAG_DISPATCH` | "What diagnostics does a `RANKER` node produce?" — every sample | 2–3 | `match node.node_type` in `_extract_node_diagnostics` (**`match`**) | 1 |
| `domain/opt_search_point.py::EVIDENCE_GROUNDING_FIELDS` | "Is `escalation_panel` a slot? where is it rendered?" — L1 round-trace review | 4 (dead-end) | signpost the 3 kinds (injection-backed / L1-surface panel / sentinel) — **see note** | 1 |
| `views/render/text.py::_TEXT_RENDERERS` | "Which renderer formats `RoundCompleteView`?" — display work | 2 | `match view` in `to_text` (**`match`**) | 1 |
| `resume_and_fork/replayers.py::REPLAYERS` | "Which replayer handles `ELIMINATION_CUT`?" — resume/fork only | 2 | type the lookup key as `ResumeCheckpointKind` — reference exemplar, tighten not rewrite | 1–2 |

**Note (`escalation_panel`).** This row entered the sweep as a suspected *dangling* injection
(a name in `EVIDENCE_GROUNDING_FIELDS` with no `INJECTIONS` slot). That was a **false positive**:
`EVIDENCE_GROUNDING_FIELDS` is the set of *citeable panel names*, not the slot set — `parent_panel`
and `sibling_yield` are equally non-slots by design, and `escalation_panel.exploration_budget`
gates the `stall_exploration` escape hatch (`validators/l1_behavior.py`). The defect was real but
was *discovery confusion*, not a dead name; the fix is a signpost grouping the three kinds, not a
removal. Recorded here so the slot-set-vs-citation-set conflation isn't re-hunted.

`markdown.py::to_markdown` was checked and **excluded** — it's a single-type renderer, not a
type-dispatch table, so it hides no call edge.

## Explicitly out of scope

- Splitting big-but-coherent files (`view.py`, `command_dispatcher.py`, `AccountModal.tsx`).
  Reorganizing without shortening a tour is the worthless move we already rejected.
- Touching tests for size (your test-complexity ceiling).
- Trimming docs / pruning data JSON.
